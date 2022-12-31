# REFACTORING: build a new class which manages all the functionality listed below

from googleapi import OBSGoogleSheets, LangConfig, SheetConfig
from deployment import Spawner
from typing import List, Dict
from models import MinionSettings
from pydantic import BaseModel
from util.util import ExecutionStatus, WebsocketResponse
import socketio

instance_service_addrs = util.ServiceAddrStorage()  # dict of `"lang": {"addr": "address"}
langs: list[str] = []
server_state = ServerState(ServerState.SLEEPING)
# init_status, wakeup_status = False, False
media_scheduler = MediaScheduler()
sheets = OBSGoogleSheets()
timing_sheets = TimingGoogleSheets()
vmix_selector = SourceSelector()
minions = Minions()

MINION_WS_PORT = 6006


class Skipper:
    class Registry(BaseModel):
        minion_configs: Dict[str, MinionSettings] = {}  # lang: config

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
        def __init__(self, json_dump=None):
            self.spawner = Spawner()
            if json_dump is not None:
                self.from_json(json_dump=json_dump)

        def activate_registry(self):
            # TODO: create instances, create Minion instances for the Skipper
            pass

        @classmethod
        def from_json(cls, json_dump):
            pass

        def json(self):
            pass

    class OBSConfig:
        def __init__(self, skipper):
            self.obs_sheets = OBSGoogleSheets()
            self.skipper = skipper

        def setup(self, sheet_url, sheet_name) -> ExecutionStatus:
            try:
                if not self.obs_sheets.setup_status:
                    self.obs_sheets.set_sheet(sheet_url, sheet_name)
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Couldn't set up obs sheet config.\nDetails: {ex}")

        def pull(self, langs: List[str] = None):
            minion_configs: Dict[str, MinionSettings] = self.obs_sheets.pull()

            if langs is not None:  # if need to pull only specified langs
                minion_configs = {lang: minion_configs[lang] for lang in minion_configs if lang in langs}
            else:
                for lang in self.skipper.registry.list_langs():  # list langs in registry
                    if lang not in minion_configs:  # if lang has been deleted in google sheets
                        self.skipper.registry.delete_minion(lang)

            for lang in minion_configs:
                self.skipper.registry.update_minion(lang, minion_configs[lang])

            self.skipper.activate_registry()

        def push(self):
            pass

    class Minion:
        def __init__(self, minion_ws_addr, lang):
            self.minion_ws_addr = minion_ws_addr
            self.lang = lang
            self.sio = socketio.Client()
            self.connect()

        def __del__(self):
            self.close()

        def connect(self):
            self.sio.connect(self.minion_ws_addr)

        def close(self):
            self.sio.disconnect()

        def apply_config(self, minion_config: MinionSettings) -> WebsocketResponse:
            response = WebsocketResponse()
            self.sio.emit("#TODO", data=minion_config.json(), callback=response.callback)
            return response

    def __init__(self):
        self.registry: Skipper.Registry = Skipper.Registry()
        self.infrastructure: Skipper.Infrastructure = Skipper.Infrastructure()
        self.obs_config: Skipper.OBSConfig = Skipper.OBSConfig(self)
        self.minions: Dict[str, Skipper.Minion] = {}

    def activate_registry(self) -> ExecutionStatus:
        self.infrastructure.activate_registry()

        configs_to_activate = [
            [lang, minion_config]
            for lang, minion_config in self.registry.minion_configs.items()
            if not minion_config.active()
        ]
        responses = [
            [lang, self.minions[lang].apply_config(minion_config=minion_config)]
            for lang, minion_config in configs_to_activate
        ]
        WebsocketResponse.wait_for(responses=[r for _, r in responses])

        # TODO: handle responses

        return ExecutionStatus(True)


    def save_to_disk(self):
        pass

    def load_from_disk(self):
        pass
