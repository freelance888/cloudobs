# REFACTORING: build a new class which manages all the functionality listed below
import json
import os

from googleapi import OBSGoogleSheets
from deployment import Spawner
from typing import List, Dict
from models import MinionSettings
from pydantic import BaseModel
from util import ExecutionStatus, WebsocketResponse
import socketio
from socketio.exceptions import ConnectionError, ConnectionRefusedError

# instance_service_addrs = util.ServiceAddrStorage()  # dict of `"lang": {"addr": "address"}
# langs: list[str] = []
# server_state = ServerState(ServerState.SLEEPING)
# # init_status, wakeup_status = False, False
# media_scheduler = MediaScheduler()
# sheets = OBSGoogleSheets()
# timing_sheets = TimingGoogleSheets()

MINION_WS_PORT = 6006


class Skipper:
    class Registry(BaseModel):
        minion_configs: Dict[str, MinionSettings] = {}  # lang: config
        vmix_sources: Dict[str, Dict] = {}  # ip: {"name": ""}
        active_vmix_source: str = "*"  # "*" or ip

        def list_langs(self):
            return list(self.minion_configs.keys())

        def update_minion(self, lang, minion_config: MinionSettings):
            if lang not in self.minion_configs:
                self.minion_configs[lang] = minion_config
            else:
                self.minion_configs[lang].modify_from(minion_config)

        def delete_minion(self, lang):
            pass

    class Infrastructure:
        def __init__(self, skipper):
            self.spawner: Spawner = Spawner()
            self.skipper = skipper

        def activate_registry(self) -> ExecutionStatus:
            try:
                self.spawner.ensure_langs(self.skipper.registry.list_langs(),
                                          wait_for_provision=True)  # [... [lang, ip], ...]
            except Exception as ex:
                # TODO: log
                return ExecutionStatus(False, message=f"Something happened while deploying minions: {ex}")

            # lang_ips = self.spawner.ip_dict.ip_list()
            for lang, ip in self.spawner.ip_dict.ip_list():
                try:
                    self.skipper.registry.minion_configs[lang].addr_config.minion_server_addr = ip
                    # if Minion instance have not been created yet
                    if lang not in self.skipper.minions:
                        self.skipper.minions[lang] = Skipper.Minion(minion_ip=ip, lang=lang)  # create
                    # else if lang or ip has changed
                    if self.skipper.minions[lang].minion_ip != ip or self.skipper.minions[lang].lang != lang:
                        del self.skipper.minions[lang]  # delete old version
                        # and replace with updated one
                        self.skipper.minions[lang] = Skipper.Minion(minion_ip=ip, lang=lang)
                except ConnectionError as ex:
                    return ExecutionStatus(False, f"Something happened while creating Minion instances. Details: {ex}")

            return ExecutionStatus(True)

        @classmethod
        def from_json(cls, skipper, json_dump):
            infra = Skipper.Infrastructure(skipper)
            infra.spawner = Spawner.from_json(json_dump=json_dump)
            return infra

        def json(self):
            return self.spawner.json()

    class OBSSheets:
        def __init__(self, skipper):
            self.obs_sheets = OBSGoogleSheets()
            self.skipper = skipper

        def setup(self, sheet_url, sheet_name) -> ExecutionStatus:
            try:
                self.obs_sheets.set_sheet(sheet_url, sheet_name)
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Couldn't set up obs sheet config.\nDetails: {ex}")

        def pull(self, langs: List[str] = None) -> ExecutionStatus:
            try:
                if not self.obs_sheets.setup_status:
                    return ExecutionStatus(False, f"setup_status of obs_sheets is False")

                minion_configs: Dict[str, MinionSettings] = self.obs_sheets.pull()

                if langs is not None:  # if need to pull only specified langs
                    minion_configs = {lang: minion_configs[lang] for lang in minion_configs if lang in langs}
                else:
                    for lang in self.skipper.registry.list_langs():  # list langs in registry
                        if lang not in minion_configs:  # if lang has been deleted in google sheets
                            self.skipper.registry.delete_minion(lang)

                for lang in minion_configs:
                    self.skipper.registry.update_minion(lang, minion_configs[lang])
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, str(ex))

        def push(self) -> ExecutionStatus:
            try:
                if not self.obs_sheets.setup_status:
                    return ExecutionStatus(False, f"setup_status of obs_sheets is False")
                self.obs_sheets.push(self.skipper.registry.minion_configs)
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while pushing obs_config info. Details: {ex}")

    class Minion:
        def __init__(self, minion_ip, lang, ws_port=MINION_WS_PORT):
            self.minion_ip = minion_ip
            self.ws_port = ws_port
            self.lang = lang
            self.sio = socketio.Client()
            self.connect()

        def __del__(self):
            self.close()

        def connect(self):
            try:
                self.sio.connect(f"http://{self.minion_ip}:{self.ws_port}")
            except Exception as ex:
                # TODO: log
                raise ConnectionError(f"Connection error for ip {self.minion_ip} lang {self.lang}. "
                                      f"Details:\n{ex}")

        def close(self):
            self.sio.disconnect()

        def apply_config(self, minion_config: MinionSettings) -> WebsocketResponse:
            response = WebsocketResponse()
            # TODO: command
            self.sio.emit("command",
                          data=json.dumps({"command": "set info", "details": {"info": minion_config.json()}}),
                          callback=response.callback)
            return response

        def json(self):
            return json.dumps({"minion_ip": self.minion_ip, "ws_port": self.ws_port, "lang": self.lang})

        @classmethod
        def from_json(cls, json_dump):
            data = json.loads(json_dump)
            return Skipper.Minion(minion_ip=data["minion_ip"], ws_port=data["ws_port"], lang=data["lang"])

    def __init__(self):
        self.registry: Skipper.Registry = Skipper.Registry()
        self.infrastructure: Skipper.Infrastructure = Skipper.Infrastructure(self)
        self.obs_config: Skipper.OBSSheets = Skipper.OBSSheets(self)
        self.minions: Dict[str, Skipper.Minion] = {}

    def pull_obs_config(self, sheet_url=None, sheet_name=None, langs=None) -> ExecutionStatus:
        """
        Pulls and applies OBS configuration from google sheets
        :param sheet_url: sheet url. No need to specify if it has been specified once before (cache)
        :param sheet_name: works same as sheet_url
        :param langs: if specified, takes only specified langs from google sheets
        :return: ExecutionStatus
        """
        if sheet_url and sheet_name:
            status: ExecutionStatus = self.obs_config.setup(sheet_url, sheet_name)
            if not status:
                return status

        status: ExecutionStatus = self.obs_config.pull(langs=langs)
        if not status:
            return status

        return self.activate_registry()

    def push_obs_config(self) -> ExecutionStatus:
        return self.obs_config.push()

    def activate_registry(self) -> ExecutionStatus:
        # check if there are all minions deployed
        status: ExecutionStatus = self.infrastructure.activate_registry()
        if not status:
            return status

        try:
            # select only those configs which have been changed (not active)
            configs_to_activate = [
                [lang, minion_config]
                for lang, minion_config in self.registry.minion_configs.items()
                if not minion_config.active()
            ]
            # collect websocket responses
            responses = [
                [lang, self.minions[lang].apply_config(minion_config=minion_config)]
                for lang, minion_config in configs_to_activate
                if lang in self.minions
            ]
            # wait until websocket callback or timeout
            WebsocketResponse.wait_for(responses=[r for _, r in responses])

            for lang, response in responses:
                if response.result() is "#TODO something":  # TODO: check if status is done
                    self.registry.minion_configs[lang].activate()
        except Exception as ex:
            return ExecutionStatus(False, f"Something happened while activating skipper's registry. Details: {ex}")

        return ExecutionStatus(True)

    def save_to_disk(self):
        registry_json = self.registry.json()
        infra_json = self.infrastructure.json()
        minions_json = json.dumps({lang: minion.json() for lang, minion in self.minions})

        with open("./dump_registry.json", "wt") as fp:
            fp.write(registry_json)
        with open("./dump_infra.json", "wt") as fp:
            fp.write(infra_json)
        with open("./dump_minions.json", "wt") as fp:
            fp.write(minions_json)

    def load_from_disk(self):
        # Load registry
        self.registry = Skipper.Registry()
        if os.path.isfile("./dump_registry.json"):
            with open("./dump_registry.json", "rt") as fp:
                content = fp.read()
                if content:
                    self.registry = Skipper.Registry.parse_raw(content)
        # Load infrastructure
        self.infrastructure = Skipper.Infrastructure(self)
        if os.path.isfile("./dump_infra.json"):
            with open("dump_infra.json", "rt") as fp:
                content = fp.read()
                if content:
                    self.infrastructure = Skipper.Infrastructure.from_json(self, content)
        # Load minions
        del self.minions
        self.minions = {}
        if os.path.isfile("./dump_minions.json"):
            with open("dump_minions.json", "rt") as fp:
                content = fp.read()
                if content:
                    self.minions = {
                        lang: Skipper.Minion.from_json(minion_json)
                        for lang, minion_json in json.loads(fp.read()).items()
                    }

        self.activate_registry()
