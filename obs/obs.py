import obswebsocket as obs
import obswebsocket.requests
from util import CallbackThread

# ORIGINAL_STREAM_SOURCE_NAME = "original_stream"
# TS_INPUT_NAME = "ts_input"
# MEDIA_INPUT_NAME = "media"
# TRANSITION_INPUT_NAME = "transition"

# MAIN_SCENE_NAME = "main"
# MEDIA_SCENE_NAME = "media"
# COMPRESSOR_FILTER_NAME = "sidechain"


# def obs_fire(type, cls, cls_foo, comment, datain, dataout):
#     raise Exception(f"{type} PYSERVER::{cls}::{cls_foo}(): {comment} " f"datain: {datain}, dataout: {dataout}")


class OBS:
    MAIN_STREAM_SOURCE_NAME = "original_stream"
    MAIN_STREAM_SOURCE_NAME_REFRESH_SOURCE = "original_stream_update"
    MAIN_MEDIA_NAME = "media"
    TEAMSPEAK_SOURCE_NAME = "ts_input"
    TRANSITION_INPUT_NAME = "transition"
    COMPRESSOR_FILTER_NAME = "sidechain"
    TS_LIMITER_FILTER_NAME = "ts_limiter"
    TS_GAIN_FILTER_NAME = "ts_gain"
    MAIN_SCENE_NAME = "main"
    PLAYBACK_MODE_FORCE = "force"  # stop any media being played right now, and play media specified
    PLAYBACK_MODE_CHECK_ANY = "check_any"  # if any video is being played, skip
    PLAYBACK_MODE_CHECK_SAME = "check_same"  # if the same video is being played, skip, otherwise play

    def __init__(self, client):
        self.client = client

        self.transition_name = "Cut"
        self.transition_path = ""
        self.transition_point = 0

        self.media_cb_thread = CallbackThread()
        self.media_cb_thread.start()

        self.client.register(self.on_event)

        self._current_media_played = None

    def setup_scene(self, scene_name=None, switch_scene=True):
        """
        Creates (if not been created) a scene called `scene_name` and sets it as a current scene.
        If it has been created, removes all the sources inside the scene and sets it as a current one.
        """
        if not scene_name:
            scene_name = OBS.MAIN_SCENE_NAME

        try:
            self.set_mute(source_name="Desktop Audio", mute=True)
        except Exception as ex:
            # TODO: logger
            print(f"W OBS::setup_scene(): couldn't mute 'Desktop Audio', details: {ex}")

        # list existing scenes
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
        scenes = self.obsws_get_scenes_list()
        for scene_info in scenes:
            scene_name = scene_info["name"]
            self.clear_scene(scene_name)

    def clear_scene(self, scene_name):
        """
        Removes all the items from a specified scene
        """
        items = self.obsws_get_scene_items_list(scene_name=scene_name)
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

    def add_or_replace_stream(self, source_url, stream_name, scene_name=None):
        """
        Adds or replaces stream source (rtmp, srt).
        `stream_name` allows adding multiple stream sources with different names.
        :param source_url: rtmp or srt url
        :param stream_name: source name
        :param scene_name: optional
        :return: None if ok, otherwise throws RuntimeError
        """
        self.delete_source_if_exist(source_name=stream_name)

    def run_media(self, path, mode=None, source_name=None, on_start=None, on_error=None, on_finish=None):
        """
        Mutes original media, adds and runs the media located at `path`, and appends a listener which removes
        the media when it has finished. Fires Exception when couldn't add or mute a source.
        :param path: media file path
        :param mode: media play mode. Possible values:
           - 'force' - stop any media being played right now, and play
                       media specified (default value)
           - 'check_any' - if any video is being played, skip
           - 'check_same' - if the same video is being played, skip, otherwise - play
        :param source_name: name of the source in OBS. Leave None to use default value
        :param on_start: callback which is being played when the video begins. on_start()
        :param on_error: callback which is being played when an error is being thrown. on_error(ex)
        :param on_finish: callback which is being played when the video is finished. on_finish()
        :return: None if ok, otherwise throws Exception
        """
        if not source_name:
            source_name = OBS.MAIN_MEDIA_NAME
        if not mode:
            mode = OBS.PLAYBACK_MODE_FORCE

        if mode == OBS.PLAYBACK_MODE_CHECK_ANY and self._current_media_played is not None:
            return
        if mode == OBS.PLAYBACK_MODE_CHECK_SAME and self._current_media_played == path:
            return

        def media_play_foo(filename):
            """
            Removes transition, runs media
            """
            # delay for self.transition_point / 1000
            try:
                self.delete_source_if_exist(source_name)  # remove media, if any has been played before
                self._run_media(filename, source_name)
                self.delete_source_if_exist(source_name=OBS.TRANSITION_INPUT_NAME)
                duration = self.client.call(obs.requests.GetMediaDuration(sourceName=source_name)).getMediaDuration()
                self.media_cb_thread.append_callback(media_end_foo, (duration / 1000) + 1, cb_type=source_name)

                if on_start is not None and callable(on_start):
                    on_start(filename, (duration / 1000) + 1)
            except Exception as ex:
                self.delete_source_if_exist(source_name)
                self._current_media_played = None
                self.media_cb_thread.delete_cb_type(cb_type=source_name)

                if on_error is not None and callable(on_error):
                    on_error(ex)

        def media_end_foo():
            """
            Deletes media, runs stinger if needed
            """
            self.delete_source_if_exist(source_name=source_name)
            if self.transition_name == "Stinger":
                self._run_media(self.transition_path, OBS.TRANSITION_INPUT_NAME)
                self.media_cb_thread.append_callback(
                    transition_end_foo, self.transition_point / 1000, cb_type=source_name
                )
            elif self.transition_name == "Cut":
                self.media_cb_thread.append_callback(transition_end_foo, 0, cb_type=source_name)

        def transition_end_foo():
            """
            On transition (stinger) finish handler.
            Removes stinger, mutes source and teamspeak
            """
            try:
                self._current_media_played = None
                self.delete_source_if_exist(source_name=OBS.TRANSITION_INPUT_NAME)
                self.media_cb_thread.delete_cb_type(cb_type=source_name)

                if on_finish is not None and callable(on_finish):
                    on_finish()
            except Exception as ex:
                self.media_cb_thread.delete_cb_type(cb_type=source_name)
                if on_error is not None and callable(on_error):
                    on_error(ex)

        self.media_cb_thread.delete_cb_type(cb_type=source_name)  # clean callbacks queue

        if self.transition_name == "Stinger":
            raise RuntimeError("Stingers are not supported after refactoring")

        self._current_media_played = path
        self.media_cb_thread.append_callback(media_play_foo, self.transition_point / 1000,
                                             args=(path, ), cb_type=source_name)

    def stop_media(self, source_name=None):
        """
        Stops playing media (removes input). Don't use OBS.delete_source_if_exist to stop
        media which has been played using OBS.run_media!
        :param source_name: input name in OBS. Leave None to use default media name
        :return: None if ok, otherwise throws Exception
        """
        if not source_name:
            source_name = self.MAIN_MEDIA_NAME

        self._current_media_played = None
        self.delete_source_if_exist(source_name=source_name)
        self.delete_source_if_exist(source_name=OBS.TRANSITION_INPUT_NAME)
        self.media_cb_thread.delete_cb_type(cb_type=source_name)

    def setup_transition(self, transition_name="Cut", transition_settings=None):
        """
        :param transition_name: "Cut" ("Stinger" is not supported)
        :param transition_settings:
        e.g.:
        {'path': '/home/user/common/_Sting_RT.mp4',
         'transition_point': 3000}
        :return:
        """
        if transition_name == "Stinger":
            raise ValueError("Stinger is not supported after refactoring")
        else:
            if "transition_point" not in transition_settings:
                transition_settings["transition_point"] = 0
            self.transition_point = int(transition_settings["transition_point"])

        self.transition_name = transition_name

    def set_stream_settings(self, server, key, type="rtmp_custom"):
        """
        Sets the streaming settings of the server
        """
        # TODO: validate server and key
        settings_ = {"server": server, "key": key}

        response = self.client.call(obs.requests.SetStreamSettings(type=type, settings=settings_, save=True))
        if not response.status:
            raise Exception(f"OBS::set_stream_settings(): datain: {response.datain}, dataout: {response.dataout}")

    def start_streaming(self):
        """
        Starts the streaming
        """
        response = self.client.call(obs.requests.StartStreaming())
        if not response.status:
            raise Exception(f"OBS::start_streaming(): datain: {response.datain}, dataout: {response.dataout}")

    def stop_streaming(self):
        """
        Stops the streaming
        """
        response = self.client.call(obs.requests.StopStreaming())
        if not response.status:
            raise Exception(
                f"E PYSERVER::OBS::stop_streaming(): datain: {response.datain}, dataout: {response.dataout}"
            )

    def set_mute(self, source_name, mute):
        """
        Sets source's mute status
        :param source_name:
        :param mute: True/False
        :return:
        """
        response = self.client.call(obs.requests.SetMute(source=source_name, mute=mute))
        if not response.status:
            raise RuntimeError(f"OBS::set_mute(): datain: {response.datain}, dataout: {response.dataout}")

    def rename_input(self, name_from, name_to):
        """
        Renames source
        :param name_from: old name
        :param name_to: new name
        :return:
        """
        response = self.client.call(obs.requests.SetSourceName(sourceName=name_from, newName=name_to))
        if not response.status:
            raise RuntimeError(f"OBS::rename_input(): datain {response.datain}, dataout: {response.dataout}")

    # def set_source_mute(self, mute):
    #     self.set_mute(OBS.MAIN_STREAM_SOURCE_NAME, mute)
    #
    # def set_ts_mute(self, mute):
    #     self.set_mute(OBS.TEAMSPEAK_SOURCE_NAME, mute)

    def _run_media(self, path, source_name, timestamp=0):
        scene_name = self.obsws_get_current_scene_name()
        self.delete_source_if_exist(source_name, scene_name)

        response = self.client.call(
            obs.requests.CreateSource(
                sourceName=source_name,
                sourceKind="ffmpeg_source",
                sceneName=scene_name,
                sourceSettings={"local_file": path},
            )
        )
        if not response.status:
            raise RuntimeError(f"OBS::_run_media(): datain: {response.datain}, dataout: {response.dataout}")

        response = self.client.call(obs.requests.SetMediaTime(sourceName=source_name, timestamp=timestamp))
        if not response.status:
            raise RuntimeError(f"OBS::_run_media(): datain: {response.datain}, dataout: {response.dataout}")

        # request = obs.requests.SetAudioMonitorType(sourceName=source_name, monitorType="monitorAndOutput")
        # response = self.client.call(request)
        # if not response.status:
        #     obs_fire("E", "OBS", "_run_media", "SetAudioMonitorType", response.datain, response.dataout)

    def delete_source_if_exist(self, source_name, scene_name=None):
        """
        Removes all inputs with name `source_name`
        """
        if not scene_name:
            scene_name = OBS.MAIN_SCENE_NAME

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
        scene_names = [scene_name] if scene_name is not None else [x["name"] for x in self.obsws_get_scenes_list()]

        for scene_name in scene_names:
            items = self.obsws_get_scene_items_list(scene_name=scene_name)
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
        # try:
        #     if message.name == "MediaEnded":
        #         pass
        # except BaseException as ex:
        #     print(f"E PYSERVER::OBS::on_event(): {ex}")
        pass

    def obsws_get_current_scene_name(self):
        return self.client.call(obs.requests.GetCurrentScene()).getName()

    def obsws_get_sources_list(self):
        """
        :return: list of [... {'name': '...', 'type': '...', 'typeId': '...'}, ...]
        """
        return self.client.call(obs.requests.GetSourcesList()).getSources()

    def obsws_get_scenes_list(self):
        """
        :return: list of [... {'name': '...', 'sources': [{..., 'id': n, ..., 'name': '...', ...}, ...]}, ...]
        """
        return self.client.call(obs.requests.GetSceneList()).getScenes()

    def obsws_get_scene_items_list(self, scene_name):
        """
        :param scene_name: name of the scene
        :return: list of [... {'itemId': n, 'sourceKind': '...', 'sourceName': '...', 'sourceType': '...'}, ...]
        """
        return self.client.call(obs.requests.GetSceneItemList(sceneName=scene_name)).getSceneItems()
