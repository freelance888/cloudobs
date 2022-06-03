import glob
import os
import re

import obswebsocket as obsws
from dotenv import load_dotenv

import obs
from util import ExecutionStatus
from util import MultilangParams

load_dotenv()
BASE_MEDIA_DIR = '/home/stream/content'  # default value, overwritten by gdrive_sync initialization
MEDIA_DIR = os.path.join(BASE_MEDIA_DIR, "media")
TRANSITION_DIR = os.path.join(BASE_MEDIA_DIR, "media")


class Server:
    def __init__(self, server_langs):
        """
        :param server_langs: dict of
            "lang": {
                "obs_host": "localhost",
                "websocket_port": 1234,
                "password": "qwerty123",
                "original_media_url": "srt://localhost"
            }
        :return:
        """
        self.server_langs = server_langs

        self.obs_instances = None  # {..., "lang": obs.OBS(), ...}
        self.obs_clients = None
        self.is_initialized = False

        self.media_dir = MultilangParams({'__all__': MEDIA_DIR})

    def initialize(self):
        """
        establish connections, initialize obs controllers, setup scenes, create original media sources
        :return: True/False, error message
        """
        status = self._establish_connections(verbose=True)

        if not status:
            self.drop_connections()
            return status

        status = self._initialize_obs_controllers(verbose=True)

        if not status:
            self.drop_connections()
            return status

        self.is_initialized = True
        return status

    def cleanup(self):
        self.stop_streaming(["__all__"])  # no need to check status
        self._reset_scenes()
        # self.drop_connections()

    def drop_connections(self):
        for lang, client in self.obs_clients.items():
            try:
                client.disconnect()
            except Exception:  # FIXME
                pass

    def schedule_media(self, schedule):
        """
        :param schedule: dictionary of {lang: [..., [path, timestamp], ...], ...}
         - path - media name
         - timestamp - relative timestamp in milliseconds
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)
        for lang, schedule_ in schedule.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::schedule_media(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                obs_.schedule_media(schedule_)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::schedule_media(): couldn't schedule media, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
        return status

    def run_media(self, params):
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, params_ in params.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::run_media(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            use_file_num, name = params_["search_by_num"], params_["name"]
            media_type = params_['media_type'] if 'media_type' in params_ else 'media'
            media_dir = os.path.join(self.media_dir[lang], "media")

            # search for the file
            if use_file_num:
                # extract file number
                file_num = re.search(r"^\d+", name)
                if not file_num:  # if the pattern is incorrect (name doesn't start with numbers)
                    msg_ = (
                        f"W PYSERVER::Server::run_media(): while `use_file_num` is set, "
                        f"`name` doesn't start with a number. lang {lang}, name {name}"
                    )
                    print(msg_)
                    status.append_warning(msg_)
                    continue
                file_num = file_num.group()

                files = glob.glob(os.path.join(media_dir, f"{file_num}*"))
                if len(files) == 0:
                    msg_ = f"W PYSERVER::Server::run_media(): no media found, " f"lang {lang}, name {name}"
                    print(msg_)
                    status.append_warning(msg_)
                    continue
                path = files[0]
            else:
                path = os.path.join(media_dir, name)
                if not os.path.isfile(path):
                    msg_ = (
                        f"W PYSERVER::Server::run_media(): no media found with name specified, "
                        f"lang {lang}, name {name}"
                    )
                    print(msg_)
                    status.append_warning(msg_)
                    continue

            try:
                obs_.run_media(path, media_type=media_type)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::run_media(): couldn't play media, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)

        return status

    def set_stream_settings(self, stream_settings):
        """
        :param stream_settings: dictionary,
        e.g. {"lang": {"server": "rtmp://...", "key": "..."}, ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, settings_ in stream_settings.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::set_stream_settings(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                obs_.set_stream_settings(server=settings_["server"], key=settings_["key"])
            except BaseException as ex:
                msg_ = (
                    f"E PYSERVER::Server::set_stream_settings(): "
                    f"couldn't set stream settings, lang {lang}. "
                    f"Details: {ex}"
                )
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)

        return status

    def get_ts_sync_offset(self):
        """
        Retrieves information about teamspeak audio sync offset
        :return: {"lang": offset_int, ...} (note, offset in milliseconds)
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        data = {}

        for lang, obs_ in self.obs_instances.items():
            try:
                offset = obs_.get_ts_sync_offset()
                data[lang] = offset
            except BaseException as ex:
                msg_ = (
                    f"E PYSERVER::Server::get_ts_sync_offset(): "
                    f"couldn't retrieve sync offset, lang {lang}. "
                    f"Details: {ex}"
                )
                print(msg_)  # TODO: logging methods
                data[lang] = "#"  # TODO: handle errors
                # return ExecutionStatus(status=False, message=msg_)
        return data

    def set_ts_sync_offset(self, offset_settings):
        """
        :param offset_settings: dictionary,
        e.g. {"lang": 4000, ...} (note, offset in milliseconds)
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, offset in offset_settings.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::set_ts_sync_offset(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                obs_.set_ts_sync_offset(offset)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::set_ts_sync_offset(): couldn't set sync offset, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)

        return status

    def get_ts_volume_db(self):
        """
        Retrieves teamspeak sound volume (in decibels)
        :return: {"lang": volume_db, ...}
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        data = {}

        for lang, obs_ in self.obs_instances.items():
            try:
                volume = obs_.get_ts_volume_db()
                data[lang] = volume
            except BaseException as ex:
                msg_ = (
                    f"E PYSERVER::Server::get_ts_volume_db(): couldn't retrieve ts volume, lang {lang}. Details: {ex}"
                )
                print(msg_)  # TODO: logging methods
                data[lang] = "#"  # TODO: handle errors
                # return ExecutionStatus(status=False, message=msg_)
        return data

    def set_ts_volume_db(self, volume_settings):
        """
        :param volume_settings: volume dictionary,
        e.g. {"lang": 0.0, ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, volume in volume_settings.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::set_ts_volume_db(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                obs_.set_ts_volume_db(volume)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::set_ts_volume_db(): couldn't set ts volume, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)

        return status

    def get_source_volume_db(self):
        """
        Retrieves original source sound volume (in decibels)
        :return: {"lang": volume_db, ...}
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        data = {}

        for lang, obs_ in self.obs_instances.items():
            try:
                volume = obs_.get_source_volume_db()
                data[lang] = volume
            except BaseException as ex:
                msg_ = (
                    f"E PYSERVER::Server::get_source_volume_db(): "
                    f"couldn't retrieve original source volume, lang {lang}. "
                    f"Details: {ex}"
                )
                print(msg_)  # TODO: logging methods
                data[lang] = "#"  # TODO: handle errors
                # return ExecutionStatus(status=False, message=msg_)
        return data

    def set_source_volume_db(self, volume_settings):
        """
        :param volume_settings: volume dictionary,
        e.g. {"lang": 0.0, ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, volume in volume_settings.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::set_source_volume_db(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                obs_.set_source_volume_db(volume)
            except BaseException as ex:
                msg_ = (
                    f"E PYSERVER::Server::set_source_volume_db(): "
                    f"couldn't set original source volume, lang {lang}. "
                    f"Details: {ex}"
                )
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)

        return status

    def setup_sidechain(self, sidechain_settings):
        """
        :param sidechain_settings: sidechain settings dictionary,
        e.g. {"lang": {'ratio': ..., 'release_time': ..., 'threshold': ...}, ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, settings in sidechain_settings.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::setup_sidechain(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                ratio = settings["ratio"] if "ratio" in settings else None
                release_time = settings["release_time"] if "release_time" in settings else None
                threshold = settings["threshold"] if "threshold" in settings else None

                obs_.setup_sidechain(ratio=ratio, release_time=release_time, threshold=threshold)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::setup_sidechain(): couldn't setup sidechain, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)
        return status

    def setup_transition(self, transition_settings):
        """
        :param transition_settings: sidechain settings dictionary,
        e.g. {"lang": {'transition_name': ..., 'audio_fade_style': ..., 'path': ..., ...}, ...}
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, settings in transition_settings.items():
            if lang not in self.obs_instances:
                msg_ = f"W PYSERVER::Server::setup_transition(): no obs instance found with lang {lang} specified"
                print(msg_)
                status.append_warning(msg_)
                continue
            obs_: obs.OBS = self.obs_instances[lang]
            try:
                transition_name = settings.pop("transition_name")
                if "path" in settings:
                    settings["path"] = os.path.join(TRANSITION_DIR, settings["path"])

                obs_.setup_transition(transition_name=transition_name, transition_settings=settings)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::setup_transition(): couldn't setup transition, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)
        return status

    def start_streaming(self, langs):
        """
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)

        for lang, obs_ in self.obs_instances.items():
            if lang not in langs:
                continue
            try:
                obs_.start_streaming()
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::start_streaming(): couldn't play media, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_

        return status

    def stop_streaming(self, langs):
        """
        :return:
        """
        if not self.is_initialized:
            return ExecutionStatus(status=False, message="The server was not initialized yet")

        status = ExecutionStatus(status=True)
        b_all_langs = len(langs) > 0 and langs[0] == "__all__"

        for lang, obs_ in self.obs_instances.items():
            if not b_all_langs and lang not in langs:
                continue
            try:
                obs_.stop_streaming()
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::stop_streaming(): couldn't play media, lang {lang}. Details: {ex}"
                print(msg_)
                status.append_error(msg_)
                # return ExecutionStatus(status=False, message=msg_)

        return status

    def set_media_dir(self, media_dir_settings):
        """
        :param media_dir_settings: dict, e.g. {"lang": media_dir}
        :return:
        """
        self.media_dir = MultilangParams(media_dir_settings)

    def _establish_connections(self, verbose=True):
        """
        establish connections
        :return: True/False
        """
        # create obs ws clients
        self.obs_clients = {
            lang: obsws.obsws(host=lang_info["obs_host"], port=int(lang_info["websocket_port"]))
            for lang, lang_info in self.server_langs.items()
        }

        status = ExecutionStatus(status=True)

        # establish connections
        for lang, client in self.obs_clients.items():
            # if couldn't establish a connection
            try:
                client.connect()
            except BaseException as ex:
                msg_ = (
                    "E PYSERVER::Server::_establish_connections(): Couldn't connect to obs server. "
                    f"Lang '{lang}', "
                    f"host '{self.server_langs[lang]['obs_host']}', "
                    f"port {self.server_langs[lang]['websocket_port']}. Details: {ex}"
                )
                if verbose:
                    print(msg_)
                status.append_error(msg_)

        return status

    def _initialize_obs_controllers(self, verbose=True):
        """
        Creates obs controller instances and set's up basic scenes
        """
        # create obs controller instances
        self.obs_instances = {lang: obs.OBS(lang, client) for lang, client in self.obs_clients.items()}

        status = ExecutionStatus(status=True)

        # reset scenes, create original media sources
        for lang, obs_ in self.obs_instances.items():
            try:
                obs_.clear_all_scenes()
                obs_.setup_scene(scene_name=obs.MAIN_SCENE_NAME)
                obs_.set_original_media_source(
                    scene_name=obs.MAIN_SCENE_NAME, original_media_source=self.server_langs[lang]["original_media_url"]
                )
                obs_.setup_ts_sound()
            except BaseException as ex:
                msg_ = (
                    f"E PYSERVER::Server::_initialize_obs_controllers(): Couldn't initialize obs controller. "
                    f"Lang: '{lang}', "
                    f"host '{self.server_langs[lang]['obs_host']}', "
                    f"port {self.server_langs[lang]['websocket_port']}. Details: {ex}"
                )
                if verbose:
                    print(msg_)
                status.append_error(msg_)

        return status

    def _reset_scenes(self, verbose=True):
        status = ExecutionStatus(status=True)

        # reset scenes, create original media sources
        for lang, obs_ in self.obs_instances.items():
            try:
                obs_.clear_all_scenes()
                obs_.setup_scene(scene_name=obs.MAIN_SCENE_NAME)
            except BaseException as ex:
                msg_ = f"E PYSERVER::Server::_reset_scenes(): Couldn't reset scenes. Details: {ex}"
                if verbose:
                    print(msg_)
                status.append_error(msg_)

        return status
