import json
import os
import time

from dotenv import load_dotenv
from flask import Flask
from flask import request
import requests
from urllib.parse import urlencode

import server
from config import API_CLEANUP_ROUTE
from config import API_INIT_ROUTE
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
from util import ExecutionStatus

load_dotenv()
# MEDIA_DIR = os.getenv('MEDIA_DIR')


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

app = Flask(__name__)
obs_server: server.Server = None


@app.route(API_INIT_ROUTE, methods=["POST"])
def init():
    """
    Query parameters:
    server_langs: json, dict {
        "obs_host": "localhost",
        "websocket_port": 1234,
        "password": "qwerty123",
        "original_media_url": "srt://localhost"
    }
    :return:
    """
    server_langs = request.args.get("server_langs", "")
    server_langs = json.loads(server_langs)

    # status: ExecutionStatus = util.validate_init_params(server_langs)
    # if not status:
    #     return status.to_http_status()

    global obs_server

    if obs_server is not None:
        try:
            obs_server.cleanup()
            time.sleep(1)  # wait for cleanup
        except Exception:  # FIXME
            pass
        del obs_server
        obs_server = None

    obs_server = server.Server(server_langs=server_langs)
    status: ExecutionStatus = obs_server.initialize()

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


@app.route(API_MEDIA_PLAY_ROUTE, methods=["POST"])
def media_play():
    """
    Query parameters:
    params: json dictionary,
    e.g. {"lang": {"name": "...", "search_by_num": "0/1"}, ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    params = request.args.get("params", None)
    params = json.loads(params)

    status: ExecutionStatus = obs_server.run_media(params=params)

    return status.to_http_status()


@app.route(API_SET_STREAM_SETTINGS_ROUTE, methods=["POST"])
def set_stream_settings():
    """
    Query parameters:
    stream_settings: json dictionary,
    e.g. {"lang": {"server": "rtmp://...", "key": "..."}, ...}
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
    Starts streaming on all machines
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    status: ExecutionStatus = obs_server.start_streaming()

    return status.to_http_status()


@app.route(API_STREAM_STOP_ROUTE, methods=["POST"])
def stream_stop():
    """
    Stops streaming on all machines
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    status: ExecutionStatus = obs_server.stop_streaming()

    return status.to_http_status()


@app.route(API_TS_OFFSET_ROUTE, methods=["POST"])
def set_ts_offset():
    """
    Query parameters:
    offset_settings: json dictionary,
    e.g. {"lang": offset, ...} (note, offset in milliseconds)
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    offset_settings = request.args.get("offset_settings", None)
    offset_settings = json.loads(offset_settings)

    status: ExecutionStatus = obs_server.set_ts_sync_offset(offset_settings=offset_settings)

    return status.to_http_status()


@app.route(API_TS_OFFSET_ROUTE, methods=["GET"])
def get_ts_offset():
    """
    Retrieves information about teamspeak sound offset
    :return: {"lang": offset, ...} (note, offset in milliseconds)
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
    volume_settings: json dictionary,
    e.g. {"lang": volume, ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    volume_settings = request.args.get("volume_settings", None)
    volume_settings = json.loads(volume_settings)
    # TODO: validate `volume_settings`

    status: ExecutionStatus = obs_server.set_ts_volume_db(volume_settings=volume_settings)

    return status.to_http_status()


@app.route(API_TS_VOLUME_ROUTE, methods=["GET"])
def get_ts_volume():
    """
    Retrieves information about teamspeak sound volume
    :return: {"lang": volume, ...} (note, volume in decibels)
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
    volume_settings: json dictionary,
    e.g. {"lang": volume, ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    volume_settings = request.args.get("volume_settings", None)
    volume_settings = json.loads(volume_settings)
    # TODO: validate `volume_settings`

    status: ExecutionStatus = obs_server.set_source_volume_db(volume_settings=volume_settings)

    return status.to_http_status()


@app.route(API_SOURCE_VOLUME_ROUTE, methods=["GET"])
def get_source_volume():
    """
    Retrieves information about original source sound volume
    :return: {"lang": volume, ...} (note, volume in decibels)
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
    e.g. {"lang": {'ratio': ..., 'release_time': ..., 'threshold': ...}, ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    sidechain_settings = request.args.get("sidechain_settings", None)
    sidechain_settings = json.loads(sidechain_settings)
    # TODO: validate `sidechain_settings`

    status: ExecutionStatus = obs_server.setup_sidechain(sidechain_settings=sidechain_settings)

    return status.to_http_status()


@app.route(API_TRANSITION_ROUTE, methods=["POST"])
def setup_transition():
    """
    Query parameters:
    transition_settings: json dictionary,
    e.g. {"lang": {'transition_name': ..., 'audio_fade_style': ..., 'path': ..., ...}, ...}
    :return:
    """
    if obs_server is None:
        return ExecutionStatus(status=False, message="The server was not initialized yet").to_http_status()

    transition_settings = request.args.get("transition_settings", None)
    transition_settings = json.loads(transition_settings)
    # TODO: validate `transition_settings`

    status: ExecutionStatus = obs_server.setup_transition(transition_settings=transition_settings)

    return status.to_http_status()


@app.route(API_GDRIVE_SYNC, methods=["POST"])
def setup_gdrive_sync():
    """
    Query parameters:
    gdrive_settings: json dictionary,
    e.g. {"lang": {'drive_id': ..., 'media_dir': ..., 'api_key': ..., 'sync_seconds': ..., gdrive_sync_addr: ...}, ...}
    :return:
    """
    gdrive_settings = request.args.get("gdrive_settings", None)
    gdrive_settings = json.loads(gdrive_settings)

    media_dir_settings = {lang: gdrive_settings[lang]['media_dir'] for lang in gdrive_settings}
    #obs_server.set_media_dir(media_dir_settings)

    status: ExecutionStatus = ExecutionStatus()
    for lang, data in gdrive_settings.items():
        drive_id = data['drive_id']
        media_dir = data['media_dir']
        api_key = data['api_key']
        sync_seconds = data['sync_seconds']
        gdrive_sync_addr = 'http://localhost:7000/init'  # data['gdrive_sync_addr']  # default, one language


        query_params = urlencode({
            'drive_id': drive_id,
            'media_dir': media_dir,
            'api_key': api_key,
            'sync_seconds': sync_seconds
        })

        response_ = requests.post(f"{gdrive_sync_addr}?{query_params}")
        if response_.status_code != 200:
            msg_ = f"E PYSERVER::setup_gdrive_sync(): Lang {lang}, details: {response_.text}"
            print(msg_)
            status.append_error(msg_)

    return status.to_http_status()


@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return '', 200


if __name__ == "__main__":
    app.run("0.0.0.0", 6000)
