import os
from datetime import datetime, timedelta
from threading import RLock
from typing import List, Dict

from pydantic import BaseModel, PrivateAttr
from pydantic.schema import Optional
import orjson

from util import hash_passwd

passwd_placeholder = "●●●●●●●●"


def orjson_dumps(v, *, default=None):
    # orjson.dumps returns bytes, to match standard json.dumps we need to decode
    if default is None:
        default = Registry.__json_encoder__
    return orjson.dumps(v, default=default).decode()


class User:
    """
    login: Just a user unique name
    passwd: User plain text password (won't be empty only when password changes)
    passwd_hash: Hashed password (will be used instead of plain text password for checks)
    permissions: A list of user permissions ('admin' - full access, 'en' - specific lang access)
    """

    def __init__(self, login: str, passwd: str, passwd_hash: str, permissions: list[str]):
        self.login = login
        self.passwd = passwd
        self.passwd_hash = passwd_hash
        self.permissions = permissions

    def __repr__(self):
        return f"User<{self.login}:{'+'.join(self.permissions)}>"

    @staticmethod
    def master():
        master_login = os.getenv("MASTER_LOGIN")
        master_passwd = os.getenv("MASTER_PASSWD")
        return User(
            login=master_login,
            passwd=passwd_placeholder,
            passwd_hash=hash_passwd(master_passwd),
            permissions=["admin"],
        )

    @staticmethod
    def vmix_player():
        master_login = "vmix player"
        master_passwd = "vmix player"
        return User(
            login=master_login,
            passwd=passwd_placeholder,
            passwd_hash=hash_passwd(master_passwd),
            permissions=[],
        )

    def is_admin(self) -> bool:
        return "admin" in self.permissions if self and self.permissions else False

    def langs(self):
        if self.permissions:
            if "admin" in self.permissions or "*" in self.permissions:
                return ["*"]
            return self.permissions
        return ["*"]


class SessionContext:
    def __init__(self, **kwargs):
        self.sid: str = kwargs["sid"] if "sid" in kwargs else None
        self.ip: str = kwargs["ip"] if "ip" in kwargs else None
        self.user: User = kwargs["user"] if "user" in kwargs else None


class OBSCloudModel(BaseModel):
    _objvers: str = PrivateAttr("")

    def __init__(self, **kwargs):
        super(OBSCloudModel, self).__init__(**kwargs)

    def __setattr__(self, key, value):
        if hasattr(self, key) and self.__getattribute__(key) != value:
            super().__setattr__("_objvers", "M")
        super().__setattr__(key, value)

    def objvers(self) -> str:
        return self._objvers

    def is_active(self) -> bool:
        return self.objvers() == "A"

    def is_modified(self) -> bool:
        return self.objvers() == "M"

    def activate(self):
        self._objvers = "A"

    def modify(self):
        self._objvers = "M"

    def deactivate(self):
        self._objvers = ""

    def keys(self) -> List[str]:
        return list(self.dict().keys())

    def set(self, key, value):
        self.__setattr__(key, value)


class MinionSettings(BaseModel):
    class AddrConfig(OBSCloudModel):
        obs_host: Optional[str] = "localhost"
        minion_server_addr: Optional[str]  # host_url
        websocket_port: Optional[int] = 4439
        password: Optional[str] = ""
        original_media_url: Optional[str] = ""

    class StreamSettings(OBSCloudModel):
        server: Optional[str] = ""
        key: Optional[str] = ""

    class StreamOn(OBSCloudModel):
        value: Optional[bool] = False

    class TSOffset(OBSCloudModel):
        value: Optional[int] = 9000

    class TSVolume(OBSCloudModel):
        value: Optional[float] = 0.0

    class VmixSpeakerBackgroundVolume(OBSCloudModel):
        value: Optional[float] = -30.0

    class SourceVolume(OBSCloudModel):
        value: Optional[float] = 0.0

    class SidechainSettings(OBSCloudModel):
        enabled: bool = True
        ratio: Optional[float] = 32.0
        release_time: Optional[int] = 1000
        threshold: Optional[float] = -30.0
        output_gain: Optional[float] = -10.0

    class LimiterSettings(OBSCloudModel):
        enabled: bool = True
        threshold: Optional[float] = -6.0
        release_time: Optional[int] = 60

    class GainSettings(OBSCloudModel):
        enabled: bool = True
        gain: Optional[float] = 1.0

    class TransitionSettings(OBSCloudModel):
        transition_name: Optional[str] = "Cut"
        path: Optional[str] = ""
        transition_point: Optional[int] = 7500

    class GDriveSettings(OBSCloudModel):
        folder_id: Optional[str] = ""
        media_dir: Optional[str] = ""
        api_key: Optional[str] = ""
        sync_seconds: Optional[float] = 0.0
        gdrive_sync_addr: Optional[str] = ""

    addr_config: AddrConfig
    stream_settings: StreamSettings = StreamSettings()
    stream_on: StreamOn = StreamOn()
    ts_offset: TSOffset = TSOffset()
    ts_volume: TSVolume = TSVolume()
    source_volume: SourceVolume = SourceVolume()
    vmix_speaker_background_volume: VmixSpeakerBackgroundVolume = VmixSpeakerBackgroundVolume()
    sidechain_settings: SidechainSettings = SidechainSettings()
    ts_limiter_settings: LimiterSettings = LimiterSettings()
    ts_gain_settings: GainSettings = GainSettings()
    transition_settings: TransitionSettings = TransitionSettings()
    gdrive_settings: GDriveSettings = GDriveSettings()

    @classmethod
    def get_none(cls):
        t = MinionSettings(addr_config={"minion_server_addr": ""})

        for subject_name in t.list_subjects():
            subject = t.get_subject(subject_name)
            for k in subject.keys():
                subject.set(k, None)
        return t

    @classmethod
    def default(cls, minion_server_addr="localhost"):
        return MinionSettings(addr_config={"minion_server_addr": minion_server_addr})

    def modify_from(self, other):
        d = other.dict()
        for subject in d.keys():
            my_subject = self.__getattribute__(subject)  # get self.subject instance
            other_subject_dict = d[subject]  # get other.subject instance dict
            for key, value in other_subject_dict.items():  # for every key, value pair
                if value is not None:  # None - means the option is disabled
                    my_subject.__setattr__(key, value)  # copy from other subject

    def list_subjects(self):
        return [x for x in self.dict().keys()]

    def get_subject(self, subject) -> OBSCloudModel:
        return self.__getattribute__(subject)

    def get_subject_dict(self, subject):
        return self.dict()[subject]

    def active(self):
        return all([self.__getattribute__(subject).objvers() == "A" for subject in self.list_subjects()])

    def activate(self):
        for subject in self.list_subjects():
            self.__getattribute__(subject).activate()


class VmixPlayer(BaseModel):
    name: str
    active: bool = False


class TimingEntry(BaseModel):
    name: str
    timestamp: timedelta  # format of hh:mm:ss
    is_enabled: bool = True
    is_played: bool = False

    class Config:
        json_loads = orjson.loads
        json_dumps = orjson_dumps


class State:
    sleeping = "sleeping"
    initializing = "initializing"
    running = "running"
    disposing = "disposing"
    error = "error"


class Registry(BaseModel):
    obs_sheet_url: str = None
    obs_sheet_name: str = None
    users_sheet_url: str = None
    users_sheet_name: str = None
    minion_configs: Dict[str, MinionSettings] = {}  # lang: config
    infrastructure_lock: bool = False  # points if needed to lock infrastructure

    server_status: str = State.sleeping
    _last_server_status: str = PrivateAttr(State.sleeping)

    timing_sheet_url: str = None
    timing_sheet_name: str = None
    timing_list: List[TimingEntry] = []  # List of TimingEntry
    timing_start_time: datetime = None  # system time when the timing will be/was started

    # ip: {"name": "", active: True/False}
    vmix_players: Dict[str, VmixPlayer] = {"*": VmixPlayer(name="ALL", active=True)}
    active_vmix_player: str = "*"  # "*" or ip

    # {"lang1": {"filename1": True/False (downloaded), "filename2": ...}, "lang2": ...}
    gdrive_files: Dict[str, Dict] = {}

    _lock = PrivateAttr()

    class Config:
        json_loads = orjson.loads
        json_dumps = orjson_dumps

    def __init__(self, **kwargs):
        self._lock = RLock()
        super(Registry, self).__init__(**kwargs)

    def __getattr__(self, item):
        if item == "_lock":
            return super(Registry, self).__getattr__(item)
        with self._lock:
            return super(Registry, self).__getattr__(item)

    def __setattr__(self, key, value):
        if key in ("_lock",):
            super(Registry, self).__setattr__(key, value)
        else:
            with self._lock:
                if key == "server_status":
                    self._last_server_status = self.server_status
                super(Registry, self).__setattr__(key, value)

    def list_langs(self):
        return list(self.minion_configs.keys())

    def update_minion(self, lang, minion_config: MinionSettings):
        if lang not in self.minion_configs:
            self.minion_configs[lang] = MinionSettings.default()
        self.minion_configs[lang].modify_from(minion_config)

    def delete_minion(self, lang):
        if lang in self.minion_configs:
            self.minion_configs.pop(lang)

    def revert_server_state(self):
        with self._lock:
            super().__setattr__("server_status", self._last_server_status)

    def masked_copy(self):
        r = self.copy()
        r.vmix_players = {(f"hidden ip {i}" if ip != "*" else ip): vmix_player
                          for i, (ip, vmix_player) in enumerate(r.vmix_players.items())}

        active_vmix_players = [[ip, vmix_player] for ip, vmix_player in r.vmix_players.items() if vmix_player.active]
        if not active_vmix_players:
            r.active_vmix_player = "*"
        else:
            r.active_vmix_player = active_vmix_players[0][0]
        return r

    def masked_json(self):
        return self.masked_copy().json()

    def masked_dict(self):
        return self.masked_copy().dict()

    def get_ip_name(self, ip):
        if ip == "*":
            return ip
        if ip in self.vmix_players:
            return self.vmix_players[ip].name
        return ip
