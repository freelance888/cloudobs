# REFACTORING: build a new class which manages all the functionality listed below

from googleapi import OBSGoogleSheets, LangConfig, SheetConfig
from deployment import Spawner
from typing import List
from util.util import ExecutionStatus

instance_service_addrs = util.ServiceAddrStorage()  # dict of `"lang": {"addr": "address"}
langs: list[str] = []
server_state = ServerState(ServerState.SLEEPING)
# init_status, wakeup_status = False, False
media_scheduler = MediaScheduler()
sheets = OBSGoogleSheets()
timing_sheets = TimingGoogleSheets()
vmix_selector = SourceSelector()
minions = Minions()


class Skipper:
    class Config:
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

        def pull(self, enable_minions_deploy: bool, langs: List[str] = None) -> ExecutionStatus:
            """
            Pulls and applies configuration from google sheet (need to setup config first, see setup())
            :param enable_minions_deploy: If true - deploys minions if needed, if false - won't deploy any minion
            :param langs: If this parameter specified - updates data for only specified languages. Note that,
            if langs were pulled before, but they are not specified in `langs` parameter - they won't be neither
            dropped nor affected somehow.
            :return:
            """
            # TODO: self.obs_sheets.pull() -> ...
            pass

        def push(self):
            # TODO: ... -> self.obs_sheets.push(...)
            pass

    def __init__(self):
        self.config = Skipper.Config(self)
