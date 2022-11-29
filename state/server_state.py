# REFACTORING: build a new class which manages all the functionality listed below

instance_service_addrs = util.ServiceAddrStorage()  # dict of `"lang": {"addr": "address"}
langs: list[str] = []
server_state = ServerState(ServerState.SLEEPING)
# init_status, wakeup_status = False, False
cb_thread = CallbackThread()
media_scheduler = MediaScheduler()
sheets = OBSGoogleSheets()
timing_sheets = TimingGoogleSheets()
vmix_selector = SourceSelector()
minions = Minions()

class ServerState:
    def __init__(self):
        pass
