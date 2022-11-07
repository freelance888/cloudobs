import json
import os
import re
import threading
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Flask, request

import util.util as util
from googleapi.google_sheets import OBSGoogleSheets, TimingGoogleSheets
from media import server
from media.scheduler import MediaScheduler
from server.minions import Minions
from util.config import (
    API_CLEANUP_ROUTE,
    API_GDRIVE_FILES,
    API_GDRIVE_SYNC,
    API_INFO_ROUTE,
    API_INIT_ROUTE,
    API_MEDIA_PLAY_ROUTE,
    API_MEDIA_SCHEDULE_PULL,
    API_MEDIA_SCHEDULE_ROUTE,
    API_MEDIA_SCHEDULE_SETUP,
    API_MINIONS_DELETE,
    API_PULL_SHEETS,
    API_PUSH_SHEETS,
    API_SET_STREAM_SETTINGS_ROUTE,
    API_SIDECHAIN_ROUTE,
    API_SOURCE_VOLUME_ROUTE,
    API_STREAM_START_ROUTE,
    API_STREAM_STOP_ROUTE,
    API_TRANSITION_ROUTE,
    API_TS_OFFSET_ROUTE,
    API_TS_VOLUME_ROUTE,
    API_VMIX_ACTIVE_PLAYER,
    API_VMIX_PLAYERS,
    API_WAKEUP_ROUTE,
    API_GET_SERVER_STATE,
    API_SOURCE_REFRESH,
)
from util.util import (CallbackThread, ExecutionStatus, MultilangParams, to_seconds, ServerState)
from util.vmix import SourceSelector

load_dotenv()
MEDIA_DIR = os.getenv("MEDIA_DIR")
COMMON_SERVICE_PORT = int(os.getenv("COMMON_SERVICE_PORT", 5000))

# Setup Sentry
# ------------
# if env var set - setup integration #
SENTRY_DSN = os.getenv("SENTRY_DSN")
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
langs: list[str] = []
server_state = ServerState(ServerState.SLEEPING)  #
# init_status, wakeup_status = False, False
cb_thread = CallbackThread()
media_scheduler = MediaScheduler()
sheets = OBSGoogleSheets()
timing_sheets = TimingGoogleSheets()
vmix_selector = SourceSelector()
minions = Minions()


def broadcast(
        api_route,
        http_method,
        params: util.MultilangParams = None,
        param_name="params",
        return_status=False,
        method_name="broadcast",
):
    requests_ = {}  # lang: request
    responses_ = {}  # lang: response

    langs_ = params.list_langs() if params is not None else langs
    # create requests for all langs
    for lang in langs_:
        addr = instance_service_addrs.addr(lang)  # get server address
        request_ = f"{addr}{api_route}"  # create requests
        if params is not None and param_name is not None:  # add query params if needed
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
    elif http_method == "DELETE":
        responses_ = util.async_aiohttp_delete_all(urls=urls)
    elif http_method == "PUT":
        responses_ = util.async_aiohttp_put_all(urls=urls)
    responses_ = {lang: responses_[i] for i, lang in enumerate(requests_.keys())}

    # return status of response or the response itself
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


def after_media_play_triggered(params: MultilangParams, status: ExecutionStatus):
    """
    :param params: media play params which was broadcasted to minions
    :param status: media play status returned from minions
    :return:
    """
    pass


def load_ip_list(path):
    global langs, instance_service_addrs
    instance_service_addrs = util.ServiceAddrStorage()

    with open(path, "rt") as fp:
        text = fp.read()
    ip_list = re.findall(r"^\[(?P<lang>[A-Za-z]+)\]\=(?P<ip>[a-zA-Z0-9\.]+)", text.replace(" ", ""), flags=re.MULTILINE)
    for lang, ip in ip_list:
        instance_service_addrs[lang] = {
            "addr": f"http://{ip}:6000",  # address of instance_service
        }
    langs = [lang for lang, ip in ip_list]


# ===== METHODS TO WORK WITH GOOGLE SHEETS ===== #
def sync_from_sheets(deploy_minions=False, force_deploy_minions=False):
    """
    This function converts google sheets data to api request and
    broadcasts it across all minions
    """
    deploy_minions = ((deploy_minions and server_state.sleeping()) or force_deploy_minions)
    last_state = server_state.get()

    try:
        server_state.set(ServerState.INITIALIZING)
        if deploy_minions:
            try:
                langs = sheets.to_df()["lang"].values
                # ip_list_for_provision - list of [..., [lang, ip], ...], only those ones which have been just deployed
                # and provisioned
                # deploy minions if they haven't been deployed yet
                ip_list = minions.ensure_langs(langs, wait_for_provision=True, provision_timeout=500)
                # wake up those which were just deployed
                wakeup_minions(ip_list)
            except Exception as ex:  # if deployment has failed, delete minions and reraise exception
                server_state.set(ServerState.SLEEPING)
                minions.cleanup()
                raise ex
        # pull broadcast-related parameters from google sheets
        params = sheets.to_multilang_params()
        for lang in params:
            try:
                params[lang][server.SUBJECT_SERVER_LANGS]["host_url"] = instance_service_addrs.addr(lang)
            except KeyError:
                server_state.set(last_state)  # reset state
                return ExecutionStatus(False, f"No minion deployed for lang {lang}")
    except TimeoutError as ex:
        msg_ = f"Provisioning timeout, please check if the deployment server works properly. Details: {ex}"
        print(msg_)
        server_state.set(ServerState.SLEEPING)
        return ExecutionStatus(False, msg_)
    except Exception as ex:
        msg_ = (
            f"Something happened while preparing for synchronization the server from Google Sheets. Details: {ex}"
        )
        print(msg_)
        server_state.set(last_state)  # reset state
        return ExecutionStatus(False, msg_)
    status = broadcast(
        api_route=API_INFO_ROUTE,
        http_method="POST",
        params=params,
        param_name="info",
        return_status=True,
        method_name="init_from_sheets",
    )
    if status:
        server_state.set(ServerState.RUNNING)
    else:
        server_state.set(ServerState.NOT_INITIALIZED)
    return status


def init_from_server_langs(server_langs):
    """
    This function takes in old `server_langs` parameter and broadcasts it across minions.
    Made for old versions compatibility.
    :param server_langs:
    :return:
    """
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


def init_from_sheets(sheet_url, worksheet_name, force_deploy_minions=False):
    """
    This function sets up google sheets object and synchronizes the server considering google sheets data.
    :param sheet_url: url of a public google sheets doc
    :param worksheet_name: a worksheet name
    :return:
    """
    try:
        sheets.set_sheet(sheet_url, worksheet_name)
        return pull_sheets(deploy_minions=True, force_deploy_minions=force_deploy_minions)
    except Exception as ex:
        msg_ = f"Something happened while setting up Google Sheets. Details: {ex}"
        print(msg_)
        return ExecutionStatus(False, msg_)


def get_info(fillna: object = "#"):
    """
    This function fetches `GET /info` from miniones
    :param fillna:
    :return:
    """
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


def pull_sheets(deploy_minions=False, force_deploy_minions=False):
    """
    pull - means pull data from Google Sheets to the server
    """
    if not sheets.ok():
        return ExecutionStatus(False, "Something happened with Google Sheets object")
    try:
        sheets.pull()
    except Exception as ex:
        return ExecutionStatus(False, f"Couldn't pull data from Google Sheets. Details: {ex}")
    return sync_from_sheets(deploy_minions=deploy_minions, force_deploy_minions=force_deploy_minions)


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


def wakeup_minions(iplist):
    """
    :param iplist: list of [..., [lang, ip], ...]
    :return:
    """
    global langs, instance_service_addrs
    # TODO
    instance_service_addrs = util.ServiceAddrStorage()
    for lst in iplist:
        # check the list structure
        if not isinstance(lst, list) or len(lst) != 2:
            return ExecutionStatus(False, "`iplist` entry should also be a list with length of 2")
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
    )  # returns ExecutionStatus

    # the server has woken up
    if status:
        server_state.set(ServerState.NOT_INITIALIZED)
    else:
        server_state.set(ServerState.SLEEPING)
    # global wakeup_status
    # wakeup_status = status.status

    return status


# ========== API ROUTES ========== #


@app.route(API_MINIONS_DELETE, methods=["DELETE"])
def delete_server_minions():
    old_state = server_state.get()
    server_state.set(ServerState.DISPOSING)
    cleanup_status = minions.cleanup()
    if cleanup_status:
        server_state.set(ServerState.SLEEPING)
        # global init_status, wakeup_status
        # init_status = False
        # wakeup_status = False
    else:
        server_state.set(old_state)
    return ExecutionStatus(cleanup_status, "").to_http_status()


@app.route(API_WAKEUP_ROUTE, methods=["POST"])
def wakeup_route():
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
            return ExecutionStatus(False, "`iplist` should be a list object. Please refer to the docs").to_http_status()
    except json.JSONDecodeError as ex:
        return ExecutionStatus(False, f"JSON decode error. Details: {ex}").to_http_status()
    except Exception as ex:
        return ExecutionStatus(False, f"Something happened. Details: {ex}").to_http_status()

    status = wakeup_minions(iplist)

    return status.to_http_status()


@app.route(API_GET_SERVER_STATE, methods=["GET"])
def get_state():
    return server_state.get()
    # if not wakeup_status:
    #    return ServerState.SLEEPING
    # if not init_status:
    #    return ServerState.NOT_INITIALIZED
    # return ServerState.RUNNING


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
    force_deploy_minions: bool
    :return:
    """
    # global init_status
    # init_status = False

    if request.args.get("server_langs", ""):
        return ExecutionStatus("`server_langs` attribute is deprecated. Please use `sheet_url`").to_http_status()
        # server_langs = request.args.get("server_langs")
        # try:
        #    server_langs = json.loads(server_langs)
        # except Exception:
        #    return ExecutionStatus(False, "Couldn't parse json").to_http_status()
        # status = init_from_server_langs(server_langs)
    elif request.args.get("sheet_url", "") and request.args.get("worksheet_name", ""):
        force_deploy_minions = request.args.get("force_deploy_minions", "false")
        force_deploy_minions = json.loads(force_deploy_minions)

        sheet_url = request.args.get("sheet_url")
        worksheet_name = request.args.get("worksheet_name")
        status = init_from_sheets(sheet_url, worksheet_name, force_deploy_minions=force_deploy_minions)
    else:
        return ExecutionStatus(False, "Invalid parameters list").to_http_status()

    return status.to_http_status()


@app.route(API_INIT_ROUTE, methods=["GET"])
def get_init():
    """
    :return:
    """
    if server_state.running():
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

    server_state.set(ServerState.NOT_INITIALIZED)
    # global init_status
    # init_status = False

    return status.to_http_status()


@app.route(API_MEDIA_SCHEDULE_SETUP, methods=["POST"])
def setup_media_schedule():
    """
    Query parameters:
    sheet_url: google sheets url
    sheet_name: google sheet name
    """
    sheet_url = request.args.get("sheet_url", None)
    sheet_name = request.args.get("sheet_name", None)

    if not sheet_url or not sheet_name:
        return ExecutionStatus(False, "Please specify both `sheet_url` and `sheet_name`").to_http_status()

    timing_sheets.set_sheet(sheet_url, sheet_name)
    if not timing_sheets.ok():
        return ExecutionStatus(False, "Something went bad. Couldn't initialize Timing Google sheets").to_http_status()

    return ExecutionStatus(True).to_http_status()


@app.route(API_MEDIA_SCHEDULE_PULL, methods=["POST"])
def pull_media_schedule():
    if not timing_sheets.ok():
        return ExecutionStatus(False, "Please complete Timing Google Sheets initialization first").to_http_status()
    try:
        timing_sheets.pull()

        data = timing_sheets.timing_df.values  # [... [timestamp, name], ...]
        schedule = [[name, timestamp] for timestamp, name in data]

        def foo(id_, name, timestamp, is_enabled, is_played):
            if not is_enabled or is_played:
                return False
            params = MultilangParams({"__all__": {"name": name, "search_by_num": "1"}}, langs=langs)
            try:
                status = broadcast(
                    API_MEDIA_PLAY_ROUTE,
                    "POST",
                    params=params,
                    param_name="params",
                    return_status=True,
                    method_name="media_play",
                )
                try:
                    after_media_play_triggered(params, status)
                except Exception as ex:
                    print(f"after_media_play_triggered: {ex}")
                return True
            except BaseException as ex:
                print(f"E PYSERVER::common_service::media_schedule(): {ex}")
                return False

        status = media_scheduler.create_schedule(schedule=schedule, foo=foo)
        return status.to_http_status()
    except Exception as ex:
        return ExecutionStatus(False, f"Couldn't pull Timing. Details: {ex}").to_http_status()


@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["POST"])
def media_schedule():
    """
    Query parameters:
    delay: in seconds, defaults to 0
    """
    if not timing_sheets.ok():
        return ExecutionStatus(False, "Please complete Timing Google Sheets initialization first").to_http_status()
    if timing_sheets.timing_df is None:
        return ExecutionStatus(False, "Please pull Timing Google Sheets first").to_http_status()

    try:
        delay = request.args.get("delay", "0")
        delay = int(delay)
    except Exception:
        return ExecutionStatus(False, "Couldn't parse `delay` parameter.")

    return media_scheduler.start_schedule(delay=delay).to_http_status()


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
    if not timing_sheets.ok():
        return ExecutionStatus(False, "Please complete Timing Google Sheets initialization first").to_http_status()
    if timing_sheets.timing_df is None:
        return ExecutionStatus(False, "Please pull Timing Google Sheets first").to_http_status()
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
    if not timing_sheets.ok():
        return ExecutionStatus(False, "Please complete Timing Google Sheets initialization first").to_http_status()
    if timing_sheets.timing_df is None:
        return ExecutionStatus(False, "Please pull Timing Google Sheets first").to_http_status()

    id_ = request.args.get("id", None)
    if id_ is None:
        return ExecutionStatus(False, message="Please specify schedule id").to_http_status()
    try:
        id_ = int(id_)
    except BaseException:
        return ExecutionStatus(False, message="Invalid `id`")
    name = request.args.get("name", None)
    timestamp = request.args.get("timestamp", None)
    is_enabled = request.args.get("is_enabled", None)

    assert is_enabled is None or is_enabled in ("true", "false")
    if is_enabled is not None:
        is_enabled = json.loads(is_enabled)

    assert timestamp is None or bool(re.fullmatch(r"\d{1,2}\:\d{2}\:\d{2}", timestamp))  # 00:01:59
    if timestamp is not None:
        timestamp = to_seconds(timestamp)

    status = media_scheduler.modify_schedule(id_=id_, name=name, timestamp=timestamp, is_enabled=is_enabled)

    return status.to_http_status()


@app.route(API_MEDIA_SCHEDULE_ROUTE, methods=["DELETE"])
def delete_media_schedule():
    if not timing_sheets.ok():
        return ExecutionStatus(False, "Please complete Timing Google Sheets initialization first").to_http_status()
    if timing_sheets.timing_df is None:
        return ExecutionStatus(False, "Please pull Timing Google Sheets first").to_http_status()
    broadcast(API_MEDIA_PLAY_ROUTE, "DELETE", return_status=True, method_name="media_play")
    return media_scheduler.delete_schedule().to_http_status()


@app.route(API_MEDIA_PLAY_ROUTE, methods=["POST"])
def media_play():
    """
    Query parameters:
    params: json dictionary,
        structure: {"name": "...", "search_by_num": "0/1", "mode": "force"}
            mode - media play mode. Possible values:
                 - "force" - stop any media being played right now, and play media specified (default value)
                 - "check_any" - if any video is being played, skip
                 - "check_same" - if the same video is being played, skip, otherwise play
    :return:
    """
    params = request.args.get("params", None)
    params = json.loads(params)

    params = MultilangParams(params, langs=langs)
    status = broadcast(
        API_MEDIA_PLAY_ROUTE, "POST", params=params, param_name="params", return_status=True, method_name="media_play"
    )

    try:
        after_media_play_triggered(params, status)
    except Exception as ex:
        print(f"after_media_play_triggered: {ex}")

    return status.to_http_status()


@app.route(API_MEDIA_PLAY_ROUTE, methods=["DELETE"])
def media_play_delete():
    """
    Stops any media played
    :return:
    """

    status = broadcast(API_MEDIA_PLAY_ROUTE, "DELETE", return_status=True, method_name="media_play")

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


@app.route(API_SOURCE_REFRESH, methods=["PUT"])
def refresh_source():
    """
    This API refreshes media sources for specified langs.
    Query parameters:
    langs: json list of langs,
    e.g. ["eng", "rus"], or ["__all__"] (default)
    :return:
    """
    _langs = request.args.get("langs", json.dumps(["__all__"]))
    _langs = json.loads(_langs)

    params = MultilangParams({_: {} for _ in _langs}, langs=langs)
    status = broadcast(API_SOURCE_REFRESH, "PUT", params=params, return_status=True, method_name="refresh_source")

    return status.to_http_status()


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
        if files_ == "#":
            continue
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
     - `ip_list` - list of dicts with the format `{"ip": "...", "label": "..."}`,
     e.g.:
    ```
    [
       {"ip": "1.2.3.4", "label": "Локация 1"},
       {"ip": "1.2.3.5", "label": "Локация 2"},
       {"ip": "1.2.3.6", "label": "Локация 3"}
    ]
    ```
    """
    ip_list = request.args.get("ip_list", None)
    if ip_list is None:
        return ExecutionStatus(False, "Please specify `ip_list`").to_http_status()
    try:
        ip_list = json.loads(ip_list)
    except json.JSONDecodeError as ex:
        return ExecutionStatus(False, f"Couldn't parse `ip_list`. Details: {ex}").to_http_status()

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


@app.route("/healthcheck", methods=["GET"])
def healthcheck():
    return "", 200


@app.before_request
def before_request():
    if server_state.sleeping():
        if request.path not in (API_WAKEUP_ROUTE, API_INIT_ROUTE, API_GET_SERVER_STATE):
            return "The server is sleeping :) Tell the admin to wake it up."
    else:  # if the server has already woken up
        if not server_state.running() and request.path not in \
                (API_INIT_ROUTE, API_WAKEUP_ROUTE, API_INFO_ROUTE, API_GET_SERVER_STATE):
            return f"{request.path} is not allowed before initialization"

    if request.path == API_MEDIA_PLAY_ROUTE or \
            (request.path == API_MEDIA_SCHEDULE_ROUTE and request.method == "POST"):
        if not vmix_selector.is_allowed(request.remote_addr):
            return f'This API is not allowed from "{request.remote_addr}"'


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
        self.app.run("0.0.0.0", COMMON_SERVICE_PORT + 1, ssl_context="adhoc")


_thread = HTTPSThread(app)

if __name__ == "__main__":
    cb_thread.start()
    _thread.start()
    app.run("0.0.0.0", COMMON_SERVICE_PORT)
