# REFACTORING: build a new class which manages all the functionality listed below

from googleapi import OBSGoogleSheets, LangConfig, SheetConfig

instance_service_addrs = util.ServiceAddrStorage()  # dict of `"lang": {"addr": "address"}
langs: list[str] = []
server_state = ServerState(ServerState.SLEEPING)
# init_status, wakeup_status = False, False
media_scheduler = MediaScheduler()
sheets = OBSGoogleSheets()
timing_sheets = TimingGoogleSheets()
vmix_selector = SourceSelector()
minions = Minions()

class Jarvice:
    def __init__(self):
        self.obs_sheets = OBSGoogleSheets()

    def config_setup(self, sheet_url, sheet_name):
        pass

    def config_pull(self):
        pass

    def config_push(self):
        pass
