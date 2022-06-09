import re
import threading
import time

import aiohttp
import asyncio
from asgiref import sync


class Response:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


def async_aiohttp_get_all(urls):
    """
    performs asynchronous get requests
    """

    async def get_all(urls):
        async with aiohttp.ClientSession() as session:
            async def fetch(url):
                async with session.get(url) as response:
                    return Response(await response.text(), response.status)

            return await asyncio.gather(*[
                fetch(url) for url in urls
            ])

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

            return await asyncio.gather(*[
                fetch(url) for url in urls
            ])

    # call get_all as a sync function to be used in a sync context
    return sync.async_to_sync(get_all)(urls)


def validate_init_params(server_langs):
    for lang, lang_info in server_langs.items():
        for attr in ["host_url", "websocket_port", "password", "original_media_url"]:
            if attr not in lang_info:
                return ExecutionStatus(status=False, message=f"Please specify `{attr}` attribute for lang '{lang}'")
        websockets_port = lang_info["websocket_port"]
        # TODO: validate `obs_host`
        if not str(websockets_port).isdigit():
            return ExecutionStatus(status=False, message="`websocket_port` must be a number")
        # TODO: validate original_media_url
    return ExecutionStatus(status=True)


def validate_media_play_params(name, use_file_num):
    if use_file_num not in ("0", "1"):
        return ExecutionStatus(status=False, message="`search_by_num` should be in range of '0' or '1'")

    if not name:
        return ExecutionStatus(status=False, message="`name` must not be empty")

    return ExecutionStatus(status=True)


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
        return self.dct[lang]["addr"]


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
    def __init__(self, status=True, message=""):
        self.status = status
        self.message = message

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
        if self.message:
            self.message += "\n-----\n"
        self.message += message
        self.status = False

    def append_error(self, message):
        if self.message:
            self.message += "\n-----\n"
        self.message += message
        self.status = False

    def to_http_status(self):
        """
        Returns http perspective of the status:
        `(message, code)`
        """
        code = 200 if self.__bool__() else 500
        msg = "Ok" if code == 200 and not self.message else self.message
        return msg, code


class CallbackThread(threading.Thread):
    def __init__(self):
        self.lock = threading.Lock()
        self.callbacks = []  # list of {"foo": foo, "args": args, "delay": delay}, note: delay in seconds
        self.running = True
        threading.Thread.__init__(self)

    def append_callback(self, foo, delay, args=None, cb_type="none"):
        """
        :param foo:
        :param delay: delay in seconds
        :return:
        """
        with self.lock:
            self.callbacks.append({
                "foo": foo, "delay": delay, "args": args,
                "__time__": time.time(), "__done__": False, "cb_type": cb_type
            })

    def clean_callbacks(self):
        with self.lock:
            self.callbacks = []

    def delete_cb_type(self, cb_type):
        with self.lock:
            self.callbacks = [
                cb for cb in self.callbacks if cb["cb_type"] != cb_type
            ]

    def run(self):
        while self.running:
            self._check_callbacks()
            time.sleep(0.01)

    def _check_callbacks(self):
        for cb in self.callbacks.copy():
            self._check_callback(cb)
        with self.lock:
            self.callbacks = [cb for cb in self.callbacks if not cb["__done__"]]

    def _check_callback(self, cb):
        if cb["__done__"]:
            return
        if (time.time() - cb["__time__"]) >= cb["delay"]:
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

class DefaultDict:
    def __init__(self, dict):
        self.dict = dict

    def __setitem__(self, key, value):
        if key not in self.dict:
            raise KeyError('No new keys allowed')
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

