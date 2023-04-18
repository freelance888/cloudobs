import glob
import os
import re

import obswebsocket as obsws
from dotenv import load_dotenv

from obs import OBS
from util import ExecutionStatus
from models import MinionSettings

load_dotenv()
BASE_MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
# DEFAULT_API_KEY = os.getenv("GDRIVE_API_KEY", "")
# DEFAULT_SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 60))
MEDIA_DIR = os.path.join(BASE_MEDIA_DIR, "media")
# TRANSITION_DIR = os.path.join(BASE_MEDIA_DIR, "media")


class OBSController:
    def __init__(self):
        self.minion_settings = MinionSettings.default()

        self.obs_instance: OBS = None
        self.obs_client = None
        self.obs_connected = False
        self.is_initialized = False

        self.media_dir = MEDIA_DIR

    def apply_info(self, minion_settings: MinionSettings):
        self.minion_settings.modify_from(other=minion_settings)

        status = ExecutionStatus(status=True)
        try:
            status = self._check_initialization()
            if not status:
                return status

            self.activate()
        except BaseException as ex:
            status.append_error(f"Server::apply_info(): Couldn't activate settings. Details: {ex}")
        return status

    def _check_initialization(self):
        if self.is_initialized:
            return ExecutionStatus(True)
        status = self._establish_connections()
        if not status:
            return status
        status = self._initialize_obs_controllers()
        if status:
            self.is_initialized = True
        return status

    def _establish_connections(self):
        """
        establish obs connection
        :return: True/False
        """
        # create obs ws clients
        addr_config = self.minion_settings.addr_config
        status = ExecutionStatus(status=True)

        # establish connections
        try:
            if not self.obs_connected:
                self.obs_client = obsws.obsws(host=addr_config.obs_host, port=addr_config.websocket_port)
                self.obs_client.connect()
                self.obs_connected = True
        except BaseException as ex:
            status.append_error(
                "Server::_establish_connections(): Couldn't connect to obs server. "
                f"Host '{addr_config.obs_host}', "
                f"port {addr_config.websocket_port}. Details: {ex}"
            )

        return status

    def _initialize_obs_controllers(self):
        """
        Creates obs controller instance and set up basic scenes
        """
        # create obs controller instances
        self.obs_instance = OBS(self.obs_client)

        addr_config = self.minion_settings.addr_config
        status = ExecutionStatus(status=True)

        # reset scenes, create original media sources
        try:
            self.obs_instance.clear_all_scenes()
            self.obs_instance.setup_scene(scene_name=OBS.MAIN_SCENE_NAME)
            self.obs_instance.add_or_replace_stream(
                stream_name=OBS.MAIN_STREAM_SOURCE_NAME, source_url=addr_config.original_media_url
            )
            self.obs_instance.setup_ts_sound()
        except BaseException as ex:
            status.append_error(
                f"Server::_initialize_obs_controllers(): " f"Couldn't initialize obs controller. Details: {ex}"
            )

        return status

    def cleanup(self):
        # self.stop_streaming()  # no need to check status
        # self._reset_scenes()
        # self.settings.deactivate(SUBJECT_SERVER_LANGS)
        # self.is_initialized = False
        # self.drop_connections()
        pass

    def drop_connections(self):
        if self.obs_client is not None:
            try:
                self.obs_client.disconnect()
            except Exception as ex:
                print(f"PYSERVER::Server::drop_connections(): {ex}")
        self.obs_connected = False

    def run_media(self, name, search_by_num=None, mode=None, source_name=None) -> ExecutionStatus:
        """
        :param name: see docs
        :param search_by_num: points if need to search a media file by leading numbers, defaults to True
        :param mode: media play mode. Possible values:
                     - "force" - stop any media being played right now, and play media specified (default value)
                     - "check_any" - if any video is being played, skip
                     - "check_same" - if the same video is being played, skip, otherwise play
        :param source_name: input name in OBS, defaults to OBS.MAIN_MEDIA_NAME
        :return: ExecutionStatus()
        """
        if not self._check_initialization():
            return ExecutionStatus(status=False, message="Couldn't initialize the server")

        if search_by_num is None:
            search_by_num = True
        if mode is None:
            mode = OBS.PLAYBACK_MODE_FORCE
        if source_name is None:
            source_name = OBS.MAIN_MEDIA_NAME

        if mode not in (OBS.PLAYBACK_MODE_FORCE, OBS.PLAYBACK_MODE_CHECK_ANY, OBS.PLAYBACK_MODE_CHECK_SAME):
            return ExecutionStatus(status=False, message="invalid `mode`")

        status = ExecutionStatus(status=True)

        media_dir = self.media_dir

        # search for the file
        if search_by_num:
            # extract file number
            file_num = re.search(r"^[\d\.]+.", name)
            if not file_num:  # if the pattern is incorrect (name doesn't start with numbers)
                status.append_error(
                    f"Server::run_media(): while `use_file_num` is set, "
                    f"`name` doesn't start with a number. name {name}"
                )
                return status
            else:
                file_num = file_num.group()

                files = glob.glob(os.path.join(media_dir, f"{file_num}*"))  # find those files
                if len(files) == 0:  # if no media found with name specified
                    status.append_warning(f"Server::run_media(): no media found, name {name}")
                    return status
                else:
                    path = files[0]
        else:
            path = os.path.join(media_dir, name)
            if not os.path.isfile(path):
                status.append_warning(f"Server::run_media(): no media found with name specified, name {name}")
                return status

        try:

            def on_start():
                self.obs_instance.set_mute(source_name=OBS.TEAMSPEAK_SOURCE_NAME, mute=True)
                self.obs_instance.set_mute(source_name=OBS.MAIN_STREAM_SOURCE_NAME, mute=True)

            def on_finish():
                self.obs_instance.set_mute(source_name=OBS.TEAMSPEAK_SOURCE_NAME, mute=False)
                self.obs_instance.set_mute(source_name=OBS.MAIN_STREAM_SOURCE_NAME, mute=False)

            self.obs_instance.run_media(
                path, mode=mode, source_name=source_name, on_start=on_start, on_error=on_finish, on_finish=on_finish
            )
        except BaseException as ex:
            status.append_error(f"Server::run_media(): couldn't play media. Details: {ex}")

        return status

    def stop_media(self) -> ExecutionStatus:
        """
        :return:
        """
        if not self._check_initialization():
            return ExecutionStatus(status=False, message="Couldn't initialize the server")

        status = ExecutionStatus(status=True)

        try:
            self.obs_instance.stop_media()
            self.obs_instance.set_mute(source_name=OBS.TEAMSPEAK_SOURCE_NAME, mute=False)
            self.obs_instance.set_mute(source_name=OBS.MAIN_STREAM_SOURCE_NAME, mute=False)
        except BaseException as ex:
            status.append_error(f"Server::stop_media(): couldn't stop media. Details: {ex}")

        return status

    def set_media_dir(self, media_dir) -> ExecutionStatus:
        """
        :param media_dir
        :return:
        """
        # if not self._check_initialization():
        #     return ExecutionStatus(status=False, message="Couldn't initialize the server")

        self.media_dir = media_dir
        if not os.path.isdir(self.media_dir):
            os.system(f"mkdir -p {self.media_dir}")

        return ExecutionStatus(status=os.path.isdir(self.media_dir))

    def refresh_media_source(self):
        if not self._check_initialization():
            return ExecutionStatus(status=False, message="Couldn't initialize the server")

        status: ExecutionStatus = ExecutionStatus()
        try:
            self.obs_instance.add_or_replace_stream(
                stream_name=OBS.MAIN_STREAM_SOURCE_NAME, source_url=self.minion_settings.addr_config.original_media_url
            )
        except Exception as ex:
            status.append_error(f"Server::refresh_media_source(): Couldn't refresh stream. Details: {ex}")
        return status

    # def _reset_scenes(self, verbose=True):
    #     status = ExecutionStatus(status=True)
    #
    #     # reset scenes, create original media sources
    #     try:
    #         self.obs_instance.clear_all_scenes()
    #         self.obs_instance.setup_scene(scene_name=obs.MAIN_SCENE_NAME)
    #     except BaseException as ex:
    #         msg_ = f"E PYSERVER::Server::_reset_scenes(): Couldn't reset scenes. Details: {ex}"
    #         if verbose:
    #             print(msg_)
    #         status.append_error(msg_)
    #
    #     return status

    def activate(self):
        status = self.activate_server_langs()
        if not self.minion_settings.addr_config.is_active():
            raise RuntimeError("Server::activate(): Couldn't activate addr_config")
        status = self.activate_stream_settings()
        status = self.activate_stream_on()
        status = self.activate_ts_offset()
        status = self.activate_ts_volume()
        status = self.activate_source_volume()
        status = self.activate_sidechain()
        status = self.activate_transition()

    def activate_server_langs(self):
        if self.minion_settings.addr_config.is_active():
            return ExecutionStatus(True)

        status: ExecutionStatus = self.refresh_media_source()

        if not status:
            return status

        self.minion_settings.sidechain_settings.modify()
        self.minion_settings.source_volume.modify()
        self.minion_settings.ts_volume.modify()
        self.minion_settings.ts_offset.modify()

        self.minion_settings.addr_config.activate()

        return ExecutionStatus(True)

    def activate_stream_settings(self):
        if self.minion_settings.stream_settings.is_active():
            return ExecutionStatus(True)

        stream_settings = self.minion_settings.stream_settings

        try:
            self.obs_instance.set_stream_settings(server=stream_settings.server, key=stream_settings.key)
            self.minion_settings.stream_settings.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate stream settings. Details {ex}")

    def activate_stream_on(self):
        if self.minion_settings.stream_on.is_active():
            return ExecutionStatus(True)

        stream_on = self.minion_settings.stream_on.value

        try:
            if stream_on:
                self.obs_instance.start_streaming()
            else:
                self.obs_instance.stop_streaming()
            self.minion_settings.stream_on.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate streaming status. Details: {ex}")

    def activate_ts_offset(self):
        if self.minion_settings.ts_offset.is_active():
            return ExecutionStatus(True)

        ts_offset = self.minion_settings.ts_offset.value

        try:
            os.system(f"pactl unload-module module-loopback")
            os.system(f"pactl load-module module-loopback sink=obs_sink latency_msec={ts_offset * 1000}")
            # self.obs_instance.set_sound_sync_offset(source_name=OBS.TEAMSPEAK_SOURCE_NAME, offset=ts_offset)
            self.minion_settings.ts_offset.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate teamspeak sound offset. Details: {ex}")

    def activate_ts_volume(self):
        if self.minion_settings.ts_volume.is_active():
            return ExecutionStatus(True)

        ts_volume = self.minion_settings.ts_volume.value

        try:
            self.obs_instance.set_sound_volume_db(source_name=OBS.TEAMSPEAK_SOURCE_NAME, volume_db=ts_volume)
            self.minion_settings.ts_volume.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate teamspeak volume. Details: {ex}")

    def activate_source_volume(self):
        if self.minion_settings.source_volume.is_active():
            return ExecutionStatus(True)

        source_volume = self.minion_settings.source_volume.value

        try:
            self.obs_instance.set_sound_volume_db(source_name=OBS.MAIN_STREAM_SOURCE_NAME, volume_db=source_volume)
            self.minion_settings.source_volume.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate source volume. Details: {ex}")

    def activate_sidechain(self):
        if self.minion_settings.sidechain_settings.is_active():
            return ExecutionStatus(True)

        sidechain_settings = self.minion_settings.sidechain_settings

        try:
            self.obs_instance.setup_sidechain(
                ratio=sidechain_settings.ratio,
                release_time=sidechain_settings.release_time,
                threshold=sidechain_settings.threshold,
                output_gain=sidechain_settings.output_gain,
            )
            self.minion_settings.sidechain_settings.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate sidechain. Details: {ex}")

    def activate_transition(self):
        if self.minion_settings.transition_settings.is_active():
            return ExecutionStatus(True)

        transition_settings = self.minion_settings.transition_settings

        try:
            self.obs_instance.setup_transition(
                transition_name=transition_settings.transition_name,
                transition_settings={
                    "path": transition_settings.path,
                    "transition_point": transition_settings.transition_point,
                },
            )
            self.minion_settings.transition_settings.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate transition. Details: {ex}")
