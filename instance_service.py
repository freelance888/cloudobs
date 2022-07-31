import json
import os
import time
import threading

from dotenv import load_dotenv
from flask import Flask
from flask import request
import requests
from urllib.parse import urlencode

import server
from config import API_CLEANUP_ROUTE
from config import API_INIT_ROUTE
from config import API_MEDIA_SCHEDULE_ROUTE
from config import API_MEDIA_PLAY_ROUTE
from config import API_SET_STREAM_SETTINGS_ROUTE
from config import API_SIDECHAIN_ROUTE
from config import API_SOURCE_VOLUME_ROUTE
from config import API_STREAM_START_ROUTE
from config import API_STREAM_STOP_ROUTE
from config import API_TRANSITION_ROUTE
from config import API_TS_OFFSET_ROUTE
from config import API_TS_VOLUME_ROUTE
from config import API_GDRIVE_SYNC
from config import API_GDRIVE_FILES
from config import API_INFO_ROUTE
from config import API_WAKEUP_ROUTE
from util import ExecutionStatus

load_dotenv()
DEFAULT_MEDIA_DIR = os.getenv('MEDIA_DIR', './content')
DEFAULT_API_KEY = os.getenv('GDRIVE_API_KEY', '')
DEFAULT_SYNC_SECONDS = os.getenv('GDRIVE_SYNC_SECONDS', 60)
try:
    DEFAULT_SYNC_SECONDS = int(DEFAULT_SYNC_SECONDS)
except:
    DEFAULT_SYNC_SECONDS = 60

# Setup Sentry
# ------------
# if env var set - setup integration
SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
    )


class GDriveHelper:
    def __init__(self):
        self.worker = {}

    def set_worker(self, addr, drive_id, media_dir, api_key, sync_seconds):
        self.worker = {
            "addr": addr,
            "drive_id": drive_id,
            "media_dir": media_dir,
            "api_key": api_key,
            "sync_seconds": sync_seconds
        }


app = Flask(__name__)
obs_server: server.Server = None
gdrive_helper: GDriveHelper = GDriveHelper()
wakeup_status = False


@app.route(API_INFO_ROUTE, methods=["GET"])
def get_info():
    """
    :return:
    """
    return json.dumps(obs_server.settings.to_dict()), 200


@app.route(API_INFO_ROUTE, methods=["POST"])
def post_info():
    """
    :return:
    """
    info_ = request.args.get("info", "")
    if not info_:
        return "Please specify `info`", 500
    try:
        info_ = json.loads(info_)
    except Exception as ex:
        return f"Error while parsing json, details: {ex}"

    return obs_server.set_info(info_).to_http_status()


@app.route(API_WAKEUP_ROUTE, methods=["POST"])
def wakeup():
    global wakeup_status
    global obs_server
    # if not wakeup_status:
    obs_server = server.Server()
    wakeup_status = True
    return "Ok", 200


@app.route(API_INIT_ROUTE, methods=["POST"])
def init():
    """
    Query parameters:
    server_langs: json, dict {
        "host_url": "...",
        "obs_host": "localhost",
        "websocket_port": 1234,
        "password": "qwerty123",
        "original_media_url": "srt://localhost"
    }
    :return:
    """
    server_langs = request.args.get("server_langs", "")
    if not server_langs:
        return ExecutionStatus(False, "server_langs not specified").to_http_status()
    server_langs = json.loads(server_langs)

    global obs_server

    if obs_server is not None:
        try:
            obs_server.cleanup()
            time.sleep(1)  # wait for cleanup
        except Exception:  # FIXME
            pass
        del obs_server
        obs_server = None

    obs_server = server.Server()
    status: ExecutionStatus = obs_server.initialize(server_langs=server_langs)

    return status.to_http_status()


@app.route(API_CLEANUP_ROUTE, methods=["POST"])
def cleanup():
    """
    :return:
    """
    global obs_server

    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    if obs_server is not None:
        obs_server.cleanup()
        del obs_server
        obs_server = None

    return ExecutionStatus(status=True).to_http_status()


@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["POST"])
def media_schedule():
    """
    Query parameters:
    schedule: json dictionary,
    e.g. [..., [path, timestamp], ...]
     - path - media name
     - timestamp - relative timestamp in milliseconds
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    schedule = request.args.get("schedule", None)
    schedule = json.loads(schedule)

    status: ExecutionStatus = obs_server.schedule_media(schedule=schedule)

    return status.to_http_status()


@app.route(API_MEDIA_PLAY_ROUTE, methods=["POST"])
def media_play():
    """
    Query parameters:
    params: json dictionary,
    e.g. {"name": "...", "search_by_num": "0/1"}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    params = request.args.get("params", None)
    params = json.loads(params)

    status: ExecutionStatus = obs_server.run_media(params=params)

    return status.to_http_status()


@app.route(API_MEDIA_PLAY_ROUTE, methods=["DELETE"])
def delete_media_play():
    """
    Stops any media played
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    status: ExecutionStatus = obs_server.stop_media()

    return status.to_http_status()


@app.route(API_SET_STREAM_SETTINGS_ROUTE, methods=["POST"])
def set_stream_settings():
    """
    Query parameters:
    stream_settings: json dictionary,
    e.g. {"server": "rtmp://...", "key": "..."}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    stream_settings = request.args.get("stream_settings", None)
    stream_settings = json.loads(stream_settings)

    status: ExecutionStatus = obs_server.set_stream_settings(stream_settings=stream_settings)

    return status.to_http_status()


@app.route(API_STREAM_START_ROUTE, methods=["POST"])
def stream_start():
    """
    Starts streaming.
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    status: ExecutionStatus = obs_server.start_streaming()

    return status.to_http_status()


@app.route(API_STREAM_STOP_ROUTE, methods=["POST"])
def stream_stop():
    """
    Stops streaming.
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    status: ExecutionStatus = obs_server.stop_streaming()

    return status.to_http_status()


@app.route(API_TS_OFFSET_ROUTE, methods=["POST"])
def set_ts_offset():
    """
    Query parameters:
    ts_offset: json dictionary,
    e.g. ts_offset (note, offset in milliseconds)
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    ts_offset = request.args.get("ts_offset", None)
    ts_offset = json.loads(ts_offset)

    status: ExecutionStatus = obs_server.set_ts_sync_offset(ts_offset=ts_offset)

    return status.to_http_status()


@app.route(API_TS_OFFSET_ROUTE, methods=["GET"])
def get_ts_offset():
    """
    Retrieves information about teamspeak sound offset
    :return: offset (note, offset in milliseconds)
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    data = obs_server.get_ts_sync_offset()
    data = json.dumps(data)

    return data, 200


@app.route(API_TS_VOLUME_ROUTE, methods=["POST"])
def set_ts_volume():
    """
    Query parameters:
    volume_db: json dictionary,
    e.g. 0
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    volume_db = request.args.get("volume_db", None)
    volume_db = json.loads(volume_db)

    status: ExecutionStatus = obs_server.set_ts_volume_db(volume_db=volume_db)

    return status.to_http_status()


@app.route(API_TS_VOLUME_ROUTE, methods=["GET"])
def get_ts_volume():
    """
    Retrieves information about teamspeak sound volume
    :return: volume_db (note, volume in decibels)
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    data = obs_server.get_ts_volume_db()
    data = json.dumps(data)

    return data, 200


@app.route(API_SOURCE_VOLUME_ROUTE, methods=["POST"])
def set_source_volume():
    """
    Query parameters:
    volume_db: json dictionary,
    e.g. 0
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    volume_db = request.args.get("volume_db", None)
    volume_db = json.loads(volume_db)
    # TODO: validate `volume_settings`

    status: ExecutionStatus = obs_server.set_source_volume_db(volume_db=volume_db)

    return status.to_http_status()


@app.route(API_SOURCE_VOLUME_ROUTE, methods=["GET"])
def get_source_volume():
    """
    Retrieves information about original source sound volume
    :return: volume_db (note, volume in decibels)
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    data = obs_server.get_source_volume_db()
    data = json.dumps(data)

    return data, 200


@app.route(API_SIDECHAIN_ROUTE, methods=["POST"])
def setup_sidechain():
    """
    Query parameters:
    sidechain_settings: json dictionary,
    e.g. {'ratio': ..., 'release_time': ..., 'threshold': ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    sidechain_settings = request.args.get("sidechain_settings", None)
    sidechain_settings = json.loads(sidechain_settings)

    status: ExecutionStatus = obs_server.setup_sidechain(sidechain_settings=sidechain_settings)

    return status.to_http_status()


@app.route(API_TRANSITION_ROUTE, methods=["POST"])
def setup_transition():
    """
    Query parameters:
    transition_settings: json dictionary,
    e.g. {'transition_name': ..., 'audio_fade_style': ..., 'path': ..., ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    transition_settings = request.args.get("transition_settings", None)
    transition_settings = json.loads(transition_settings)

    status: ExecutionStatus = obs_server.setup_transition(transition_settings=transition_settings)

    return status.to_http_status()


@app.route(API_GDRIVE_SYNC, methods=["POST"])
def setup_gdrive_sync():
    # TODO: -> Server
    """
    Query parameters:
    gdrive_settings: json dictionary,
    e.g. {'drive_id': ..., 'media_dir': ..., 'api_key': ..., 'sync_seconds': ..., gdrive_sync_addr: ...}
    :return:
    """
    # check if gdrive_settings is specified
    gdrive_settings = request.args.get("gdrive_settings", None)
    if not gdrive_settings:
        return ExecutionStatus(False, "gdrive_settings not specified").to_http_status()
    gdrive_settings = json.loads(gdrive_settings)
    # drive_id should also be specified
    if "drive_id" not in gdrive_settings:
        return ExecutionStatus(False, "drive_id not specified").to_http_status()

    obs_server.setup_gdrive(gdrive_settings).to_http_status()


@app.route(API_GDRIVE_FILES, methods=["GET"])
def get_gdrive_files():
    """
    Retrieves information about google drive files,
    returns dict of [... [filename, true/false - loaded/not loaded], ...]
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    if "addr" not in gdrive_helper.worker:
        return ExecutionStatus(False, "Google drive was not initialized yet").to_http_status()

    addr = gdrive_helper.worker["addr"]
    response_ = requests.get(f"{addr}/files")
    if response_.status_code != 200:
        msg_ = f"E PYSERVER::get_gdrive_files(): Details: {response_.text}"
        print(msg_)
        return "#", 500
    data = json.loads(response_.text)
    return json.dumps(data), 200


@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return '', 200


@app.before_request
def before_request():
    if not wakeup_status:
        if request.path not in (API_WAKEUP_ROUTE,):
            return f"The server is sleeping :) Tell the admin to wake it up."


if __name__ == "__main__":
    app.run("0.0.0.0", 6000)
