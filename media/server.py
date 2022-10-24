import glob
import os
import re
from urllib.parse import urlencode

import obswebsocket as obsws
import requests
from dotenv import load_dotenv

from media import obs
from util.util import ExecutionStatus

load_dotenv()
BASE_MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
DEFAULT_API_KEY = os.getenv("GDRIVE_API_KEY", "")
DEFAULT_SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 60))
MEDIA_DIR = os.path.join(BASE_MEDIA_DIR, "media")
TRANSITION_DIR = os.path.join(BASE_MEDIA_DIR, "media")

SUBJECT_SERVER_LANGS = "server_langs"
SUBJECT_STREAM_SETTINGS = "stream_settings"
SUBJECT_MEDIA_SCHEDULE = "media_schedule"
SUBJECT_TS_OFFSET = "ts_offset"
SUBJECT_TS_VOLUME = "ts_volume"
SUBJECT_SOURCE_VOLUME = "source_volume"
SUBJECT_SIDECHAIN = "sidechain"
SUBJECT_TRANSITION = "transition"
SUBJECT_GDRIVE_SETTINGS = "gdrive_settings"
SUBJECT_STREAM_ON = "stream_on"


class ServerSettings:
    @staticmethod
    def default_dict():
        return {
            SUBJECT_SERVER_LANGS: {
                "obs_host": "localhost",
                "host_url": "",
                "websocket_port": 4439,
                "password": "",
                "original_media_url": "",
                "objvers": "",
            },
            SUBJECT_STREAM_SETTINGS: {
                "server": "",
                "key": "",
                "objvers": "",
            },
            SUBJECT_STREAM_ON: {
                "value": False,
                "objvers": "",
            },
            SUBJECT_MEDIA_SCHEDULE: {
                "value": None,  # list of [id, name, timestamp, is_enabled, is_played]
                "objvers": "",
            },
            SUBJECT_TS_OFFSET: {
                "value": 6000,
                "objvers": "M",
            },
            SUBJECT_TS_VOLUME: {
                "value": 0,
                "objvers": "M",
            },
            SUBJECT_SOURCE_VOLUME: {
                "value": 0,
                "objvers": "M",
            },
            SUBJECT_SIDECHAIN: {
                "ratio": 32,
                "release_time": 1000,
                "threshold": -30.0,
                "output_gain": -10.0,
                "objvers": "M",
            },
            SUBJECT_TRANSITION: {
                "transition_name": "Cut",
                "path": "",
                "transition_point": 6500,
                "objvers": "M",
            },
            SUBJECT_GDRIVE_SETTINGS: {
                "drive_id": "",
                "media_dir": "",
                "api_key": "",
                "sync_seconds": 0,
                "gdrive_sync_addr": "",
                "objvers": "",
            },
        }

    @staticmethod
    def default():
        return ServerSettings()

    def __init__(self):
        self._settings = ServerSettings.default_dict()
        self.subjects = list(self._settings.keys())

    def set(self, subject, attribute, value):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        if attribute not in self._settings[subject]:
            raise KeyError(f'Invalid subject attribute "{subject}.{attribute}"')
        if attribute == "objvers":
            raise KeyError('Explicit "objvers" modification is not allowed')

        if self._settings[subject][attribute] == value:  # if the value is the same
            pass
        else:
            self._settings[subject][attribute] = value
            self._settings[subject]["objvers"] = "M"

    def get_subject(self, subject):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        subject = self._settings[subject].copy()
        subject.pop("objvers")
        return subject

    def get(self, subject, attribute):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        if attribute not in self._settings[subject]:
            raise KeyError(f'Invalid subject attribute "{subject}.{attribute}"')
        if attribute == "objvers":
            raise KeyError('"objvers" access is not allowed')
        return self._settings[subject][attribute]

    def to_dict(self):
        result = {subject: self.get_subject(subject) for subject in self.subjects}
        result[SUBJECT_SERVER_LANGS].pop("obs_host")
        return result

    def is_modified(self, subject):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        return self._settings[subject]["objvers"] == "M"

    def is_active(self, subject):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        return self._settings[subject]["objvers"] == "A"

    def activate(self, subject):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        self._settings[subject]["objvers"] = "A"

    def deactivate(self, subject):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        self._settings[subject]["objvers"] = ""

    def modify_subject(self, subject):
        if subject not in self._settings:
            raise KeyError(f'Invalid subject "{subject}"')
        self._settings[subject]["objvers"] = "M"


class Server:
    def __init__(self):
        self.settings = ServerSettings()

        self.obs_instance: obs.OBS = None
        self.obs_client = None
        self.obs_connected = False
        self.is_initialized = False

        self.media_dir = MEDIA_DIR

    def set_info(self, info):
        status = ExecutionStatus(status=True)
        try:
            for subject, data in info.items():  # for each subject
                for k, v in data.items():  # for each key-value pair
                    self.settings.set(subject, k, v)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::set_info(): couldn't activate settings. Details: {ex}"
            status.append_error(msg_)
            print(msg_)
        return status

    def initialize(self, server_langs):
        """
        establish connections, initialize obs controllers, setup scenes, create original media sources
        :param server_langs:
            {
                "obs_host": "localhost",
                "host_url": ...,
                "websocket_port": 1234,
                "password": "qwerty123",
                "original_media_url": "srt://localhost"
            }
        :return: Status
        """
        status = ExecutionStatus(status=True)
        try:
            for k, v in server_langs.items():
                self.settings.set(SUBJECT_SERVER_LANGS, k, v)
            self.activate()
        except Exception as ex:
            msg_ = f"E PYSERVER::Server::initialize(), details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def cleanup(self):
        self.stop_streaming()  # no need to check status
        self._reset_scenes()
        self.settings.deactivate(SUBJECT_SERVER_LANGS)
        # self.drop_connections()

    def drop_connections(self):
        if self.obs_client is not None:
            try:
                self.obs_client.disconnect()
            except Exception as ex:
                print(f"PYSERVER::Server::drop_connections(): {ex}")
        self.obs_connected = False

    def schedule_media(self, schedule):
        """
        :param schedule: dictionary of [..., [path, timestamp], ...]
         - path - media name
         - timestamp - relative timestamp in milliseconds
        """
        # TODO: activate
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.obs_instance.schedule_media(schedule)
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::schedule_media(): couldn't schedule media. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def run_media(self, params):
        """
        :param params: json dictionary,
        e.g. {"name": "...", "search_by_num": "0/1"}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        use_file_num, name = params["search_by_num"], params["name"]
        media_type = params["media_type"] if "media_type" in params else "media"
        media_dir = os.path.join(self.media_dir, "media")

        # search for the file
        if use_file_num:
            # extract file number
            file_num = re.search(r"^\d+", name)
            if not file_num:  # if the pattern is incorrect (name doesn't start with numbers)
                msg_ = (
                    f"W PYSERVER::Server::run_media(): while `use_file_num` is set, "
                    f"`name` doesn't start with a number. name {name}"
                )
                print(msg_)
                status.append_error(msg_)
                return status
            else:
                file_num = file_num.group()

                files = glob.glob(os.path.join(media_dir, f"{file_num}*"))
                if len(files) == 0:
                    msg_ = f"W PYSERVER::Server::run_media(): no media found, name {name}"
                    print(msg_)
                    status.append_error(msg_)
                    return status
                else:
                    path = files[0]
        else:
            path = os.path.join(media_dir, name)
            if not os.path.isfile(path):
                msg_ = f"W PYSERVER::Server::run_media(): no media found with name specified, name {name}"
                print(msg_)
                status.append_warning(msg_)
                return status

        try:
            self.obs_instance.run_media(path, media_type=media_type)
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::run_media(): couldn't play media. Details: {ex}"
            print(msg_)
            status.append_error(msg_)

        return status

    def stop_media(self):
        """
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.obs_instance.stop_media()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::run_media(): couldn't stop media. Details: {ex}"
            print(msg_)
            status.append_error(msg_)

        return status

    def set_stream_settings(self, stream_settings):
        """
        :param stream_settings: dictionary,
        e.g. {"server": "rtmp://...", "key": "..."}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.settings.set(SUBJECT_STREAM_SETTINGS, attribute="server", value=stream_settings["server"])
            self.settings.set(SUBJECT_STREAM_SETTINGS, attribute="key", value=stream_settings["key"])
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::set_stream_settings(): couldn't set stream settings. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def get_ts_sync_offset(self):
        """
        Retrieves information about teamspeak audio sync offset
        :return: offset_int (note, offset in milliseconds)
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        try:
            return self.settings.get(SUBJECT_TS_OFFSET, "value")
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::get_ts_sync_offset(): couldn't get sync offset. Details: {ex}"
            print(msg_)  # TODO: logging methods
            return "#"

    def set_ts_sync_offset(self, ts_offset):
        """
        :param ts_offset:
        e.g. 4000 (note, offset in milliseconds)
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.settings.set(SUBJECT_TS_OFFSET, attribute="value", value=ts_offset)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::set_ts_sync_offset(): couldn't set ts_offset. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def get_ts_volume_db(self):
        """
        Retrieves teamspeak sound volume (in decibels)
        :return: volume_db
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        try:
            return self.settings.get(SUBJECT_TS_VOLUME, "value")
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::get_ts_volume_db(): couldn't get ts volume_db. Details: {ex}"
            print(msg_)  # TODO: logging methods
            return "#"

    def set_ts_volume_db(self, volume_db):
        """
        :param volume_db: volume (in decibels),
        e.g. 0.0
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.settings.set(SUBJECT_TS_VOLUME, attribute="value", value=volume_db)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::set_ts_volume_db(): couldn't set ts volume_db. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def get_source_volume_db(self):
        """
        Retrieves original source sound volume (in decibels)
        :return: volume_db
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        try:
            return self.settings.get(SUBJECT_SOURCE_VOLUME, "value")
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::get_source_volume_db(): couldn't get source volume_db. Details: {ex}"
            print(msg_)  # TODO: logging methods
            return "#"

    def set_source_volume_db(self, volume_db):
        """
        :param volume_db: volume (in decibels),
        e.g. 0.0
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.settings.set(SUBJECT_SOURCE_VOLUME, attribute="value", value=volume_db)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::set_source_volume_db(): couldn't set volume_db. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def get_sidechain(self):
        """
        Retrieves original source sound volume (in decibels)
        :return: sidechain_settings,
        e.g. {'ratio': ..., 'release_time': ..., 'threshold': ...}
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        try:
            return self.settings.get_subject(SUBJECT_SIDECHAIN)
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::get_sidechain(): couldn't get sidechain_settings. Details: {ex}"
            print(msg_)  # TODO: logging methods
            return "#"

    def setup_sidechain(self, sidechain_settings):
        """
        :param sidechain_settings: sidechain settings dictionary,
        e.g. {'ratio': ..., 'release_time': ..., 'threshold': ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            for k, v in sidechain_settings.items():
                self.settings.set(SUBJECT_SIDECHAIN, attribute=k, value=v)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::setup_sidechain(): couldn't set sidechain_settings. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def get_transition(self):
        """
        Retrieves current transition info
        :return: transition_settings,
        e.g. {'transition_name': ..., 'audio_fade_style': ..., 'path': ..., ...}
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        try:
            return self.settings.get_subject(SUBJECT_TRANSITION)
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::get_transition(): couldn't get transition_settings. Details: {ex}"
            print(msg_)  # TODO: logging methods
            return "#"

    def setup_transition(self, transition_settings):
        """
        :param transition_settings: transition settings dictionary,
        e.g. {'transition_name': ..., 'audio_fade_style': ..., 'path': ..., ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            for k, v in transition_settings.items():
                self.settings.set(SUBJECT_TRANSITION, attribute=k, value=v)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::setup_transition(): couldn't set transition_settings. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def get_streaming(self):
        """
        Retrieves current streaming state
        :return: bool,
        e.g. False
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        try:
            return self.settings.get(SUBJECT_STREAM_ON, "value")
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::get_streaming(): couldn't get streaming state. Details: {ex}"
            print(msg_)  # TODO: logging methods
            return "#"

    def start_streaming(self):
        """
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.settings.set(SUBJECT_STREAM_ON, attribute="value", value=True)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::start_streaming(): couldn't set streaming state. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def stop_streaming(self):
        """
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            self.settings.set(SUBJECT_STREAM_ON, attribute="value", value=False)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::stop_streaming(): couldn't set streaming state. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def setup_gdrive(self, gdrive_settings):
        """
        :param gdrive_settings: gdrive settings dictionary,
        e.g. {'drive_id': ..., 'media_dir': ..., 'api_key': ..., 'sync_seconds': ..., gdrive_sync_addr: ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        try:
            for k, v in gdrive_settings.items():
                self.settings.set(SUBJECT_GDRIVE_SETTINGS, attribute=k, value=v)
            self.activate()
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::setup_gdrive(): couldn't set gdrive_settings. Details: {ex}"
            print(msg_)
            status.append_error(msg_)
        return status

    def set_media_dir(self, media_dir):
        """
        :param dir_settings
        :return:
        """
        self.media_dir = media_dir
        if not os.path.isdir(self.media_dir):
            os.system(f"mkdir -p {self.media_dir}")

    def _establish_connections(self, verbose=True):
        """
        establish obs connection
        :return: True/False
        """
        # create obs ws clients
        lang_info = self.settings.get_subject(SUBJECT_SERVER_LANGS)
        status = ExecutionStatus(status=True)

        # establish connections
        try:
            if not self.obs_connected:
                self.obs_client = obsws.obsws(host=lang_info["obs_host"], port=int(lang_info["websocket_port"]))
                self.obs_client.connect()
                self.obs_connected = True
        except BaseException as ex:
            msg_ = (
                "E PYSERVER::Server::_establish_connections(): Couldn't connect to obs server. "
                f"Host '{lang_info['obs_host']}', "
                f"port {lang_info['websocket_port']}. Details: {ex}"
            )
            if verbose:
                print(msg_)
            status.append_error(msg_)

        return status

    def _initialize_obs_controllers(self, verbose=True):
        """
        Creates obs controller instance and set up basic scenes
        """
        # create obs controller instances
        self.obs_instance = obs.OBS(self.obs_client)

        lang_info = self.settings.get_subject(SUBJECT_SERVER_LANGS)
        status = ExecutionStatus(status=True)

        # reset scenes, create original media sources
        try:
            self.obs_instance.clear_all_scenes()
            self.obs_instance.setup_scene(scene_name=obs.MAIN_SCENE_NAME)
            self.obs_instance.set_original_media_source(
                scene_name=obs.MAIN_SCENE_NAME, original_media_source=lang_info["original_media_url"]
            )
            self.obs_instance.setup_ts_sound()
        except BaseException as ex:
            msg_ = (
                f"E PYSERVER::Server::_initialize_obs_controllers(): Couldn't initialize obs controller. "
                f"Host '{lang_info['obs_host']}', "
                f"port {lang_info['websocket_port']}. Details: {ex}"
            )
            if verbose:
                print(msg_)
            status.append_error(msg_)

        return status

    def _reset_scenes(self, verbose=True):
        status = ExecutionStatus(status=True)

        # reset scenes, create original media sources
        try:
            self.obs_instance.clear_all_scenes()
            self.obs_instance.setup_scene(scene_name=obs.MAIN_SCENE_NAME)
        except BaseException as ex:
            msg_ = f"E PYSERVER::Server::_reset_scenes(): Couldn't reset scenes. Details: {ex}"
            if verbose:
                print(msg_)
            status.append_error(msg_)

        return status

    def activate(self):
        self.activate_server_langs()
        if not self.settings.is_active(SUBJECT_SERVER_LANGS):
            raise RuntimeError("E PYSERVER::Server::activate(): attempt to activate config without initialization")
        self.activate_stream_settings()
        self.activate_stream_on()
        # TODO: SCHEDULE
        self.activate_ts_offset()
        self.activate_ts_volume()
        self.activate_source_volume()
        self.activate_sidechain()
        self.activate_transition()
        self.activate_gdrive()

    def activate_server_langs(self):
        if not self.settings.is_modified(subject=SUBJECT_SERVER_LANGS):
            return
        #self.drop_connections()

        status = self._establish_connections(verbose=True)

        if not status:
            #self.drop_connections()
            return status

        status = self._initialize_obs_controllers(verbose=True)
        self.settings.modify_subject(SUBJECT_SIDECHAIN)
        self.settings.modify_subject(SUBJECT_SOURCE_VOLUME)
        self.settings.modify_subject(SUBJECT_TS_VOLUME)
        self.settings.modify_subject(SUBJECT_TS_OFFSET)

        if not status:
            #self.drop_connections()
            return status

        self.is_initialized = True
        self.settings.activate(SUBJECT_SERVER_LANGS)
        return status

    def activate_stream_settings(self):
        if not self.settings.is_modified(subject=SUBJECT_STREAM_SETTINGS):
            return
        stream_settings = self.settings.get_subject(SUBJECT_STREAM_SETTINGS)
        self.obs_instance.set_stream_settings(server=stream_settings["server"], key=stream_settings["key"])
        self.settings.activate(SUBJECT_STREAM_SETTINGS)

    def activate_stream_on(self):
        if not self.settings.is_modified(subject=SUBJECT_STREAM_ON):
            return
        stream_on = self.settings.get(SUBJECT_STREAM_ON, "value")
        if stream_on:
            self.obs_instance.start_streaming()
        else:
            self.obs_instance.stop_streaming()
        self.settings.activate(SUBJECT_STREAM_ON)

    def activate_schedule(self):
        if not self.settings.is_modified(subject=SUBJECT_MEDIA_SCHEDULE):
            return

    def activate_ts_offset(self):
        if not self.settings.is_modified(subject=SUBJECT_TS_OFFSET):
            return
        ts_offset = self.settings.get(SUBJECT_TS_OFFSET, "value")
        self.obs_instance.set_ts_sync_offset(ts_offset)
        self.settings.activate(SUBJECT_TS_OFFSET)

    def activate_ts_volume(self):
        if not self.settings.is_modified(subject=SUBJECT_TS_VOLUME):
            return
        volume_db = self.settings.get(SUBJECT_TS_VOLUME, "value")
        self.obs_instance.set_ts_volume_db(volume_db)
        self.settings.activate(SUBJECT_TS_VOLUME)

    def activate_source_volume(self):
        if not self.settings.is_modified(subject=SUBJECT_SOURCE_VOLUME):
            return
        volume_db = self.settings.get(SUBJECT_SOURCE_VOLUME, "value")
        self.obs_instance.set_source_volume_db(volume_db)
        self.settings.activate(SUBJECT_SOURCE_VOLUME)

    def activate_sidechain(self):
        if not self.settings.is_modified(subject=SUBJECT_SIDECHAIN):
            return
        sidechain_settings = self.settings.get_subject(SUBJECT_SIDECHAIN)
        self.obs_instance.setup_sidechain(
            ratio=sidechain_settings["ratio"],
            release_time=sidechain_settings["release_time"],
            output_gain=sidechain_settings["output_gain"],
            threshold=sidechain_settings["threshold"],
        )
        self.settings.activate(SUBJECT_SIDECHAIN)

    def activate_transition(self):
        if not self.settings.is_modified(subject=SUBJECT_TRANSITION):
            return
        transition_settings = self.settings.get_subject(SUBJECT_TRANSITION)
        self.obs_instance.setup_transition(
            transition_name=transition_settings["transition_name"], transition_settings=transition_settings
        )
        self.settings.activate(SUBJECT_TRANSITION)

    def activate_gdrive(self):
        self.settings.is_active(subject=SUBJECT_GDRIVE_SETTINGS)
        if not self.settings.is_modified(subject=SUBJECT_GDRIVE_SETTINGS):
            return
        gdrive_settings = self.settings.get_subject(SUBJECT_GDRIVE_SETTINGS)

        # set media dir
        media_dir = gdrive_settings["media_dir"] if "media_dir" in gdrive_settings else BASE_MEDIA_DIR
        self.set_media_dir(media_dir)

        drive_id = gdrive_settings["drive_id"]
        api_key = gdrive_settings["api_key"] if "api_key" in gdrive_settings else DEFAULT_API_KEY
        sync_seconds = gdrive_settings["sync_seconds"] if "sync_seconds" in gdrive_settings else DEFAULT_SYNC_SECONDS
        gdrive_sync_addr = (
            gdrive_settings["gdrive_sync_addr"] if "gdrive_sync_addr" in gdrive_settings else "http://localhost:7000"
        )
        gdrive_sync_addr = gdrive_sync_addr.rstrip("/")

        # build a query
        query_params = urlencode(
            {"drive_id": drive_id, "media_dir": media_dir, "api_key": api_key, "sync_seconds": sync_seconds}
        )

        response_ = requests.post(f"{gdrive_sync_addr}/init?{query_params}")
        if response_.status_code != 200:
            msg_ = f"E PYSERVER::activate_gdrive(): Details: {response_.text}"
            raise RuntimeError(msg_)

        self.settings.activate(SUBJECT_GDRIVE_SETTINGS)
