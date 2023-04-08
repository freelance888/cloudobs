import os
from dotenv import load_dotenv
from server import Skipper

load_dotenv()
COMMON_SERVICE_PORT = int(os.getenv("COMMON_SERVICE_PORT", 5000))

# Setup Sentry
# ------------
# if env var set - setup integration
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
    )

if __name__ == "__main__":
    skipper = Skipper(port=COMMON_SERVICE_PORT)
    skipper.run()

"""
# Run the following code to run the server:

from threading import Thread

import os
from dotenv import load_dotenv
from server import Skipper

skipper = Skipper(port=5010)

class T(Thread):
    def __init__(self, skipper):
        super().__init__()
        self.skipper = skipper

    def run(self):
        self.skipper.run()

t = T(skipper)
t.start()

# end

###
skipper.registry.minion_configs["Bel"].addr_config.obs_host = skipper.registry.minion_configs["Bel"].addr_config.minion_server_addr
skipper.registry.minion_configs["Bel"].addr_config.minion_server_addr = "localhost"
skipper.minions["Bel"] = Skipper.Minion(minion_ip="localhost", lang="Bel", ws_port=6000)
skipper.activate_registry()
"""
"""
# -- client
# Run the following code to connect to the server
import socketio
import json
import time
from util import WebsocketResponse

sio = socketio.Client()
sio.connect('http://localhost:5010')

registry_changes = []
def on_registry_change(data):
    registry_changes.append(data)

logs = []
def on_log(data):
    logs.append(data)

sio.on("on_registry_change", on_registry_change)
sio.on("on_log", on_log)

ws_response = WebsocketResponse()
command = {
    "command": "pull config",
    "details": {
        "sheet_url": "https://docs.google.com/spreadsheets/d/10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME",
        "sheet_name": "table_4",
    }
}
sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
time.sleep(2)
ws_response.result()

# ----- get info
ws_response = WebsocketResponse()
command = {
    "command": "get info"
}
sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
time.sleep(2)
json.loads(json.loads(ws_response.result())["serializable_object"])

# ----- play media
ws_response = WebsocketResponse()
command = {
    "command": "play media",
    "details": {
        "name": "30_video.mp4",
        "search_by_num": True,
        "mode": "force"
    }
}
sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
time.sleep(2)
ws_response.result()

# ----- pull timing
ws_response = WebsocketResponse()
command = {
    "command": "pull timing",
    "details": {
        "sheet_url": "https://docs.google.com/spreadsheets/d/10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME/edit#gid=2006470615",
        "sheet_name": "demo_timing1"
    }
}
sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
time.sleep(2)
ws_response.result()
"""
