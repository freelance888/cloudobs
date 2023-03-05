import asyncio
import hashlib
import json
import re
import sys
import threading
import time
from enum import Enum
from threading import Lock

import aiohttp
from asgiref import sync


def async_aiohttp_get_all(urls):
    """
    performs asynchronous get requests
    """

    async def get_all(urls):
        async with aiohttp.ClientSession() as session:
            async def fetch(url):
                async with session.get(url) as response:
                    return Response(await response.text(), response.status)

            return await asyncio.gather(*[fetch(url) for url in urls])

    # call get_all as a sync function to be used in a sync context
    return sync.async_to_sync(get_all)(urls)


def async_aiohttp_post_all(urls):
    """
    performs asynchronous get requests
    """

    async def get_all(urls):
        async with aiohttp.ClientSession() as session:
            async def fetch(url):
                async with session.post(url) as response:
                    return Response(await response.text(), response.status)

            return await asyncio.gather(*[fetch(url) for url in urls])

    # call get_all as a sync function to be used in a sync context
    return sync.async_to_sync(get_all)(urls)


def async_aiohttp_delete_all(urls):
    """
    performs asynchronous get requests
    """

    async def get_all(urls):
        async with aiohttp.ClientSession() as session:
            async def fetch(url):
                async with session.delete(url) as response:
                    return Response(await response.text(), response.status)

            return await asyncio.gather(*[fetch(url) for url in urls])

    # call get_all as a sync function to be used in a sync context
    return sync.async_to_sync(get_all)(urls)


def async_aiohttp_put_all(urls):
    """
    performs asynchronous get requests
    """

    async def get_all(urls):
        async with aiohttp.ClientSession() as session:
            async def fetch(url):
                async with session.put(url) as response:
                    return Response(await response.text(), response.status)

            return await asyncio.gather(*[fetch(url) for url in urls])

    # call get_all as a sync function to be used in a sync context
    return sync.async_to_sync(get_all)(urls)


def validate_init_params(server_langs):
    try:
        for lang, lang_info in server_langs.items():
            for attr in ["host_url", "websocket_port", "password", "original_media_url"]:
                if attr not in lang_info:
                    return ExecutionStatus(status=False, message=f"Please specify `{attr}` attribute for lang '{lang}'")
            websockets_port = lang_info["websocket_port"]
            # TODO: validate `obs_host`
            if not str(websockets_port).isdigit():
                return ExecutionStatus(status=False, message="`websocket_port` must be a number")
            # TODO: validate original_media_url
    except Exception:
        return ExecutionStatus(status=False, message="Invalid `server_langs` format")
    return ExecutionStatus(status=True)


def validate_media_play_params(name, use_file_num):
    if use_file_num not in ("0", "1"):
        return ExecutionStatus(status=False, message="`search_by_num` should be in range of '0' or '1'")

    if not name:
        return ExecutionStatus(status=False, message="`name` must not be empty")

    return ExecutionStatus(status=True)


def generate_file_md5(filename, blocksize=2 ** 25):
    m = hashlib.md5()
    with open(filename, "rb") as f:
        while True:
            buf = f.read(blocksize)
            if not buf:
                break
            m.update(buf)
    return m.hexdigest()


def log(text):
    print(text)
    sys.stdout.flush()


class Response:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


class ServiceAddrStorage:
    """
    TODO: save/load config to/from disk
    """

    def __init__(self):
        self.dct = {}

    def __getitem__(self, item):
        return self.dct[item]

    def __setitem__(self, key, value):
        self.dct[key] = value

    def __iter__(self):
        return self.dct.__iter__()

    def items(self):
        return self.dct.items()

    def addr(self, lang):
        if lang not in self.dct:
            raise KeyError(lang)
        return self.dct[lang]["addr"]


class MultilangParams:
    def __init__(self, params_dict, langs=None):
        self.params_dict = params_dict
        self.langs = langs
        self.all_langs = (len(params_dict) == 1) and ("__all__" in params_dict)

    def __getitem__(self, item):
        if self.all_langs:
            return self.params_dict["__all__"]
        return self.params_dict[item]

    def __setitem__(self, key, value):
        if self.all_langs:
            raise NotImplementedError()
        self.params_dict[key] = value

    def __iter__(self):
        return self.params_dict.__iter__()

    def items(self):
        return self.params_dict.items()

    def list_langs(self):
        if self.all_langs:
            return self.langs
        return list(self.params_dict.keys())


class ExecutionStatus:
    def __init__(self, status=True, message="", serializable_object=None):
        """
        :param status:
        :param message:
        :param json_result:
        """
        self.status = status
        if isinstance(message, list):
            self.message = message
        else:
            self.message = [message]
        self.serializable_object = serializable_object

        if re.match(r"\d{3}$", str(status)):
            self._type = "http"
        else:
            self._type = ""

    def __bool__(self):
        if self._type == "http":
            return bool(self.status) and str(self.status)[:1] == "2"
        else:
            return bool(self.status)

    def append_warning(self, message):
        self.message.append(message)

    def append_error(self, message):
        self.message.append(message)
        self.status = False

    def to_http_status(self):
        """
        Returns http perspective of the status:
        `(message, code)`
        """
        code = 200 if self.__bool__() else 500

        if self.message:
            message = "\n".join(self.message)
        else:
            message = "Ok"

        return message, code

    def json(self):
        """
        Converts the ExecutionStatus into json string.
        Has the following structure:
        {
            "result": True/False,
            "details": "message",
            "serializable_object": some_serializable_object
        }
        :return:
        """
        # if there is only one message, leave it as string
        if isinstance(self.message, list):
            if len(self.message) == 1:
                message = self.message[0]
            else:
                message = self.message
        else:
            message = self.message

        return json.dumps(
            {"result": self.__bool__(), "details": message, "serializable_object": self.serializable_object}
        )

    def dict(self):
        # if there is only one message, leave it as string
        if isinstance(self.message, list):
            if len(self.message) == 1:
                message = self.message[0]
            else:
                message = self.message
        else:
            message = self.message

        return {"result": self.__bool__(), "details": message, "serializable_object": self.serializable_object}

    @classmethod
    def from_json(cls, json_string: str):
        obj = json.loads(json_string)

        return ExecutionStatus(
            status=obj["result"],
            message=obj["details"],
            serializable_object=(obj["serializable_object"] if "serializable_object" in obj else None),
        )


class DefaultDict:
    def __init__(self, dict):
        self.dict = dict

    def __setitem__(self, key, value):
        if key not in self.dict:
            raise KeyError("No new keys allowed")
        else:
            self.dict[key] = value

    def __getitem__(self, item):
        return self.dict[item]

    def keys(self):
        return self.dict.keys()

    def values(self):
        return self.dict.values()

    def items(self):
        return self.dict.items()

    def to_dict(self):
        return self.dict.copy()


class GDriveFiles:
    def __init__(self, with_lock=False):
        self.filenames = dict()  # filename: bool - loaded
        self.with_lock = with_lock
        if self.with_lock:
            self._lock = threading.Lock()

    def __setitem__(self, key, value):
        if not self.with_lock:
            self.filenames[key] = value
        with self._lock:
            self.filenames[key] = value

    def __getitem__(self, item):
        if not self.with_lock:
            return self.filenames[item]
        with self._lock:
            return self.filenames[item]

    def __iter__(self):
        self._n = 0
        self._items = list(self.filenames.keys())
        return self

    def __next__(self):
        if self._n < len(self.filenames):
            result = self._items[self._n]  # (filename, b_state)
            self._n += 1
            return result
        else:
            raise StopIteration

    def items(self):
        return self.filenames.items()


class CallbackThread(threading.Thread):
    def __init__(self):
        self.lock = threading.RLock()
        self.callbacks = []  # list of {"foo": foo, "args": args, "delay": delay}, note: delay in seconds
        self.running = True
        threading.Thread.__init__(self)

    def append_callback(self, foo, delay, args=None, cb_type="none"):
        """
        :param foo:
        :param delay: delay in seconds, or callable
        :return:
        """
        with self.lock:
            self.callbacks.append(
                {
                    "foo": foo,
                    "delay": delay,
                    "args": args,
                    "__time__": time.time(),
                    "__done__": False,
                    "cb_type": cb_type,
                }
            )

    def clean_callbacks(self):
        with self.lock:
            self.callbacks = []

    def delete_cb_type(self, cb_type):
        with self.lock:
            self.callbacks = [cb for cb in self.callbacks if cb["cb_type"] != cb_type]

    def run(self):
        while self.running:
            self._check_callbacks()
            time.sleep(0.01)

    def _check_callbacks(self):
        with self.lock:
            for cb in self.callbacks.copy():
                self._check_callback(cb)
            self.callbacks = [cb for cb in self.callbacks if not cb["__done__"]]

    def _check_callback(self, cb):
        if cb["__done__"]:
            return
        if callable(cb["delay"]):
            delay = cb["delay"]()
        else:
            delay = cb["delay"]
        if (time.time() - cb["__time__"]) >= delay:
            self._invoke(cb)
            cb["__done__"] = True

    def _invoke(self, cb):
        try:
            foo, args = cb["foo"], cb["args"]
            if args is not None:
                foo(*args)
            else:
                foo()
        except BaseException as ex:
            print(f"E PYSERVER::CallbackThread::_invoke(): {ex}")


class ServerState:
    SLEEPING = "sleeping"
    NOT_INITIALIZED = "not initialized"
    INITIALIZING = "initializing"
    RUNNING = "running"
    DISPOSING = "disposing"

    def __init__(self, state):
        assert state in (
            ServerState.SLEEPING,
            ServerState.NOT_INITIALIZED,
            ServerState.INITIALIZING,
            ServerState.RUNNING,
            ServerState.DISPOSING,
        )
        self.state = state
        self.lock = Lock()

    def set(self, state):
        assert state in (
            ServerState.SLEEPING,
            ServerState.NOT_INITIALIZED,
            ServerState.INITIALIZING,
            ServerState.RUNNING,
            ServerState.DISPOSING,
        )
        with self.lock:
            self.state = state

    def get(self):
        with self.lock:
            return self.state

    def sleeping(self):
        return self.state == ServerState.SLEEPING

    def not_initialized(self):
        return self.state == ServerState.NOT_INITIALIZED

    def initializing(self):
        return self.state == ServerState.INITIALIZING

    def running(self):
        return self.state == ServerState.RUNNING

    def disposing(self):
        return self.state == ServerState.DISPOSING


class WebsocketResponse:
    @classmethod
    def wait_for(cls, responses):
        if not responses:
            return responses
        while not all([r.done() for r in responses]):
            time.sleep(0.1)
        return responses

    def __init__(self, timeout=5.0):
        self.t = time.time()
        self.timeout = timeout
        self.response = None
        self._done = False

    def callback(self, *data):
        self.response = data
        if isinstance(self.response, tuple) and len(self.response) == 1:
            self.response = self.response[0]
        self._done = True

    def done(self):
        return self._done or (time.time() - self.t) >= self.timeout

    def result(self):
        return self.response


class LogLevel(Enum):
    info = 1
    warn = 2
    error = 3


class LogType(Enum):
    command_started = (LogLevel.info, 'Command Started')
    command_completed = (LogLevel.info, 'Command Completed')
    skipper_error = (LogLevel.error, 'Skipper Error')
    minion_error = (LogLevel.error, 'Minion Error')
