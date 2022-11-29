import os

import obswebsocket as obs
import obswebsocket.requests

from util.util import CallbackThread
import util.util as util

ORIGINAL_STREAM_SOURCE_NAME = "original_stream"
TS_INPUT_NAME = "ts_input"
MEDIA_INPUT_NAME = "media"
TRANSITION_INPUT_NAME = "transition"

MAIN_SCENE_NAME = "main"
MEDIA_SCENE_NAME = "media"
COMPRESSOR_FILTER_NAME = "sidechain"


def create_event_handler(obs_instance):
    def foo(message):
        obs_instance.on_event(message)

    return foo


def obs_fire(type, cls, cls_foo, comment, datain, dataout):
    raise Exception(f"{type} PYSERVER::{cls}::{cls_foo}(): {comment} " f"datain: {datain}, dataout: {dataout}")


class OBS:
    def __init__(self, client):
        self.client = client
        self.original_media_source = None
        self.media_queue = []
        self.callback_queue = []  # list of

        self.transition_name = "Cut"
        self.transition_path = ""
        self.transition_point = 0

        self.schedule_mode = False

        self.media_cb_thread = CallbackThread()
        self.media_cb_thread.start()

        self.client.register(create_event_handler(self))

        self._current_media_played = None

    def set_original_media_source(self, scene_name, original_media_source,
                                  recreate_source=True):
        """
        Adds an original media source
        :param scene_name: scene to add an input
        :param original_media_source: url like 'protocol://address[:port][/path][...]', may be rtmp, srt
        """
        self.original_media_source = original_media_source
        if recreate_source:
            self.delete_source(ORIGINAL_STREAM_SOURCE_NAME)

        source_settings = {
            "buffering_mb": 12,
            "input": original_media_source,
            "is_local_file": False,
            "clear_on_media_end": False,
        }
        if not self.source_exists(source_name=ORIGINAL_STREAM_SOURCE_NAME):
            request = obs.requests.CreateSource(
                sourceName=ORIGINAL_STREAM_SOURCE_NAME,
                sourceKind="ffmpeg_source",
                sceneName=scene_name,
                sourceSettings=source_settings,
            )
            response = self.client.call(request)
            if not response.status:
                raise Exception(
                    f"E PYSERVER::OBS::add_original_media_source(): "
                    f"datain: {response.datain}, dataout: {response.dataout}"
                )

        response = self.client.call(obs.requests.SetSourceSettings(
            ORIGINAL_STREAM_SOURCE_NAME,
            source_settings,
        ))
        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::add_original_media_source(): "
                f"datain: {response.datain}, dataout: {response.dataout}"
            )

        request = obs.requests.SetAudioMonitorType(sourceName=ORIGINAL_STREAM_SOURCE_NAME, monitorType="none")
        response = self.client.call(request)

        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::add_original_media_source(): "
                f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def setup_scene(self, scene_name="main", switch_scene=True):
        """
        Creates (if not been created) a scene called `scene_name` and sets it as a current scene.
        If it has been created, removes all the sources inside the scene and sets it as a current one.
        """
        try:
            self.set_mute(source_name="Desktop Audio", mute=True)
        except Exception as ex:
            print(f"W PYSERVER::OBS::setup_scene(): couldn't mute 'Desktop Audio', details: {ex}")

        scenes = self.client.call(
            obs.requests.GetSceneList()
        ).getScenes()  # [... {'name': '...', 'sources': [...]}, ...]

        # if such scene has already been created
        if any([x["name"] == scene_name for x in scenes]):
            self.clear_scene(scene_name)
        else:
            self.create_scene(scene_name)

        if switch_scene:
            self.set_current_scene(scene_name)

    def clear_all_scenes(self):
        """
        Lists all the scenes and removes all the scene items.
        """
        scenes = self.obsws_get_scene_list()
        for scene_info in scenes:
            scene_name = scene_info["name"]
            self.clear_scene(scene_name)

    def clear_scene(self, scene_name):
        """
        Removes all the items from a specified scene
        """
        items = self.obsws_get_scene_item_list(scene_name=scene_name)
        for item in items:
            self.delete_scene_item(item_id=item["itemId"], source_name=item["sourceName"], scene_name=scene_name)

    def set_current_scene(self, scene_name):
        """
        Switches current scene to `scene_name`
        """
        self.client.call(obs.requests.SetCurrentScene(scene_name=scene_name))

    def create_scene(self, scene_name):
        """
        Creates a scene with name `scene_name`
        """
        self.client.call(obs.requests.CreateScene(sceneName=scene_name))

    def run_media(self, path, media_type="media", mode=util.PLAYBACK_MODE_FORCE, source_name=MEDIA_INPUT_NAME):
        """
        Mutes original media, adds and runs the media located at `path`, and appends a listener which removes
        the media when it has finished. Fires Exception when couldn't add or mute a source.
        """
        CB_TYPE = "media"

        if mode == util.PLAYBACK_MODE_CHECK_ANY and self._current_media_played is not None:
            return
        if mode == util.PLAYBACK_MODE_CHECK_SAME and self._current_media_played == path:
            return

        def media_play_foo():
            """
            Removes transition, runs media
            """
            # delay for self.transition_point / 1000
            try:
                self.delete_source(source_name)  # remove media, if any has been played before
                self._run_media(path, source_name)
                self.delete_source(source_name=TRANSITION_INPUT_NAME)
                self.set_source_mute(True)
                self.set_ts_mute(True)
                duration = self.client.call(obs.requests.GetMediaDuration(sourceName=source_name)).getMediaDuration()
                self.media_cb_thread.append_callback(media_end_foo, duration / 1000, cb_type=CB_TYPE)
            except Exception as ex:
                self.delete_source(source_name)
                self.set_source_mute(False)
                self.set_ts_mute(False)
                self._current_media_played = None
                raise ex

        def media_end_foo():
            """
            Deletes media, runs stinger if needed
            """
            self.delete_source(source_name=source_name)
            if self.transition_name == "Stinger":
                self._run_media(self.transition_path, TRANSITION_INPUT_NAME)
                self.media_cb_thread.append_callback(transition_end_foo, self.transition_point / 1000, cb_type=CB_TYPE)
            elif self.transition_name == "Cut":
                self.media_cb_thread.append_callback(transition_end_foo, 0, cb_type=CB_TYPE)

        def transition_end_foo():
            """
            On transition (stinger) finish handler.
            Removes stinger, mutes source and teamspeak
            """
            self._current_media_played = None
            self.delete_source(source_name=TRANSITION_INPUT_NAME)
            self.set_source_mute(False)
            self.set_ts_mute(False)
            self.media_cb_thread.delete_cb_type(cb_type=CB_TYPE)

        self.media_cb_thread.delete_cb_type(cb_type=CB_TYPE)  # clean callbacks queue

        if self.transition_name == "Stinger":
            self._run_media(self.transition_path, TRANSITION_INPUT_NAME)
            self.set_source_mute(True)  # mute main source
            self.set_ts_mute(True)  # mute teamspeak
        elif self.transition_name == "Cut":
            self.set_source_mute(False)  # unmute main source
            self.set_ts_mute(False)  # unmute teamspeak

        self._current_media_played = path
        self.media_cb_thread.append_callback(media_play_foo, self.transition_point / 1000, cb_type=CB_TYPE)

    def stop_media(self, source_name=MEDIA_INPUT_NAME):
        """
        Stop playing media
        """
        CB_TYPE = "media"

        self._current_media_played = None
        self.delete_source(source_name=source_name)
        self.delete_source(source_name=TRANSITION_INPUT_NAME)
        self.set_source_mute(False)
        self.set_ts_mute(False)
        self.media_cb_thread.delete_cb_type(cb_type=CB_TYPE)

    def setup_ts_sound(self):
        """
        Adds/Resets teamspeak audio input (default device).
        """

        self.delete_source(TS_INPUT_NAME)
        current_scene = self.obsws_get_current_scene_name()

        response = self.client.call(
            obs.requests.CreateSource(
                sourceName=TS_INPUT_NAME,
                sourceKind="pulse_output_capture",
                sceneName=current_scene,
            )
        )

        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::setup_ts_sound(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def setup_transition(self, transition_name="Cut", transition_settings=None):
        """
        :param transition_name: transition name, e.g. "Cut" or "Stinger"
        :param transition_settings:
        e.g.:
        {'path': '/home/user/common/_Sting_RT.mp4',
         'transition_point': 3000}
        :return:
        """
        if transition_name == "Stinger":
            if "transition_point" not in transition_settings:
                transition_settings["transition_point"] = 3000
            if "path" not in transition_settings:
                raise Exception("E PYSERVER::OBS::setup_transition(): " "`path` is not specified")
            if not os.path.isfile(transition_settings["path"]):
                raise Exception(f"W PYSERVER::OBS::setup_transition(): " f"no such file: {transition_settings['path']}")
            self.transition_path = transition_settings["path"]
            self.transition_point = int(transition_settings["transition_point"])
        else:
            if "transition_point" not in transition_settings:
                transition_settings["transition_point"] = 0
            self.transition_point = int(transition_settings["transition_point"])

        self.transition_name = transition_name

    def get_ts_sync_offset(self):
        """
        Retrieves teamspeak sound sync offset
        :return:
        """
        response = self.client.call(obs.requests.GetSyncOffset(source=TS_INPUT_NAME))

        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::get_ts_sync_offset(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

        return response.getOffset() // 1_000_000

    def set_ts_sync_offset(self, offset):
        """
        Sets teamspeak sound ('ts_input' source) sync offset
        :return:
        """
        response = self.client.call(
            obs.requests.SetSyncOffset(
                source=TS_INPUT_NAME,
                offset=offset * 1_000_000,  # convert to nanoseconds (refer to documentation)
            )
        )

        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::set_ts_sync_offset(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def get_ts_volume_db(self):
        """
        Retrieves teamspeak sound volume (in decibels)
        :return:
        """
        response = self.client.call(obs.requests.GetVolume(source=TS_INPUT_NAME, useDecibel=True))

        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::get_ts_volume_db(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

        return response.getVolume()

    def set_ts_volume_db(self, volume_db):
        """
        Sets teamspeak sound volume (in decibels)
        :param volume_db:
        :return:
        """
        response = self.client.call(obs.requests.SetVolume(source=TS_INPUT_NAME, volume=volume_db, useDecibel=True))

        if not response.status:
            raise RuntimeError(
                f"E PYSERVER::OBS::set_ts_volume_db(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def get_source_volume_db(self):
        """
        Retrieves original source sound volume (in decibels)
        :return:
        """
        response = self.client.call(obs.requests.GetVolume(source=ORIGINAL_STREAM_SOURCE_NAME, useDecibel=True))

        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::get_source_volume_db(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

        return response.getVolume()

    def set_source_volume_db(self, volume_db):
        """
        Sets original source sound volume (in decibels)
        :param volume_db:
        :return:
        """
        response = self.client.call(
            obs.requests.SetVolume(source=ORIGINAL_STREAM_SOURCE_NAME, volume=volume_db, useDecibel=True)
        )

        if not response.status:
            raise RuntimeError(
                f"E PYSERVER::OBS::set_source_volume_db(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def setup_sidechain(self, ratio=None, release_time=None, threshold=None, output_gain=None):
        """
        [{'enabled': True,
          'name': 'sidechain',
          'settings': {'ratio': 15.0,
           'release_time': 1000,
           'sidechain_source': 'Mic/Aux',
           'threshold': -29.2},
          'type': 'compressor_filter'}]
        """
        response = self.client.call(obs.requests.GetSourceFilters(sourceName=ORIGINAL_STREAM_SOURCE_NAME))

        if not response.status:
            raise RuntimeError(
                f"E PYSERVER::OBS::setup_sidechain(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

        filters = response.getFilters()  # [... {'enabled': ..., 'name': ..., 'settings': ..., 'type': ...} ...]

        sourceName = ORIGINAL_STREAM_SOURCE_NAME
        filterName = COMPRESSOR_FILTER_NAME
        filterType = "compressor_filter"
        filterSettings = {
            "sidechain_source": TS_INPUT_NAME,
        }
        if ratio is not None:
            filterSettings["ratio"] = ratio
        if release_time is not None:
            filterSettings["release_time"] = release_time
        if threshold is not None:
            filterSettings["threshold"] = threshold
        if output_gain is not None:
            filterSettings["output_gain"] = output_gain

        if all([f["name"] != COMPRESSOR_FILTER_NAME for f in filters]):  # if no compressor input added before
            response = self.client.call(
                obs.requests.AddFilterToSource(
                    sourceName=sourceName,
                    filterName=filterName,
                    filterType=filterType,
                    filterSettings=filterSettings,
                )
            )
            if not response.status:
                raise RuntimeError(
                    f"E PYSERVER::OBS::setup_sidechain(): " f"datain: {response.datain}, dataout: {response.dataout}"
                )
        else:  # if compressor was already added before
            response = self.client.call(
                obs.requests.SetSourceFilterSettings(
                    sourceName=sourceName,
                    filterName=filterName,
                    filterSettings=filterSettings,
                )
            )
            if not response.status:
                raise RuntimeError(
                    f"E PYSERVER::OBS::setup_sidechain(): " f"datain: {response.datain}, dataout: {response.dataout}"
                )

    def set_stream_settings(self, server, key, type="rtmp_custom"):
        """
        Sets the streaming settings of the server
        """
        # TODO: validate server and key
        settings_ = {"server": server, "key": key}

        response = self.client.call(obs.requests.SetStreamSettings(type=type, settings=settings_, save=True))
        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::set_stream_settings(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def start_streaming(self):
        """
        Starts the streaming
        """
        response = self.client.call(obs.requests.StartStreaming())
        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::start_streaming(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def stop_streaming(self):
        """
        Starts the streaming
        """
        response = self.client.call(obs.requests.StopStreaming())
        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::stop_streaming(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def set_source_mute(self, mute):
        self.set_mute(ORIGINAL_STREAM_SOURCE_NAME, mute)

    def set_ts_mute(self, mute):
        self.set_mute(TS_INPUT_NAME, mute)

    def set_mute(self, source_name, mute):
        response = self.client.call(obs.requests.SetMute(source=source_name, mute=mute))
        if not response.status:
            raise Exception(f"E PYSERVER::OBS::set_mute(): " f"datain: {response.datain}, dataout: {response.dataout}")

    def _run_media(self, path, source_name):
        scene_name = self.obsws_get_current_scene_name()
        self.delete_source(source_name, scene_name)

        response = self.client.call(
            obs.requests.CreateSource(
                sourceName=source_name,
                sourceKind="ffmpeg_source",
                sceneName=scene_name,
                sourceSettings={"local_file": path},
            )
        )
        if not response.status:
            obs_fire("E", "OBS", "_run_media", "CreateSource", response.datain, response.dataout)

        response = self.client.call(obs.requests.SetMediaTime(sourceName=source_name, timestamp=0))
        if not response.status:
            obs_fire("E", "OBS", "_run_media", "SetMediaTime", response.datain, response.dataout)

        # request = obs.requests.SetAudioMonitorType(sourceName=source_name, monitorType="monitorAndOutput")
        # response = self.client.call(request)
        # if not response.status:
        #     obs_fire("E", "OBS", "_run_media", "SetAudioMonitorType", response.datain, response.dataout)

    def delete_source(self, source_name, scene_name=None):
        """
        Removes all inputs with name `source_name`
        """
        item_id, scene_name = self.get_item_from_sourcename(source_name, scene_name)
        if item_id is not None:
            self.delete_scene_item(item_id=item_id, source_name=source_name, scene_name=scene_name)

    def source_exists(self, source_name, scene_name=None):
        """
        Checks if the item with source name `source_name` exists
        """
        item_id, scene_name = self.get_item_from_sourcename(source_name, scene_name)
        return item_id is not None

    def get_item_from_sourcename(self, source_name, scene_name=None):
        """
        Returns (item_id, scene_name) given a source_name
        """
        scene_names = [scene_name] if scene_name is not None \
            else [x["name"] for x in self.obsws_get_scene_list()]

        for scene_name in scene_names:
            items = self.obsws_get_scene_item_list(scene_name=scene_name)
            for item in items:
                item_id, source_name_ = item["itemId"], item["sourceName"]
                if source_name_ == source_name:
                    return item_id, scene_name
        return None, None

    def delete_scene_item(self, item_id, source_name, scene_name):
        """
        Removes an input given item_id, source_name and scene_name
        """
        item = {"id": item_id, "name": source_name}
        response = self.client.call(obs.requests.DeleteSceneItem(scene=scene_name, item=item))
        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::delete_scene_item(): " f"datain: {response.datain}, dataout: {response.dataout}"
            )

    def on_event(self, message):
        # we handle the error here for the reason of this function is called from another thread
        # from obs-websocket-py library, and I am not sure of the exception will be handled properly there
        try:
            if message.name == "MediaEnded":
                self.on_media_ended(message)
        except BaseException as ex:
            print(f"E PYSERVER::OBS::on_event(): {ex}")

    def on_media_ended(self, message):
        """
        Fired on event MediaEnded. Fires Exception if could't delete a scene item or unmute a source
        """
        source_name = message.getSourceName()

        if source_name in self.media_queue and self.obsws_get_current_scene_name() == MEDIA_SCENE_NAME:
            response = self.client.call(obs.requests.SetCurrentScene(scene_name=MAIN_SCENE_NAME))
            if not response.status:
                raise Exception(
                    f"E PYSERVER::OBS::on_media_ended(): " f"datain: {response.datain}, dataout: {response.dataout}"
                )

    def obsws_get_current_scene_name(self):
        return self.client.call(obs.requests.GetCurrentScene()).getName()

    def obsws_get_sources_list(self):
        """
        :return: list of [... {'name': '...', 'type': '...', 'typeId': '...'}, ...]
        """
        return self.client.call(obs.requests.GetSourcesList()).getSources()

    def obsws_get_scene_list(self):
        """
        :return: list of [... {'name': '...', 'sources': [{..., 'id': n, ..., 'name': '...', ...}, ...]}, ...]
        """
        return self.client.call(obs.requests.GetSceneList()).getScenes()

    def obsws_get_scene_item_list(self, scene_name):
        """
        :param scene_name: name of the scene
        :return: list of [... {'itemId': n, 'sourceKind': '...', 'sourceName': '...', 'sourceType': '...'}, ...]
        """
        return self.client.call(obs.requests.GetSceneItemList(sceneName=scene_name)).getSceneItems()
