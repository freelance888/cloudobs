import json
import os
import re
from urllib.parse import urlencode
import threading

from dotenv import load_dotenv
from flask import Flask
from flask import request

from media import server
from util.config import API_CLEANUP_ROUTE
from util.config import API_WAKEUP_ROUTE
from util.config import API_INIT_ROUTE
from util.config import API_MEDIA_SCHEDULE_ROUTE
from util.config import API_MEDIA_PLAY_ROUTE
from util.config import API_SET_STREAM_SETTINGS_ROUTE
from util.config import API_SIDECHAIN_ROUTE
from util.config import API_SOURCE_VOLUME_ROUTE
from util.config import API_STREAM_START_ROUTE
from util.config import API_STREAM_STOP_ROUTE
from util.config import API_TRANSITION_ROUTE
from util.config import API_TS_OFFSET_ROUTE
from util.config import API_TS_VOLUME_ROUTE
from util.config import API_GDRIVE_SYNC
from util.config import API_GDRIVE_FILES
from util.config import API_INFO_ROUTE
from util.config import API_PULL_SHEETS
from util.config import API_PUSH_SHEETS
from util.config import API_VMIX_PLAYERS, API_VMIX_ACTIVE_PLAYER
from util.util import ExecutionStatus, MultilangParams, CallbackThread
from util.vmix import SourceSelector
import util.util as util
from media.scheduler import MediaScheduler
from googleapi.google_sheets import OBSGoogleSheets

load_dotenv()
MEDIA_DIR = os.getenv("MEDIA_DIR")
COMMON_SERVICE_PORT = int(os.getenv("COMMON_SERVICE_PORT", 5000))

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
instance_service_addrs = util.ServiceAddrStorage()  # dict of `"lang": {"addr": "address"}
langs = []
init_status, wakeup_status = False, False
cb_thread = CallbackThread()
media_scheduler = MediaScheduler()
sheets = OBSGoogleSheets()
vmix_selector = SourceSelector()


def broadcast(api_route,
              http_method,
              params: util.MultilangParams = None,
              param_name="params",
              return_status=False,
              method_name="broadcast"):
    requests_ = {}  # lang: request
    responses_ = {}  # lang: response

    langs_ = params.list_langs() if params is not None else langs
    # create requests for all langs
    for lang in langs_:
        addr = instance_service_addrs.addr(lang)  # get server address
        request_ = f"{addr}{api_route}"  # create requests
        if params is not None:  # add query params if needed
            params_json = json.dumps(params[lang])
            query_params = urlencode({param_name: params_json})
            request_ = request_ + "?" + query_params
        requests_[lang] = request_  # dump request

    # initialize grequests
    urls = list(requests_.values())
    if http_method == "GET":
        responses_ = util.async_aiohttp_get_all(urls=urls)
    elif http_method == "POST":
        responses_ = util.async_aiohttp_post_all(urls=urls)
    responses_ = {lang: responses_[i] for i, lang in enumerate(requests_.keys())}

    # decide wether to return status of response
    if return_status:
        status: ExecutionStatus = ExecutionStatus(status=True)
        for lang, response_ in responses_.items():
            if response_.status_code != 200:
                msg_ = f"E PYSERVER::{method_name}(): {lang}, details: {response_.text}"
                print(msg_)
                status.append_error(msg_)
        return status
    else:
        return responses_


def load_ip_list(path):
    global langs, instance_service_addrs
    instance_service_addrs = util.ServiceAddrStorage()

    with open(path, "rt") as fp:
        text = fp.read()
    ip_list = re.findall(r"^\[(?P<lang>[A-Za-z]+)\]\=(?P<ip>[a-zA-Z0-9\.]+)",
                         text.replace(' ', ''), flags=re.MULTILINE)
    for lang, ip in ip_list:
        instance_service_addrs[lang] = {
            "addr": f"http://{ip}:6000",  # address of instance_service
        }
    langs = [lang for lang, ip in ip_list]


def sync_from_sheets():
    try:
        params = sheets.to_multilang_params()
        for lang in params:
            params[lang][server.SUBJECT_SERVER_LANGS]["host_url"] = instance_service_addrs.addr(lang)
    except Exception as ex:
        msg_ = f"Something happened while preparing for synchronization the server from Google Sheets. " \
               f"Details: {ex}"
        print(msg_)
        return ExecutionStatus(False, msg_)
    status = broadcast(
        api_route=API_INFO_ROUTE,
        http_method="POST",
        params=params,
        param_name="info",
        return_status=True,
        method_name="init_from_sheets"
    )
    return status


def init_from_server_langs(server_langs):
    # validate input parameters before broadcasting them to servers
    status: ExecutionStatus = util.validate_init_params(server_langs)
    if not status:
        return status

    for lang in server_langs:
        server_langs[lang]["obs_host"] = "localhost"

    params = MultilangParams(server_langs, langs=langs)
    status = broadcast(
        API_INIT_ROUTE,
        "POST",
        params=params,
        param_name="server_langs",
        return_status=True,
        method_name="init_from_server_langs",
    )
    return status


def init_from_sheets(sheet_url, worksheet_name):
    try:
        sheets.set_sheet(sheet_url, worksheet_name)
        pull_sheets()
        return ExecutionStatus(True)
    except Exception as ex:
        msg_ = f"Something happened while setting up Google Sheets. Details: {ex}"
        print(msg_)
        return ExecutionStatus(False, msg_)


def get_info(fillna: object = "#"):
    responses = broadcast(API_INFO_ROUTE, "GET", params=None, return_status=False)
    data = {}
    schedule = media_scheduler.get_schedule()

    for lang, response in responses.items():
        try:
            data[lang] = json.loads(response.text)
            data[lang][server.SUBJECT_SERVER_LANGS]["host_url"] = instance_service_addrs.addr(lang)

            data[lang][server.SUBJECT_MEDIA_SCHEDULE] = "#" if schedule is None else schedule
        except json.JSONDecodeError:
            if fillna:
                data[lang] = fillna
    return data


def pull_sheets():
    """
    pull - means pull data from Google Sheets to the server
    """
    if not sheets.ok():
        return ExecutionStatus(False, "Something happened with Google Sheets object")
    try:
        sheets.pull()
    except Exception as ex:
        return ExecutionStatus(False, f"Couldn't pull data from Google Sheets. Details: {ex}")
    return sync_from_sheets()


def push_sheets():
    """
    push - means push data from the server to Google Sheets
    """
    if not sheets.ok():
        return ExecutionStatus(False, "Something happened with Google Sheets object")
    try:
        info_ = get_info(fillna=False)
        for lang, info__ in info_.items():
            sheets.from_info(lang, info__)
        sheets.push()
    except Exception as ex:
        return ExecutionStatus(False, f"Couldn't push data to Google Sheets. Details: {ex}")
    return ExecutionStatus(True, "Ok")


@app.route(API_WAKEUP_ROUTE, methods=["POST"])
def wakeup():
    """
    Query parameters:
     - iplist - json list of [... [lang_code, ip_address], ...]
    """
    # validate parameters
    iplist = request.args.get("iplist", "")
    if not iplist:
        return ExecutionStatus(False, "Please specify `iplist` parameter").to_http_status()
    try:
        iplist = json.loads(iplist)
        if not isinstance(iplist, list):
            return ExecutionStatus(False, "`iplist` should be a list object. Please refer to docs").to_http_status()
    except json.JSONDecodeError as ex:
        return ExecutionStatus(False, f"JSON decode error. Details: {ex}").to_http_status()
    except Exception as ex:
        return ExecutionStatus(False, f"Something happened. Details: {ex}").to_http_status()

    # fill metadata
    global langs, instance_service_addrs
    instance_service_addrs = util.ServiceAddrStorage()
    for lst in iplist:
        # check the list structure
        if not isinstance(lst, list) or len(lst) != 2:
            return ExecutionStatus(False, "`iplist` entry should also be a list with length 2").to_http_status()
        lang, ip = lst
        instance_service_addrs[lang] = {
            "addr": f"http://{ip}:6000",  # address of instance_service
        }
    langs = [lang for lang, ip in iplist]

    # broadcast wakeup to minions
    status = broadcast(
        API_WAKEUP_ROUTE,
        "POST",
        return_status=True,
        method_name="wakeup",
    )

    # the server has woken up
    global wakeup_status
    wakeup_status = True
    return "Ok", 200


@app.route(API_INFO_ROUTE, methods=["GET"])
def info():
    """
    :return:
    """
    return json.dumps(get_info()), 200


@app.route(API_INIT_ROUTE, methods=["POST"])
def init():
    """
    Query parameters:
    server_langs: json,
        dict of "lang": {
            "host_url": "base_url",
            "websocket_port": 1234,
            "password": "qwerty123",
            "original_media_url": "srt://localhost"
        }
    e.g.: {
        "eng": {
            "host_url": "http://255.255.255.255:5000",
            "websocket_port": 1234,
            "password": "qwerty123",
            "original_media_url": "srt://localhost"
        },
        "eng": ...
    }
    :return:
    """
    global init_status
    init_status = False

    if request.args.get("server_langs", ""):
        server_langs = request.args.get("server_langs")
        try:
            server_langs = json.loads(server_langs)
        except:
            return ExecutionStatus(False, "Couldn't parse json").to_http_status()
        status = init_from_server_langs(server_langs)
    elif request.args.get("sheet_url", "") and request.args.get("worksheet_name", ""):
        sheet_url = request.args.get("sheet_url")
        worksheet_name = request.args.get("worksheet_name")
        status = init_from_sheets(sheet_url, worksheet_name)
    else:
        return ExecutionStatus(False, "Invalid parameters list").to_http_status()

    if status:
        init_status = True
    return status.to_http_status()


@app.route(API_INIT_ROUTE, methods=["GET"])
def get_init():
    """
    :return:
    """
    if init_status:
        responses = broadcast(API_INFO_ROUTE, "GET", params=None, return_status=False)
        data = {}
        for lang, response in responses.items():
            try:
                data[lang] = json.loads(response.text)[server.SUBJECT_SERVER_LANGS]
                data[lang]["host_url"] = instance_service_addrs.addr(lang)
            except json.JSONDecodeError:
                data[lang] = "#"

        return json.dumps(data), 200
    else:
        # server_langs_default:
        # {
        #     "obs_host": "localhost",
        #     "host_url": "",
        #     "websocket_port": 4439,
        #     "password": "",
        #     "original_media_url": "",
        # }
        server_langs_default = server.ServerSettings.default().get_subject(server.SUBJECT_SERVER_LANGS)  # dict
        data = {}
        for lang in instance_service_addrs:
            addr = instance_service_addrs.addr(lang)
            server_langs = server_langs_default.copy()
            server_langs["host_url"] = addr
            server_langs.pop("obs_host")
            data[lang] = server_langs
        return json.dumps(data), 200


@app.route(API_PULL_SHEETS, methods=["POST"])
def api_pull_sheets():
    return pull_sheets().to_http_status()


@app.route(API_PUSH_SHEETS, methods=["POST"])
def api_push_sheets():
    return push_sheets().to_http_status()


@app.route(API_CLEANUP_ROUTE, methods=["POST"])
def cleanup():
    """
    :return:
    """
    status = ExecutionStatus(status=True)

    responses = broadcast(API_CLEANUP_ROUTE, "POST")
    for lang, response in responses.items():
        if response.status_code != 200:
            msg_ = f"E PYSERVER::cleanup(): couldn't cleanup server for {lang}, details: {response.text}"
            print(msg_)
            status.append_error(msg_)

    global init_status
    init_status = False

    return status.to_http_status()


@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["GET"])
def get_media_schedule():
    """
    :return: dictionary:
    {
      id_1: {
        "name": "...",
        "timestamp": ...,
        "is_enabled": true/false,
        "is_played": true/false  # добавил еще is_played, показывает, было ли это видео уже проиграно
      },
      id_2: {
        ...
      },
      ...
    }
    """
    return ExecutionStatus(True, message=json.dumps(media_scheduler.get_schedule())).to_http_status()


# PUT /media/schedule?id=...&is_enabled=False&name=...&timestamp=...
@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["PUT"])
def update_media_schedule():
    """
    Query parameters:
    id: schedule id,
    name: schedule id,
    timestamp: new timestamp,
    is_enabled: schedule id
    :return:
    """
    id_ = request.args.get("id", None)
    if id_ is None:
        return ExecutionStatus(False, message="Please specify schedule id").to_http_status()
    try:
        id_ = int(id_)
    except:
        return ExecutionStatus(False, message="Invalid `id`")
    name = request.args.get("name", None)
    timestamp = request.args.get("timestamp", None)
    is_enabled = request.args.get("is_enabled", None)

    status = media_scheduler.modify_schedule(id_=id_, name=name, timestamp=timestamp, is_enabled=is_enabled)

    return status.to_http_status()


@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["DELETE"])
def delete_media_schedule():
    return media_scheduler.delete_schedule().to_http_status()


@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["POST"])
def media_schedule():
    """
    Query parameters:
    schedule: schedule list,
    e.g. [..., [name, timestamp], ...]
     - path - media name
     - timestamp - relative timestamp in seconds
    :return:
    """
    schedule = request.args.get("schedule", None)
    schedule = json.loads(schedule)

    def foo(id_, name, timestamp, is_enabled, is_played):
        if not is_enabled or is_played:
            return False
        params = MultilangParams({"__all__": {"name": name, "search_by_num": "1"}}, langs=langs)
        try:
            _ = broadcast(
                API_MEDIA_PLAY_ROUTE, "POST", params=params,
                param_name="params", return_status=True, method_name="media_play"
            )
            return True
        except BaseException as ex:
            print(f"E PYSERVER::common_service::media_schedule(): {ex}")
            return False

    status = media_scheduler.create_schedule(schedule=schedule, foo=foo)

    return status.to_http_status()


@app.route(API_MEDIA_PLAY_ROUTE, methods=["POST"])
def media_play():
    """
    Query parameters:
    params: json dictionary,
    e.g. {"lang": {"name": "...", "search_by_num": "0/1"}, ...}
    :return:
    """
    params = request.args.get("params", None)
    params = json.loads(params)

    params = MultilangParams(params, langs=langs)
    status = broadcast(
        API_MEDIA_PLAY_ROUTE, "POST", params=params, param_name="params", return_status=True, method_name="media_play"
    )

    return status.to_http_status()


@app.route(API_MEDIA_PLAY_ROUTE, methods=["DELETE"])
def media_play_delete():
    """
    Stops any media played
    :return:
    """

    status = broadcast(
        API_MEDIA_PLAY_ROUTE, "DELETE", return_status=True, method_name="media_play"
    )

    return status.to_http_status()


@app.route(API_SET_STREAM_SETTINGS_ROUTE, methods=["POST"])
def set_stream_settings():
    """
    Query parameters:
    stream_settings: json dictionary,
    e.g. {"lang": {"server": "rtmp://...", "key": "..."}, ...}
    :return:
    """
    stream_settings = request.args.get("stream_settings", None)
    stream_settings = json.loads(stream_settings)

    params = MultilangParams(stream_settings, langs=langs)
    status = broadcast(
        API_SET_STREAM_SETTINGS_ROUTE,
        "POST",
        params=params,
        param_name="stream_settings",
        return_status=True,
        method_name="set_stream_settings",
    )

    return status.to_http_status()


@app.route(API_STREAM_START_ROUTE, methods=["POST"])
def stream_start():
    """
    Starts streaming.
    Query parameters:
    langs: json list of langs,
    e.g. ["eng", "rus"], or ["__all__"] (default)
    :return:
    """
    _langs = request.args.get("langs", json.dumps(["__all__"]))
    _langs = json.loads(_langs)

    params = MultilangParams({_: {} for _ in _langs}, langs=langs)
    status = broadcast(API_STREAM_START_ROUTE, "POST", params=params, return_status=True, method_name="stream_start")

    return status.to_http_status()


@app.route(API_STREAM_STOP_ROUTE, methods=["POST"])
def stream_stop():
    """
    Stops streaming.
    Query parameters:
    langs: json list of langs,
    e.g. ["eng", "rus"], or ["__all__"] (default)
    :return:
    """
    _langs = request.args.get("langs", json.dumps(["__all__"]))
    _langs = json.loads(_langs)

    params = MultilangParams({_: {} for _ in _langs}, langs=langs)
    status = broadcast(API_STREAM_STOP_ROUTE, "POST", params=params, return_status=True, method_name="stream_stop")

    return status.to_http_status()


@app.route(API_TS_OFFSET_ROUTE, methods=["POST"])
def set_ts_offset():
    """
    Query parameters:
    offset_settings: json dictionary,
    e.g. {"lang": 4000, ...} (note, offset in milliseconds)
    :return:
    """
    offset_settings = request.args.get("offset_settings", None)
    offset_settings = json.loads(offset_settings)

    params = MultilangParams(offset_settings, langs=langs)
    status = broadcast(
        API_TS_OFFSET_ROUTE,
        "POST",
        params=params,
        param_name="ts_offset",
        return_status=True,
        method_name="set_ts_offset",
    )

    return status.to_http_status()


@app.route(API_TS_OFFSET_ROUTE, methods=["GET"])
def get_ts_offset():
    """
    Retrieves information about teamspeak sound offset
    :return: {"lang": offset, ...} (note, offset in milliseconds)
    """
    responses = broadcast(API_TS_OFFSET_ROUTE, "GET", params=None, return_status=False)
    data = {}
    for lang, response in responses.items():
        try:
            data[lang] = json.loads(response.text)
        except json.JSONDecodeError:
            data[lang] = "#"

    return json.dumps(data), 200


@app.route(API_TS_VOLUME_ROUTE, methods=["POST"])
def set_ts_volume():
    """
    Query parameters:
    volume_settings: json dictionary,
    e.g. {"lang": 0.0, ...}
    :return:
    """
    volume_settings = request.args.get("volume_settings", None)
    volume_settings = json.loads(volume_settings)

    params = MultilangParams(volume_settings, langs=langs)
    status = broadcast(
        API_TS_VOLUME_ROUTE,
        "POST",
        params=params,
        param_name="volume_db",
        return_status=True,
        method_name="set_ts_volume",
    )

    return status.to_http_status()


@app.route(API_TS_VOLUME_ROUTE, methods=["GET"])
def get_ts_volume():
    """
    Retrieves information about teamspeak sound volume
    :return: {"lang": offset, ...} (note, volume in decibels)
    """
    responses = broadcast(API_TS_VOLUME_ROUTE, "GET", params=None, return_status=False)
    data = {}
    for lang, response in responses.items():
        try:
            data[lang] = json.loads(response.text)
        except json.JSONDecodeError:
            data[lang] = "#"

    return json.dumps(data), 200


@app.route(API_SOURCE_VOLUME_ROUTE, methods=["POST"])
def set_source_volume():
    """
    Query parameters:
    volume_settings: json dictionary,
    e.g. {"lang": 0.0, ...}
    :return:
    """
    volume_settings = request.args.get("volume_settings", None)
    volume_settings = json.loads(volume_settings)

    params = MultilangParams(volume_settings, langs=langs)
    status = broadcast(
        API_SOURCE_VOLUME_ROUTE,
        "POST",
        params=params,
        param_name="volume_db",
        return_status=True,
        method_name="set_source_volume",
    )

    return status.to_http_status()


@app.route(API_SOURCE_VOLUME_ROUTE, methods=["GET"])
def get_source_volume():
    """
    Retrieves information about original source volume
    :return: {"lang": volume, ...} (note, volume in decibels)
    """
    responses = broadcast(API_SOURCE_VOLUME_ROUTE, "GET", params=None, return_status=False)
    data = {}
    for lang, response in responses.items():
        try:
            data[lang] = json.loads(response.text)
        except json.JSONDecodeError:
            data[lang] = "#"

    return json.dumps(data), 200


@app.route(API_SIDECHAIN_ROUTE, methods=["POST"])
def setup_sidechain():
    """
    Query parameters:
    sidechain_settings: json dictionary,
    e.g. {"lang": {'ratio': ..., 'release_time': ..., 'threshold': ...}, ...}
    :return:
    """
    sidechain_settings = request.args.get("sidechain_settings", None)
    sidechain_settings = json.loads(sidechain_settings)

    params = MultilangParams(sidechain_settings, langs=langs)
    status = broadcast(
        API_SIDECHAIN_ROUTE,
        "POST",
        params=params,
        param_name="sidechain_settings",
        return_status=True,
        method_name="setup_sidechain",
    )

    return status.to_http_status()


@app.route(API_TRANSITION_ROUTE, methods=["POST"])
def setup_transition():
    """
    Query parameters:
    transition_settings: json dictionary,
    e.g. {"lang": {'transition_name': ..., 'transition_point': ..., 'path': ..., ...}, ...}
    :return:
    """
    transition_settings = request.args.get("transition_settings", None)
    transition_settings = json.loads(transition_settings)

    params = MultilangParams(transition_settings, langs=langs)
    status = broadcast(
        API_TRANSITION_ROUTE,
        "POST",
        params=params,
        param_name="transition_settings",
        return_status=True,
        method_name="setup_transition",
    )

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

    params = MultilangParams(gdrive_settings, langs=langs)
    status = broadcast(
        API_GDRIVE_SYNC,
        "POST",
        params=params,
        param_name="gdrive_settings",
        return_status=True,
        method_name="setup_gdrive_sync",
    )

    return status.to_http_status()


@app.route(API_GDRIVE_FILES, methods=["GET"])
def get_gdrive_files():
    """
    Retrieves information about google drive files
    Query parameters:
    return_details: "1/0", points if needed to return detailed info for all languages,
     - "0" - returns dict of {"__all__": [... [filename, true/false - loaded/not loaded], ...]}
     - "1" - returns dict of {"lang": [... [filename, true/false - at least loaded on one server (or not)], ...]}
     - default - "0"
    """
    return_details = int(request.args.get("return_details", "0"))

    # fetch data from remote servers
    responses = broadcast(API_GDRIVE_FILES, "GET", params=None, return_status=False)
    data = {}  # dict of {"lang": [... [filename, true/false - loaded/not loaded], ...]}
    for lang, response in responses.items():
        try:
            data[lang] = json.loads(response.text)
        except json.JSONDecodeError:
            data[lang] = "#"

    # result: {"lang": {}, ... } if return_details else {"__all__": {}}
    result = {"__all__": {}}
    if return_details:
        result = {lang: {} for lang in data}

    for lang_, files_ in data.items():
        lang = lang_ if return_details else "__all__"
        for filename, loaded in files_:
            files = result[lang]
            # condition, if the file has been loaded at least on one server
            files[filename] = max(loaded, files[filename]) if filename in files else loaded

    result = {lang: list(files.items()) for lang, files in result.items()}

    return json.dumps(result), 200


@app.route(API_VMIX_PLAYERS, methods=["POST"])
def post_vmix_players():
    """
    Sets vmix ip addresses list
    Query parameters:
     - ip_list - json list of ip. E.g.: ["1.2.3.4", "3.4.5.6"]
    """
    ip_list = request.args.get("ip_list", None)
    if ip_list is None:
        return ExecutionStatus(False, "Please specify `ip_list`").to_http_status()
    try:
        ip_list = json.loads(ip_list)
    except json.JSONDecodeError as ex:
        return ExecutionStatus(False, f"Couldn't parse `ip_list`. Details: {ex}").to_http_status()
    for ip in ip_list:
        if not isinstance(ip, str):
            return ExecutionStatus(False, f"`ip_list` entries should be strings. Got \"{ip}\"").to_http_status()

    vmix_selector.set_ip_list(ip_list)
    return ExecutionStatus(True, "Ok").to_http_status()


@app.route(API_VMIX_PLAYERS, methods=["GET"])
def get_vmix_players():
    """
    Returns current vmix players
    """
    return ExecutionStatus(True, json.dumps(vmix_selector.dump_dict())).to_http_status()


@app.route(API_VMIX_ACTIVE_PLAYER, methods=["POST"])
def post_active_vmix_player():
    """
    Selects active vmix player.
    Query parameters:
     - ip - ip address string
    """
    ip = request.args.get("ip", None)
    if ip is None or not ip:
        return ExecutionStatus(False, "Please specify `ip`").to_http_status()
    try:
        vmix_selector.set_active_ip(ip)
    except BaseException as ex:
        return ExecutionStatus(False, f"Something happened. Details: {ex}").to_http_status()
    return ExecutionStatus(True).to_http_status()


@app.route(API_VMIX_ACTIVE_PLAYER, methods=["GET"])
def get_active_vmix_player():
    """
    Returns current active vmix player
    """
    return ExecutionStatus(True, vmix_selector.get_active_ip()).to_http_status()


@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return '', 200


@app.before_request
def before_request():
    if not wakeup_status:
        if request.path != API_WAKEUP_ROUTE:
            return f"The server is sleeping :) Tell the admin to wake it up."
    else:  # if the server has already woken up
        if not init_status and request.path not in (API_INIT_ROUTE, API_WAKEUP_ROUTE, API_INFO_ROUTE):
            return f"{request.path} is not allowed before initialization"

    if request.path == API_MEDIA_PLAY_ROUTE:
        if request.remote_addr != vmix_selector.get_active_ip():
            return f"This API is not allowed from \"{request.remote_addr}\""


@app.after_request
def after_request(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET,HEAD,OPTIONS,POST,PUT,DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Origin, X-Requested-With, Content-Type, Accept, Authorization"
    return response


@app.errorhandler(Exception)
def server_error(err):
    print(f"E PYSERVER::server_error(): {err}")
    return f"Something happened :(\n\nDetails:\n{err}", 500


class HTTPSThread(threading.Thread):
    def __init__(self, app):
        super().__init__()
        self.app = app

    def run(self) -> None:
        self.app.run("0.0.0.0", COMMON_SERVICE_PORT + 1, ssl_context='adhoc')


_thread = HTTPSThread(app)

if __name__ == "__main__":
    cb_thread.start()
    _thread.start()
    app.run("0.0.0.0", COMMON_SERVICE_PORT)
