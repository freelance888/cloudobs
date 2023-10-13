import os
from dotenv import load_dotenv
from server import Skipper
from util import ExecutionStatus
import requests
import json

load_dotenv()
COMMON_SERVICE_PORT = int(os.getenv("COMMON_SERVICE_PORT", 5000))
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", None)
TELEGRAM_CHANNEL_ID = int(TELEGRAM_CHANNEL_ID) if TELEGRAM_CHANNEL_ID is not None else None
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", None)

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


def telegram_media_play_callback(command, details, lang, result: ExecutionStatus, ip):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    video = details["name"] if "name" in details else ""

    tg_msg = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": f"🎦 <b>Start video</b>:  {video} 🎦",
        "parse_mode": "HTML",
    }
    requests.post(f"{base_url}/sendMessage", json=tg_msg, timeout=5)


# if __name__ == "__main__":
skipper = Skipper(port=COMMON_SERVICE_PORT)
skipper.event_handler.add_or_replace_on_command_completed_event(
    foo=telegram_media_play_callback, id="telegram media play",
    command="play media", run_in_new_thread=True
)
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
import clipboard as clip

sio = socketio.Client()
#sio.connect('http://sa_main:5010')
sio.connect('http://localhost:5010', auth={"HTTP_LOGIN": "boba", "HTTP_PASSWORD": ""})

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
        "users_sheet_name": "Users (sample)",
        #"ip_langs": dict([x.split()[::] for x in clip.paste().split("\n")])
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
t = json.loads(json.loads(ws_response.result())["serializable_object"])
t['registry']['minion_configs']

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
