from pydantic import BaseModel, PrivateAttr
from pydantic.schema import Optional
from typing import List, Dict
from datetime import datetime
from threading import Lock, RLock, Thread


class OBSCloudModel(BaseModel):
    _objvers: str = PrivateAttr("")

    def __init__(self, *args, **kwargs):
        super(OBSCloudModel, self).__init__(*args, **kwargs)

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
        value: Optional[int] = 6800

    class TSVolume(OBSCloudModel):
        value: Optional[float] = 0.0

    class SourceVolume(OBSCloudModel):
        value: Optional[float] = 0.0

    class SidechainSettings(OBSCloudModel):
        ratio: Optional[float] = 32.0
        release_time: Optional[int] = 1000
        threshold: Optional[float] = -30.0
        output_gain: Optional[float] = -10.0

    class TransitionSettings(OBSCloudModel):
        transition_name: Optional[str] = "Cut"
        path: Optional[str] = ""
        transition_point: Optional[int] = 6500

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
    sidechain_settings: SidechainSettings = SidechainSettings()
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


class State:
    sleeping = "sleeping"
    initializing = "initializing"
    running = "running"
    disposing = "disposing"
    error = "error"


class Registry(BaseModel):
    obs_sheet_url: str = None
    obs_sheet_name: str = None
    minion_configs: Dict[str, MinionSettings] = {}  # lang: config
    infrastructure_lock: bool = False  # points if needed to lock infrastructure

    server_status: str = State.sleeping
    _last_server_status: str = PrivateAttr(State.sleeping)

    timing_sheet_url: str = None
    timing_sheet_name: str = None
    timing_list = []
    timing_start_time: datetime = None  # system time when the timing will be/was started

    # ip: {"name": "", active: True/False}
    vmix_players: Dict[str, VmixPlayer] = {"*": VmixPlayer(name="All", active=True)}
    active_vmix_player: str = "*"  # "*" or ip

    _lock = PrivateAttr()

    def __init__(self, **kwargs):
        self._lock = RLock()
        super(Registry, self).__init__(**kwargs)

    def __getattr__(self, item):
        if item == "_lock":
            return super(Registry, self).__getattr__(item)
        with self._lock:
            return super(Registry, self).__getattr__(item)

    def __setattr__(self, key, value):
        if key == "_lock":
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
            self.minion_configs[lang] = minion_config
        else:
            self.minion_configs[lang].modify_from(minion_config)

    def delete_minion(self, lang):
        if lang in self.minion_configs:
            self.minion_configs.pop(lang)

    def revert_server_state(self):
        with self._lock:
            super().__setattr__("server_status", self._last_server_status)