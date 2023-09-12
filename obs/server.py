import glob
import os
import re
import json
import time
from datetime import datetime, timedelta

import obswebsocket as obsws
import obswebsocket.requests
from dotenv import load_dotenv
from pydantic import BaseModel, PrivateAttr
from typing import List, Dict
import threading

from obs import OBS
from util import ExecutionStatus
from models import MinionSettings
from util import CallbackThread

load_dotenv()
BASE_MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
# DEFAULT_API_KEY = os.getenv("GDRIVE_API_KEY", "")
# DEFAULT_SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 60))
MEDIA_DIR = os.path.join(BASE_MEDIA_DIR, "media")


class OBSFilter(BaseModel):
    enabled: bool = True
    filter_type: str
    filter_settings: dict = None


class OBSInput(BaseModel):
    source_kind: str
    scene_name: str = None
    source_settings: dict = None
    is_muted: bool = False
    volume: float = 0
    monitor_type: str = "none"
    filters: Dict[str, OBSFilter] = {}


class OBSConfig(BaseModel):
    inputs: Dict[str, OBSInput] = {
        "Desktop Audio": OBSInput(
            source_name="Desktop Audio",
            source_kind="pulse_output_capture",
            is_muted=True, source_settings={"device_id": "default"},
        )
    }
    scene: str = "main"

    playing_media_name: str = None
    playing_media_is_vs: bool = False
    playing_media_ts: float = None  # seconds: time.time()
    playing_media_duration: float = None  # seconds


class OBSMonitoring:
    def __init__(self, obs, obs_wrapper, obs_controller):
        self.obs_config = OBSConfig()
        self.obs = obs
        self.obs_wrapper: OBS = obs_wrapper
        self.obs_controller = obs_controller

        self.lock = threading.RLock()
        self.monitoring_thread = threading.Thread(target=self.monitoring)
        self.monitoring_thread.start()

    def monitoring(self):
        while True:
            try:
                self.sync()
            except Exception as ex:
                print(f"Monitoring error: {ex}")
            time.sleep(3)

    def sync(self):
        with self.lock:
            self._check_configs()
            self._check_connection()
            self._sync_scene()
            self._sync_inputs()
            self._sync_playing_media()

    def _check_configs(self):
        if OBS.MAIN_STREAM_SOURCE_NAME in self.obs_config.inputs and \
                OBS.TEAMSPEAK_SOURCE_NAME in self.obs_config.inputs:
            if self.obs_config.playing_media_name:  # media is playing rn

                if self.obs_config.playing_media_is_vs:  # if it is vmix speaker running
                    self.obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME].is_muted = False
                    self.obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME].volume = \
                        self.obs_controller.minion_settings.vmix_speaker_background_volume.value

                    self.obs_config.inputs[OBS.TEAMSPEAK_SOURCE_NAME].is_muted = True

                else:  # if non vmix speaker running
                    self.obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME].is_muted = True
                    self.obs_config.inputs[OBS.TEAMSPEAK_SOURCE_NAME].is_muted = True

            else:  # media is not playing rn
                self.obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME].is_muted = False
                self.obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME].volume = \
                    self.obs_controller.minion_settings.source_volume.value

                self.obs_config.inputs[OBS.TEAMSPEAK_SOURCE_NAME].is_muted = False
                self.obs_config.inputs[OBS.TEAMSPEAK_SOURCE_NAME].volume = \
                    self.obs_controller.minion_settings.ts_volume.value

    def _check_connection(self):
        try:
            if not self.obs.ws or not self.obs.ws.connected:
                self.obs.connect()
        except Exception as ex:
            raise ConnectionError(f"Couldn't connect to OBS: {ex}")

        try:  # check connection by sending a simple request
            response = self.obs.call(obsws.requests.GetCurrentScene())
            if not response.status:
                raise Exception(f"Couldn't check connection. Details: {response.dataout}")
        except Exception as ex:  # if connection is broken - disconnect and try again
            self.obs.disconnect()
            raise ex

    def _sync_scene(self):
        current_scene = self.obs.call(obsws.requests.GetCurrentScene()).getName()
        if current_scene != self.obs_config.scene:
            # [... {'name': '...', 'sources': [...]}, ...]
            scenes = self.obs.call(obsws.requests.GetSceneList()).getScenes()

            # if such scene doesn't exist, create
            if all([x["name"] != self.obs_config.scene for x in scenes]):
                self.obs.call(obsws.requests.CreateScene(sceneName=self.obs_config.scene))

            self.obs.call(obsws.requests.SetCurrentScene(scene_name=self.obs_config.scene))

    def _sync_inputs(self):
        scene_items = self.obs.call(obsws.requests.GetSceneItemList(sceneName=self.obs_config.scene)).getSceneItems()

        # delete scene items which doesn't exist in configs
        for scene_item in scene_items:
            source_name = scene_item["sourceName"]
            if source_name not in self.obs_config.inputs:
                # pass
                if source_name not in ("Desktop Audio", OBS.MAIN_MEDIA_NAME):
                    self.obs.call(obsws.requests.DeleteSceneItem(item=source_name, scene=self.obs_config.scene))
        # sync all inputs
        for source_name in self.obs_config.inputs.keys():
            self._sync_input(source_name)

    def _sync_input(self, source_name):
        input_: OBSInput = self.obs_config.inputs[source_name]
        if not input_.scene_name:
            input_.scene_name = "main"

        if source_name != "Desktop Audio" and not self._source_exists(source_name):
            self.obs.call(
                obsws.requests.CreateSource(
                    sourceName=source_name,
                    sourceKind=input_.source_kind,
                    sceneName=input_.scene_name,
                    sourceSettings=input_.source_settings,
                )
            )
        else:
            response = self.obs.call(obsws.requests.GetSourceSettings(sourceName=source_name))
            source_type = response.getSourceType()
            source_settings = response.getSourceSettings()

            # if settings are different -> synchronize
            if not self._settings_equal(input_.source_kind, source_type,
                                        input_.source_settings, source_settings):
                # if json.dumps({"source_kind": source_type, "source_settings": source_settings}) != \
                #         json.dumps({"source_kind": input_.source_kind, "source_settings": input_.source_settings}):
                self.obs.call(
                    obsws.requests.SetSourceSettings(sourceName=source_name,
                                                     sourceSettings=input_.source_settings,
                                                     sourceType=input_.source_kind)
                )

        self._sync_filters(source_name)
        self._sync_volume(source_name)
        self._sync_mute(source_name)
        self._sync_monitor_type(source_name)

    def _sync_filters(self, source_name):
        input_ = self.obs_config.inputs[source_name]
        filters = self.obs.call(obsws.requests.GetSourceFilters(source_name)).getFilters()

        for filter_ in filters:  # iterate all filters on the input
            filter_name = filter_["name"]
            if filter_name not in input_.filters:  # if such filter doesn't exist in config -> remove
                self.obs.call(obsws.requests.RemoveFilterFromSource(source_name, filter_name))

        for filter_name in input_.filters:  # iterate through config filters
            filter_: OBSFilter = input_.filters[filter_name]
            if filter_name not in [f["name"] for f in filters]:  # if such filter doesn't exist yet -> create
                self.obs.call(
                    obsws.requests.AddFilterToSource(
                        sourceName=source_name,
                        filterName=filter_name,
                        filterType=filter_.filter_type,
                        filterSettings=filter_.filter_settings,
                    )
                )
            else:  # if such filter already exists -> check it
                existing_filter = [f for f in filters if f["name"] == filter_name][0]
                filter_dict = {
                    "enabled": True,
                    "name": filter_name,
                    "settings": filter_.filter_settings,
                    "type": filter_.filter_type,
                }
                if not self._settings_equal("sidechain", "sidechain", filter_dict, existing_filter):
                    # if json.dumps(existing_filter) != json.dumps(filter_dict):  # if settings are differing
                    self.obs.call(obsws.requests.SetSourceFilterSettings(  # synchronise them
                        sourceName=source_name, filterName=filter_name, filterSettings=filter_.filter_settings,
                    ))

            self.obs.call(
                obsws.requests.SetSourceFilterVisibility(
                    sourceName=source_name, filterName=filter_name, filterEnabled=filter_.enabled,
                )
            )

    def _sync_volume(self, source_name):
        input_: OBSInput = self.obs_config.inputs[source_name]

        self.obs.call(
            obsws.requests.SetVolume(source=source_name, volume=input_.volume, useDecibel=True)
        )

    def _sync_mute(self, source_name):
        input_: OBSInput = self.obs_config.inputs[source_name]

        self.obs.call(
            obsws.requests.SetMute(source=source_name, mute=input_.is_muted)
        )

    def _sync_monitor_type(self, source_name):
        input_: OBSInput = self.obs_config.inputs[source_name]

        self.obs.call(obsws.requests.SetAudioMonitorType(sourceName=source_name,
                                                         monitorType=input_.monitor_type))

    def _sync_playing_media(self):
        if self.obs_config and self.obs_config.playing_media_name and not self._source_exists(OBS.MAIN_MEDIA_NAME):
            print(f"Recreating playing media: {self.obs_config.playing_media_name}")
            self.obs_wrapper._run_media(self.obs_config.playing_media_name,
                             OBS.MAIN_MEDIA_NAME,
                             timestamp=(time.time() - self.obs_config.playing_media_ts) * 1000)

    def _source_exists(self, source_name):
        """
        Checks if the item with source name `source_name` exists
        """
        return self._get_itemid_from_sourcename(source_name) is not None

    def _get_itemid_from_sourcename(self, source_name):
        """
        Returns (item_id, scene_name) given a source_name
        """
        items = self.obs.call(obsws.requests.GetSceneItemList(sceneName=self.obs_config.scene)).getSceneItems()
        for item in items:
            item_id, source_name_ = item["itemId"], item["sourceName"]
            if source_name_ == source_name:
                return item_id
        return None

    def _settings_equal(self, source_kind_conf, source_kind_obs, source_settings_conf, source_settings_obs):
        if source_kind_conf != source_kind_obs:
            return False

        if source_settings_conf is not None:
            if source_settings_obs is None:
                return False
            for key, value in source_settings_conf.items():
                if source_settings_obs[key] != value:
                    return False

        return True

    def run_media(self, name, media_dir, search_by_num=None, mode=None) -> ExecutionStatus:
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
        if search_by_num is None:
            search_by_num = True
        if mode is None:
            mode = OBS.PLAYBACK_MODE_FORCE

        if mode not in (OBS.PLAYBACK_MODE_FORCE, OBS.PLAYBACK_MODE_CHECK_ANY, OBS.PLAYBACK_MODE_CHECK_SAME):
            return ExecutionStatus(status=False, message="invalid `mode`")

        status = ExecutionStatus(status=True)

        # search for the file
        if search_by_num:
            # extract file number
            file_num = re.search(r"(?P<file_num>[\d\.]+)_.", name)
            if not file_num:  # if the pattern is incorrect (name doesn't start with numbers)
                status.append_error(
                    f"Server::run_media(): while `use_file_num` is set, "
                    f"`name` doesn't start with a number. name {name}"
                )
                return status
            else:
                file_num = file_num.group("file_num")

                files = glob.glob(os.path.join(media_dir, f"{file_num}_*"))  # find those files
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
            def on_start(filename, duration):
                self.obs_config.playing_media_name = filename
                self.obs_config.playing_media_ts = time.time()
                self.obs_config.playing_media_duration = duration

                if re.match(r"^[\d\.]+_vs_", os.path.basename(filename)):  # if the file is vmix speaker
                    # do not mute original stream
                    self.obs_config.playing_media_is_vs = True
                else:
                    self.obs_config.playing_media_is_vs = False

                self.sync()

            def on_finish():
                self.obs_config.playing_media_name = None
                self.obs_config.playing_media_is_vs = False

                self.sync()

            self.obs_wrapper.run_media(
                path, mode=mode, source_name=OBS.MAIN_MEDIA_NAME,
                on_start=on_start, on_error=on_finish, on_finish=on_finish
            )
        except BaseException as ex:
            status.append_error(f"Server::run_media(): couldn't play media. Details: {ex}")

        return status

    def stop_media(self) -> ExecutionStatus:
        """
        :return:
        """
        status = ExecutionStatus(status=True)

        try:
            self.obs_wrapper.stop_media()

            # self.obs_config.inputs[OBS.TEAMSPEAK_SOURCE_NAME].is_muted = False
            # self.obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME].is_muted = False
            self.obs_config.playing_media_name = None
            self.obs_config.playing_media_is_vs = False

            self.sync()
        except BaseException as ex:
            status.append_error(f"Server::stop_media(): couldn't stop media. Details: {ex}")

        return status


class OBSController:
    def __init__(self):
        self.minion_settings = MinionSettings.default()

        self.obs_ws: obsws.obsws = None
        self.obs_instance: OBS = None
        self.obs_monitoring: OBSMonitoring = None
        self.obs_connected = False
        self.is_initialized = False

        self.media_dir = MEDIA_DIR

        self.media_cb_thread = CallbackThread()
        self.media_cb_thread.start()

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
        # if not status:
        #     return status
        # status = self._initialize_obs_controllers()
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
                self.obs_ws = obsws.obsws(host=addr_config.obs_host, port=addr_config.websocket_port, timeout=10)
                self.obs_instance = OBS(self.obs_ws)
                self.obs_monitoring = OBSMonitoring(obs=self.obs_ws,
                                                    obs_wrapper=self.obs_instance,
                                                    obs_controller=self)
                # self.obs_client = obsws.obsws(host=addr_config.obs_host, port=addr_config.websocket_port)
                # self.obs_client.connect()
                self.obs_connected = True
        except BaseException as ex:
            msg = "Server::_establish_connections(): Couldn't connect to obs server. " \
                  f"Host '{addr_config.obs_host}', " \
                  f"port {addr_config.websocket_port}. Details: {ex}"
            # print(msg)
            status.append_error(msg)

        return status

    def _initialize_obs_controllers(self):
        """
        Creates obs controller instance and set up basic scenes
        """
        # # create obs controller instances
        # # self.obs_monitoring =
        # self.obs_instance = OBS(self.obs_client)
        #
        # addr_config = self.minion_settings.addr_config
        # status = ExecutionStatus(status=True)
        #
        # # reset scenes, create original media sources
        # try:
        #     self.obs_instance.clear_all_scenes()
        #     self.obs_instance.setup_scene(scene_name=OBS.MAIN_SCENE_NAME)
        #     self.obs_instance.add_or_replace_stream(
        #         stream_name=OBS.MAIN_STREAM_SOURCE_NAME, source_url=addr_config.original_media_url
        #     )
        #     self.obs_instance.setup_ts_sound()
        # except BaseException as ex:
        #     status.append_error(
        #         f"Server::_initialize_obs_controllers(): " f"Couldn't initialize obs controller. Details: {ex}"
        #     )
        #
        # return status
        pass

    def cleanup(self):
        # self.stop_streaming()  # no need to check status
        # self._reset_scenes()
        # self.settings.deactivate(SUBJECT_SERVER_LANGS)
        # self.is_initialized = False
        # self.drop_connections()
        pass

    def drop_connections(self):
        # if self.obs_client is not None:
        #     try:
        #         self.obs_client.disconnect()
        #     except Exception as ex:
        #         print(f"PYSERVER::Server::drop_connections(): {ex}")
        # self.obs_connected = False
        pass

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
        return self.obs_monitoring.run_media(
            name=name, media_dir=self.media_dir, search_by_num=search_by_num, mode=mode
        )

    def stop_media(self) -> ExecutionStatus:
        """
        :return:
        """
        return self.obs_monitoring.stop_media()

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

        if self.obs_monitoring.obs_config.playing_media_name:
            return ExecutionStatus(False, "Refreshing media is not allowed while playing video")

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
        self.activate_monitoring()
        status = self.activate_server_langs()
        if not self.minion_settings.addr_config.is_active():
            raise RuntimeError(f"Server::activate(): Couldn't activate addr_config. Details: {status.message}")
        status = self.activate_stream_settings()
        status = self.activate_stream_on()
        status = self.activate_ts_offset()
        # status = self.activate_ts_volume()
        # status = self.activate_source_volume()
        # status = self.activate_sidechain()
        status = self.activate_transition()

    def activate_monitoring(self):
        obs_config: OBSConfig = self.obs_monitoring.obs_config

        # sync teamspeak
        obs_config.inputs[OBS.TEAMSPEAK_SOURCE_NAME] = OBSInput(
            source_kind="pulse_output_capture",
            source_settings={
                "device_id": "obs_sink.monitor",
            },
            is_muted=False,
            volume=self.minion_settings.ts_volume.value,
            filters={
                OBS.TS_GAIN_FILTER_NAME: OBSFilter(
                    enabled=self.minion_settings.ts_gain_settings.enabled,
                    filter_type="gain_filter",
                    filter_settings={
                        "db": self.minion_settings.ts_gain_settings.gain,
                    }
                ),
                OBS.TS_LIMITER_FILTER_NAME: OBSFilter(
                    enabled=self.minion_settings.ts_limiter_settings.enabled,
                    filter_type="limiter_filter",
                    filter_settings={
                        "release_time": self.minion_settings.ts_limiter_settings.release_time,
                        "threshold": self.minion_settings.ts_limiter_settings.threshold,
                    }
                ),
            }
        )

        # sync main source
        obs_config.inputs[OBS.MAIN_STREAM_SOURCE_NAME] = OBSInput(
            source_kind="ffmpeg_source",
            source_settings={
                "buffering_mb": 12,
                "input": self.minion_settings.addr_config.original_media_url,
                "is_local_file": False,
                "clear_on_media_end": False,
            },
            is_muted=False,
            monitor_type="none",
            volume=self.minion_settings.source_volume.value,
            filters={
                OBS.COMPRESSOR_FILTER_NAME: OBSFilter(
                    filter_type="compressor_filter",
                    filter_settings={
                        "sidechain_source": OBS.TEAMSPEAK_SOURCE_NAME,
                        "ratio": self.minion_settings.sidechain_settings.ratio,
                        "release_time": self.minion_settings.sidechain_settings.release_time,
                        "threshold": self.minion_settings.sidechain_settings.threshold,
                        "output_gain": self.minion_settings.sidechain_settings.output_gain,
                    }
                )
            }
        )

        self.obs_monitoring.sync()

    def activate_server_langs(self):
        if self.minion_settings.addr_config.is_active():
            return ExecutionStatus(True)

        status: ExecutionStatus = self.refresh_media_source()

        if not status:
            return status

        # self.minion_settings.sidechain_settings.modify()
        # self.minion_settings.source_volume.modify()
        # self.minion_settings.ts_volume.modify()
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
        if self.minion_settings.stream_on.is_active() or \
                not self.minion_settings.stream_settings.server or \
                not self.minion_settings.stream_settings.key:
            return ExecutionStatus(True)

        stream_on = self.minion_settings.stream_on.value

        try:
            response = self.obs_monitoring.obs.call(obsws.requests.GetStreamingStatus())

            if stream_on:  # if need to start streaming
                if not response.getStreaming():  # and streaming is not active
                    self.obs_instance.start_streaming()  # start it
            else:  # if need to stop streaming
                if response.getStreaming():  # and streaming is active
                    self.obs_instance.stop_streaming()  # stop it
            self.minion_settings.stream_on.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate streaming status. Details: {ex}")

    def activate_ts_offset(self):
        if self.minion_settings.ts_offset.is_active():
            return ExecutionStatus(True)

        ts_offset = self.minion_settings.ts_offset.value

        try:
            os.system(f'bash --login -c "/usr/bin/pactl unload-module module-loopback"')
            os.system(
                f'bash --login -c "/usr/bin/pactl load-module module-loopback sink=obs_sink latency_msec={ts_offset}"')
            # self.obs_instance.set_sound_sync_offset(source_name=OBS.TEAMSPEAK_SOURCE_NAME, offset=ts_offset)
            self.minion_settings.ts_offset.activate()

            return ExecutionStatus(True)
        except Exception as ex:
            return ExecutionStatus(False, f"Couldn't activate teamspeak sound offset. Details: {ex}")

    # def activate_ts_volume(self):
    #     if self.minion_settings.ts_volume.is_active():
    #         return ExecutionStatus(True)
    #
    #     ts_volume = self.minion_settings.ts_volume.value
    #
    #     try:
    #         self.obs_instance.set_sound_volume_db(source_name=OBS.TEAMSPEAK_SOURCE_NAME, volume_db=ts_volume)
    #         self.minion_settings.ts_volume.activate()
    #
    #         return ExecutionStatus(True)
    #     except Exception as ex:
    #         return ExecutionStatus(False, f"Couldn't activate teamspeak volume. Details: {ex}")

    # def activate_source_volume(self):
    #     if self.minion_settings.source_volume.is_active():
    #         return ExecutionStatus(True)
    #
    #     source_volume = self.minion_settings.source_volume.value
    #
    #     try:
    #         self.obs_instance.set_sound_volume_db(source_name=OBS.MAIN_STREAM_SOURCE_NAME, volume_db=source_volume)
    #         self.minion_settings.source_volume.activate()
    #
    #         return ExecutionStatus(True)
    #     except Exception as ex:
    #         return ExecutionStatus(False, f"Couldn't activate source volume. Details: {ex}")

    # def activate_sidechain(self):
    #     if self.minion_settings.sidechain_settings.is_active():
    #         return ExecutionStatus(True)
    #
    #     sidechain_settings = self.minion_settings.sidechain_settings
    #
    #     try:
    #         self.obs_instance.setup_sidechain(
    #             ratio=sidechain_settings.ratio,
    #             release_time=sidechain_settings.release_time,
    #             threshold=sidechain_settings.threshold,
    #             output_gain=sidechain_settings.output_gain,
    #         )
    #         self.minion_settings.sidechain_settings.activate()
    #
    #         return ExecutionStatus(True)
    #     except Exception as ex:
    #         return ExecutionStatus(False, f"Couldn't activate sidechain. Details: {ex}")

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
