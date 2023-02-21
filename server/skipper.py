# REFACTORING: build a new class which manages all the functionality listed below
import json
import os
from datetime import datetime, timedelta
from threading import RLock
from typing import List, Dict

import socketio
from flask import Flask
from pydantic import BaseModel
from socketio.exceptions import ConnectionError

from deployment import Spawner, IPDict
from googleapi import OBSGoogleSheets, TimingGoogleSheets
from models import MinionSettings, VmixPlayer, Registry, State, TimingEntry, orjson_dumps
from obs import OBS
from util import ExecutionStatus, WebsocketResponse, CallbackThread

MINION_WS_PORT = 6000


class Skipper:
    class Infrastructure:
        def __init__(self, skipper):
            self.spawner: Spawner = Spawner()
            self.skipper = skipper

        def _ensure_minions(self):
            for lang, ip in self.spawner.ip_dict.ip_list():
                # with self.skipper.registry_lock:
                if lang not in self.skipper.registry.minion_configs:
                    raise KeyError(f"No lang {lang} found in google sheets, but passed in `ip_langs`")
                self.skipper.registry.minion_configs[lang].addr_config.minion_server_addr = ip
                # if Minion instance have not been created yet
                if lang not in self.skipper.minions:
                    self.skipper.minions[lang] = Skipper.Minion(
                        minion_ip=ip, lang=lang, ws_port=MINION_WS_PORT, skipper=self.skipper
                    )  # create
                # else if lang or ip has changed
                if self.skipper.minions[lang].minion_ip != ip or self.skipper.minions[lang].lang != lang:
                    del self.skipper.minions[lang]  # delete old version
                    # and replace with updated one
                    self.skipper.minions[lang] = Skipper.Minion(
                        minion_ip=ip, lang=lang, ws_port=MINION_WS_PORT, skipper=self.skipper
                    )

        def activate_registry(self) -> ExecutionStatus:
            # with self.skipper.registry_lock:
            if self.skipper.registry.infrastructure_lock:
                self.skipper.registry.server_status = State.running
                return ExecutionStatus(True, "Infrastructure is locked")

            with self.skipper.infrastructure_lock:
                try:
                    # with self.skipper.registry_lock:
                    self.skipper.registry.server_status = State.initializing
                    langs = self.skipper.registry.list_langs()
                    self.spawner.ensure_langs(langs=langs, wait_for_provision=True)  # [... [lang, ip], ...]
                except Exception as ex:
                    # TODO: log
                    # with self.skipper.registry_lock:
                    self.skipper.registry.revert_server_state()
                    return ExecutionStatus(False, message=f"Something happened while deploying minions: {ex}")

                # lang_ips = self.spawner.ip_dict.ip_list()
                try:
                    self._ensure_minions()
                except ConnectionError as ex:
                    # with self.skipper.registry_lock:
                    self.skipper.registry.revert_server_state()
                    return ExecutionStatus(False, f"Something happened while creating Minion instances. Details: {ex}")

            # with self.skipper.registry_lock:
            self.skipper.registry.server_status = State.running
            self.skipper.registry.infrastructure_lock = True
            return ExecutionStatus(True)

        def delete_servers(self) -> ExecutionStatus:
            with self.skipper.infrastructure_lock:
                return ExecutionStatus(self.spawner.cleanup())

        def set_ip_langs(self, ip_langs: Dict[str, str]) -> ExecutionStatus:
            """
            :param ip_langs: dict of (ip: lang)
            :return:
            """
            try:
                self.spawner = Spawner.from_json(IPDict(ip_langs=ip_langs).json())
                self._ensure_minions()
                self.skipper.registry.server_status = State.running
                self.skipper.registry.infrastructure_lock = True
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Couldn't set ip_langs. Details: {ex}")

        @classmethod
        def from_json(cls, skipper, json_dump):
            infra = Skipper.Infrastructure(skipper)
            infra.spawner = Spawner.from_json(json_dump=json_dump)
            return infra

        def json(self):
            return self.spawner.json()

    class OBSSheets:
        def __init__(self, skipper):
            self.obs_sheets = OBSGoogleSheets()
            self.skipper = skipper

        def setup(self, sheet_url, sheet_name) -> ExecutionStatus:
            # with self.skipper.registry_lock:
            try:
                self.obs_sheets.set_sheet(sheet_url, sheet_name)
                self.skipper.registry.obs_sheet_url = sheet_url
                self.skipper.registry.obs_sheet_name = sheet_name
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Couldn't set up obs sheet config.\nDetails: {ex}")

        def pull(self, langs: List[str] = None) -> ExecutionStatus:
            # with self.skipper.registry_lock:
            try:
                if not self.obs_sheets.setup_status:
                    return ExecutionStatus(False, f"OBS sheets has not been set up yet")

                minion_configs: Dict[str, MinionSettings] = self.obs_sheets.pull()

                if langs is not None:  # if need to pull only specified langs
                    minion_configs = {lang: minion_configs[lang] for lang in minion_configs if lang in langs}
                else:
                    for lang in self.skipper.registry.list_langs():  # list langs in registry
                        if lang not in minion_configs:  # if lang has been deleted in google sheets
                            self.skipper.registry.delete_minion(lang)

                for lang in minion_configs:
                    self.skipper.registry.update_minion(lang, minion_configs[lang])
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, str(ex))

        def push(self) -> ExecutionStatus:
            # with self.skipper.registry_lock:
            try:
                if not self.obs_sheets.setup_status:
                    return ExecutionStatus(False, f"setup_status of obs_sheets is False")
                self.obs_sheets.push(self.skipper.registry.minion_configs)
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while pushing obs_config info. Details: {ex}")

    class Minion:
        def __init__(self, minion_ip, lang, skipper, ws_port=MINION_WS_PORT):
            self.minion_ip = minion_ip
            self.ws_port = ws_port
            self.lang = lang
            self.skipper: Skipper = skipper
            self.sio = socketio.Client()
            self.connect()

        def __del__(self):
            self.close()

        def connect(self):
            try:
                self.sio.connect(f"http://{self.minion_ip}:{self.ws_port}")
                self._register_event_handlers()
            except Exception as ex:
                # TODO: log
                raise ConnectionError(f"Connection error for ip {self.minion_ip} lang {self.lang}. Details:\n{ex}")

        def _register_event_handlers(self):
            self.sio.on("on_gdrive_files_changed", handler=self._on_gdrive_files_changed)

        def _on_gdrive_files_changed(self, data):
            with self.skipper.registry_lock:
                self.skipper.registry.gdrive_files[self.lang] = orjson.loads(data)

        def close(self):
            self.sio.disconnect()

        def apply_config(self, minion_config: MinionSettings) -> WebsocketResponse:
            return self.command(command="set config", details={"info": minion_config.json()})

        def command(self, command, details=None) -> WebsocketResponse:
            response = WebsocketResponse()

            self.sio.emit(
                "command", data=json.dumps({"command": command, "details": details}), callback=response.callback
            )

            return response

        def json(self):
            return json.dumps({"minion_ip": self.minion_ip, "ws_port": self.ws_port, "lang": self.lang})

        @classmethod
        def from_json(cls, json_dump, skipper):
            data = json.loads(json_dump)
            return Skipper.Minion(minion_ip=data["minion_ip"], ws_port=data["ws_port"],
                                  lang=data["lang"], skipper=skipper)

    class Timing:
        def __init__(self, skipper):
            self.sheets = TimingGoogleSheets()
            self.skipper = skipper
            self.cb_thread = CallbackThread()
            self.cb_thread.start()

        def setup(self, sheet_url, sheet_name) -> ExecutionStatus:
            """
            Sets up the google sheet settings
            :param sheet_url: url of google sheets
            :param sheet_name: sheet name
            :return: ExecutionStatus
            """
            try:
                self.sheets.set_sheet(sheet_url, sheet_name)
                self.skipper.registry.timing_sheet_url = sheet_url
                self.skipper.registry.timing_sheet_name = sheet_name
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Couldn't set up timing sheet config.\nDetails: {ex}")

        def _sync_callbacks(self) -> ExecutionStatus:
            """
            Synchronizes the timing, delays, etc.
            Every time you modify skipper.registry.timing_list -> you should call this function
            :return: ExecutionStatus
            """

            def foo_maker(skipper: Skipper, id: int):
                def foo() -> ExecutionStatus:
                    entry: TimingEntry = skipper.registry.timing_list[id]
                    if not entry.is_enabled or entry.is_played:  # if disabled or already has been played
                        return ExecutionStatus(True)

                    status: ExecutionStatus = skipper.command.exec(
                        command="play media",
                        details={"name": "name", "search_by_num": True, "mode": OBS.PLAYBACK_MODE_CHECK_SAME},
                    )

                    entry.is_played = True
                    return status

                return foo

            try:
                self.cb_thread.delete_cb_type("timing")

                time_from_start = self.get_current_timedelta()
                for i, entry in enumerate(self.skipper.registry.timing_list):
                    self.cb_thread.append_callback(
                        foo=foo_maker(self.skipper, i),
                        delay=(entry.timestamp - time_from_start).total_seconds(),
                        cb_type="timing",
                    )
                return ExecutionStatus()
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while synchronizing the timing. Details: {ex}")

        def get_current_timedelta(self) -> timedelta:
            if self.skipper.registry.timing_start_time is None:
                return timedelta(days=-999)
            return datetime.now() - self.skipper.registry.timing_start_time

        def pull(self) -> ExecutionStatus:
            """
            Pulls and applies timing from google sheets. Returns ExecutionStatus(False) if sheets
            have not been set up yet. Note that this function does not reset timing, current timestamp
            of the timing (if it is running) remains. To reset the timing (timestamp) use stop() function.
            :return: ExecutionStatus
            """
            try:
                if not self.sheets.setup_status:
                    return ExecutionStatus(False, f"setup_status of timing_sheets is False")

                timing_df = self.sheets.pull()

                # if timing_delta < 0 -> timing has not been started yet
                timing_delta = self.get_current_timedelta()  # now() - timing_start_time
                self.skipper.registry.timing_list = [
                    TimingEntry(
                        name=name, timestamp=timestamp, is_enabled=True, is_played=timing_delta > timestamp
                    )
                    for timestamp, name in timing_df.values
                ]
                return self._sync_callbacks()
            except Exception as ex:
                return ExecutionStatus(False, str(ex))

        def run(self, countdown: timedelta = None, daytime: datetime = None) -> ExecutionStatus:
            """
            Runs the timing. Note that the timing should be pulled before calling `run()`.
            If neither countdown nor daytime not specified -> runs the timing instantly.
            :param countdown: If specified, daytime will be ignored
            :param daytime: system time to start the timing. Format: hh:mm:ss
            :return: ExecutionStatus
            """
            try:
                if not self.sheets.setup_status:
                    return ExecutionStatus(False, f"setup_status of timing_sheets is False")
                if countdown is not None:
                    self.skipper.registry.timing_start_time = datetime.now() + countdown
                elif daytime is not None:
                    self.skipper.registry.timing_start_time = daytime
                else:  # if both are None
                    self.skipper.registry.timing_start_time = datetime.now()
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while running the timing. Details: {ex}")
            return self._sync_callbacks()

        def stop(self) -> ExecutionStatus:
            try:
                self.skipper.command("stop media")
                # with self.skipper.registry_lock:
                self.skipper.registry.timing_start_time = None
                self.cb_thread.delete_cb_type("timing")
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while stopping the timing. Details: {ex}")

        def remove(self) -> ExecutionStatus:
            status = self.stop()
            self.skipper.registry.timing_list = []
            return status

    class Command:
        """
        Command structure for a Skipper:
        {
            "command": "play media|media stop|set ts volume|get ts volume|...",
            "details": ... may be dict, str, list, int, bool, etc.
        }
        Example:
        {
            "command": "play media",
            "details": {"name": "01_video.mp4", "search_by_num": "1", "mode": "check_same"}
        }
        Note that the command is a json string, which being parsed by Streamer.Command class

        Command response has the following structure (json):
        {
            "status": True/False,
            "return_value": ...,
            "details": ...
        }
        """

        def __init__(self, skipper):
            self.skipper: Skipper = skipper

        @classmethod
        def valid(cls, command: str):
            """
            See valid command structure in class description
            """
            try:
                command = json.loads(command)
                if not isinstance(command, dict):
                    return False
                if "command" not in command:
                    return False
            except:
                return False
            return True

        def _check_caller(self, ip, command, details=None, lang=None) -> ExecutionStatus:
            """
            Checks if the command is allowed given the command, ip, lang and details.
            If returned ExecutionStatus(True) - the command is allowed, otherwise not allowed
            """
            # if the server is sleeping - only allow the following commands
            if self.skipper.registry.server_status == State.sleeping:
                if command in ("pull config", "get info", "infrastructure unlock"):
                    return ExecutionStatus(True)
                else:
                    return ExecutionStatus(False)
            # if the server is initializing or disposing - only allow 'get info' command
            if self.skipper.registry.server_status in (State.initializing, State.disposing):
                if command == "get info":
                    return ExecutionStatus(True)
                else:
                    return ExecutionStatus(False, f"Server is '{self.skipper.registry.server_status}'...")
            # check vmix player's ip
            if self.skipper.registry.active_vmix_player == "*":
                return ExecutionStatus(True)
            if command in ("play media", "run timing") and ip != self.skipper.registry.active_vmix_player:
                return ExecutionStatus(False, f"Command '{command}' is not allowed from {ip}")
            # True - allows the command
            return ExecutionStatus(True)

        def _fix_infrastructure_lock(self):
            """
            Checks if the infrastructure lock's state is valid for current server status
            """
            if self.skipper.registry.server_status == State.sleeping:
                self.skipper.registry.infrastructure_lock = False

        def exec_raw(self, command_raw, environ=None) -> ExecutionStatus:
            """
            Command structure for a Skipper:
            {
                "command": "pull config|play media|media stop|set ts volume|get ts volume|...",
                "details": ... may be dict, str, list, int, bool, etc. - optional
                "lang": ...
            }
            :return: ExecutionStatus
            """
            if not Skipper.Command.valid(command_raw):
                raise ValueError(f"SKIPPER: Validation error. Invalid command '{command_raw}'")

            command = json.loads(command_raw)
            if "details" not in command:
                command["details"] = {}
            if "lang" not in command:
                command["lang"] = "*"
            command, details, lang = command["command"], command["details"], command["lang"]

            return self.exec(command, details=details, lang=lang, environ=environ)

        def exec(self, command, details=None, lang=None, environ=None) -> ExecutionStatus:
            """
            Command structure for a Streamer:
            {
                "command": "play media|media stop|set ts volume|get ts volume|...",
                "details": ... may be dict, str, list, int, bool, etc.
            }
            :return: ExecutionStatus
            """
            if lang is None:
                lang = "*"  # langs
            # prefetch minion_configs for specified langs, for the purpose of not duplicating the code
            minion_configs: List[MinionSettings] = [
                minion_config
                for lang_, minion_config in self.skipper.registry.minion_configs.items()
                if (lang == "*" or lang_ == lang)
            ]

            remote_addr = environ["REMOTE_ADDR"] if environ and "REMOTE_ADDR" in environ else "*"
            if not self._check_caller(remote_addr, command):
                return self._check_caller(remote_addr, command)
            self._fix_infrastructure_lock()  # if the server is sleeping - infrastructure should not be locked

            try:
                if command == "pull config":
                    # details:
                    # note that all the parameters are optional
                    # {
                    #   "sheet_url": "url",
                    #   "sheet_name": "name",
                    #   "langs": ["lang1", "lang2", ...],
                    #   "ip_langs": {... "ip": "lang", ...}
                    # }
                    sheet_url = None if not details or "sheet_url" not in details else details["sheet_url"]
                    sheet_name = None if not details or "sheet_name" not in details else details["sheet_name"]
                    # check if details is not None and has "langs" and details["lang"] is a list of strings
                    langs = (
                        None  # None - means all langs
                        if (
                                (not details)
                                or ("langs" not in details)
                                or (not isinstance(details["langs"], list))
                                or (not all([isinstance(obj, str) for obj in details["langs"]]))
                        )
                        else details["langs"]  # the code above validates details["langs"]
                    )
                    # pull_obs_config() manages registry lock itself
                    status: ExecutionStatus = self.pull_obs_config(
                        sheet_url=sheet_url, sheet_name=sheet_name, langs=langs
                    )
                    if not status:
                        return status

                    # if "ip_langs" has been specified - set infrastructure's ip_langs and lock environment
                    if "ip_langs" in details and details["ip_langs"] and not self.skipper.registry.infrastructure_lock:
                        status: ExecutionStatus = self.skipper.infrastructure.set_ip_langs(details["ip_langs"])
                        if not status:
                            return status
                    return self.skipper.activate_registry()
                elif command == "dispose":
                    return self.delete_minions()
                elif command == "get info":
                    return ExecutionStatus(True,
                                           serializable_object=orjson_dumps({"registry": self.skipper.registry.dict()}))
                elif command in (
                        "set stream settings",
                        "set teamspeak offset",
                        "set teamspeak volume",
                        "set source volume",
                        "set sidechain settings",
                        "set transition settings",
                ):
                    return self.set_info(command=command, details=details, lang=lang, environ=environ)
                elif command == "infrastructure lock":
                    # with self.registry_lock:
                    self.skipper.registry.infrastructure_lock = True
                    return ExecutionStatus(True)
                elif command == "infrastructure unlock":
                    # with self.registry_lock:
                    self.skipper.registry.infrastructure_lock = False
                    return ExecutionStatus(True)
                elif command == "vmix players add":
                    # details: {"ip": "ip address", "name": "... Moscow ..."}
                    if not details or "ip" not in details or "name" not in details:  # validate
                        return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")
                    if details["ip"] == "*":
                        return ExecutionStatus(False, f"Adding '*' is not allowed")
                    # with self.registry_lock:
                    # add vmix player into registry
                    self.skipper.registry.vmix_players[details["ip"]] = VmixPlayer(name=details["name"], active=False)
                    return ExecutionStatus(True)
                elif command == "vmix players remove":
                    # details: {"ip": "ip address"}
                    if not details or "ip" not in details:
                        return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")
                    # with self.registry_lock:
                    if details["ip"] not in self.skipper.registry.vmix_players:
                        return ExecutionStatus(True)
                    if details["ip"] == "*":
                        return ExecutionStatus(False, "Removing '*' is not allowed")

                    # with self.registry_lock:
                    self.skipper.registry.vmix_players.pop(details["ip"])
                    return ExecutionStatus(True)
                # elif command == "vmix players list":
                #     # no details needed
                #     # returns ExecutionStatus(True, serializable_object={ip: vmix_player.dict(), ...})
                #     # with self.registry_lock:
                #     return ExecutionStatus(True, serializable_object={
                #         ip: vmix_player.dict() for ip, vmix_player in self.skipper.registry.vmix_players.items()
                #     })
                elif command == "vmix players set active":
                    # details: {"ip": "ip address"}. ip address may be '*' - all active
                    if not details or "ip" not in details:
                        return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")

                    # with self.registry_lock:
                    self.skipper.registry.active_vmix_player = details["ip"]
                    for ip in self.skipper.registry.vmix_players:
                        self.skipper.registry.vmix_players[ip].active = self.skipper.registry.active_vmix_player == ip
                    return ExecutionStatus(True)
                elif command == "start streaming":
                    for minion_config in minion_configs:
                        minion_config.stream_on.value = True
                    return self.skipper.activate_registry()
                elif command == "stop streaming":
                    for minion_config in minion_configs:
                        minion_config.stream_on.value = False
                    return self.skipper.activate_registry()
                elif command == "pull timing":
                    # details:
                    # parameters are optional
                    # {"sheet_url": "url", "sheet_name": "name"}
                    sheet_url = None if not details or "sheet_url" not in details else details["sheet_url"]
                    sheet_name = None if not details or "sheet_name" not in details else details["sheet_name"]
                    # pull_obs_config() manages registry lock itself
                    if sheet_url and sheet_name:
                        status: ExecutionStatus = self.skipper.timing.setup(sheet_url=sheet_url, sheet_name=sheet_name)
                        if not status:
                            return status
                    return self.skipper.timing.pull()
                elif command == "run timing":
                    # details:
                    # parameters are optional
                    # {"countdown": "hh24:mm:ss", "daytime": "hh24:mm:ss"}
                    countdown, daytime = None, None
                    if details and "countdown" in details and details["countdown"]:
                        try:
                            dt = datetime.strptime(details["countdown"], "%H:%M:%S")
                            countdown = timedelta(hours=dt.hour, minutes=dt.minute, seconds=dt.second)
                        except Exception as ex:
                            return ExecutionStatus(False, f"Invalid 'countdown'. Details: {ex}")
                    if details and "daytime" in details and details["daytime"]:
                        try:
                            dt = datetime.strptime(details["daytime"], "%H:%M:%S")
                            now = datetime.now()
                            daytime = datetime(
                                year=now.year,
                                month=now.month,
                                day=now.day,
                                hour=dt.hour,
                                minute=dt.minute,
                                second=dt.second,
                            )
                            if daytime < now:  # if time specified is less than current time -> add 1 day
                                daytime = daytime + timedelta(days=1)
                        except Exception as ex:
                            return ExecutionStatus(False, f"Invalid 'daytime'. Details: {ex}")
                    return self.skipper.timing.run(countdown=countdown, daytime=daytime)
                elif command == "stop timing":
                    return self.skipper.timing.stop()
                elif command == "remove timing":
                    return self.skipper.timing.remove()
                else:
                    with self.skipper.infrastructure_lock:
                        return self._minion_command(command=command, details=details, lang=lang)
            except Exception as ex:
                return ExecutionStatus(
                    False,
                    f"Something happened while executing the command.\n"
                    f"Command '{command}', details '{details}'.\n"
                    f"Error details: {ex}",
                )

        def set_info(self, command, details, lang=None, environ=None) -> ExecutionStatus:
            if lang is None:
                lang = "*"
            minion_settings: List[MinionSettings] = [
                settings
                for lang_, settings in self.skipper.registry.minion_configs.items()
                if (lang == "*" or lang_ == lang)
            ]

            if command == "set stream settings":
                # details: {"server": "...", "key": "..."}
                if "server" not in details or "key" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                for settings in minion_settings:
                    settings.stream_settings.server = details["server"]
                    settings.stream_settings.key = details["key"]
                return self.skipper.activate_registry()
            elif command == "set teamspeak offset":
                # details: {"value": numeric_value}  - offset in milliseconds
                if "value" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    value = int(details["value"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse teamspeak offset: {details}")
                for settings in minion_settings:
                    settings.ts_offset.value = value
                return self.skipper.activate_registry()
            elif command == "set teamspeak volume":
                # details: {"value": numeric_value}  - volume in decibels
                if "value" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    value = float(details["value"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse teamspeak volume: {details}")
                for settings in minion_settings:
                    settings.ts_volume.value = value
                return self.skipper.activate_registry()
            elif command == "set source volume":
                # details: {"value": numeric_value}  - volume in decibels
                if "value" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    value = float(details["value"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse source volume: {details}")
                for settings in minion_settings:
                    settings.source_volume.value = value
                return self.skipper.activate_registry()
            elif command == "set sidechain settings":
                # details: {"ratio": ..., "release_time": ..., "threshold": ..., "output_gain": ...}
                # all parameters are numeric
                if (
                        "ratio" not in details
                        and "release_time" not in details
                        and "threshold" not in details
                        and "output_gain" not in details
                ):
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    for settings in minion_settings:
                        if "ratio" in details:
                            settings.sidechain_settings.ratio = float(details["ratio"])
                        if "release_time" in details:
                            settings.sidechain_settings.release_time = float(details["release_time"])
                        if "threshold" in details:
                            settings.sidechain_settings.threshold = float(details["threshold"])
                        if "output_gain" in details:
                            settings.sidechain_settings.output_gain = float(details["output_gain"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse sidechain settings: {details}")
                return self.skipper.activate_registry()
            elif command == "set transition settings":
                # details: {"transition_point": ...}
                # all parameters are numeric
                if "transition_point" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    transition_point = float(details["transition_point"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse transition settings: {details}")
                for settings in minion_settings:
                    settings.transition_settings.transition_point = transition_point
                return self.skipper.activate_registry()
            else:
                return ExecutionStatus(False, f"Invalid command '{command}'")

        def _minion_command(self, command, details=None, lang=None) -> ExecutionStatus:
            """
            If lang is None -> broadcasts the command across all minions. Note that for specific commands
            lang is not needed. Lang can be either a single lang code or None ('*' - equal to None).
            Returns ExecutionStatus, where serializable object of status will have the following structure:
            {
                lang1: lang1_execution_status.dict(),
                lang2: lang2_execution_status.dict(),
                ...
            }
            """
            try:
                if lang == "*":
                    ws_responses = [
                        [lang, self.skipper.minions[lang].command(command=command, details=details)]
                        for lang in self.skipper.minions
                    ]  # emit commands
                    WebsocketResponse.wait_for([ws_response for _, ws_response in ws_responses])  # wait for responses
                    # parse websocket results into ExecutionStatus instances and then into dictionaries
                    # if websocket result was a timeout error, or a minion didn't return anything ->
                    # ExecutionStatus(False)
                    statuses: Dict[str, Dict] = {
                        # parse a status and convert it into a dictionary
                        lang: (
                            ExecutionStatus.from_json(ws_response.result())
                            # minion has not returned - means the minion didn't return anything
                            # or a timeout error has been thrown
                            if ws_response.result()
                            else ExecutionStatus(False, "Minion has not returned")
                        ).dict()
                        for lang, ws_response in ws_responses
                    }
                    # one vs all
                    return ExecutionStatus(
                        all([status["result"] for status in statuses.values()]), serializable_object=statuses
                    )
                elif lang not in self.skipper.minions:  # if lang is specified, and it is not present in self.minions
                    return ExecutionStatus(
                        False,
                        f"Invalid lang '{lang}'",
                        serializable_object={lang: ExecutionStatus(False, f"Invalid lang '{lang}'").dict()},
                    )
                else:  # if lang is specified and is present in self.minions
                    # emit command
                    ws_response: WebsocketResponse = self.skipper.minions[lang].command(
                        command=command, details=details
                    )
                    WebsocketResponse.wait_for([ws_response])  # wait for the response

                    if ws_response.result():  # if minion has returned
                        status: ExecutionStatus = ExecutionStatus.from_json(ws_response.result())
                        return ExecutionStatus(
                            status.status, message=status.message, serializable_object={lang: status.dict()}
                        )
                    else:  # if minion has not returned or timeout error has thrown
                        return ExecutionStatus(
                            False, serializable_object={lang: ExecutionStatus(False, "Minion has not returned").dict()}
                        )
            except Exception as ex:
                return ExecutionStatus(
                    False,
                    message=f"Something happened while sending a command.\n"
                            f"Command: '{command}', details: '{details}'.\n"
                            f"Error details: {ex}",
                    serializable_object={lang: ExecutionStatus(False, "Exception has been thrown").dict()},
                )

        def pull_obs_config(self, sheet_url=None, sheet_name=None, langs=None) -> ExecutionStatus:
            """
            Pulls and applies OBS configuration from google sheets
            :param sheet_url: sheet url. No need to specify if it has been specified once before (cache)
            :param sheet_name: works same as sheet_url
            :param langs: if specified, takes only specified langs from google sheets
            :return: ExecutionStatus
            """
            with self.skipper.registry_lock:
                if sheet_url and sheet_name:
                    status: ExecutionStatus = self.skipper.obs_config.setup(sheet_url, sheet_name)
                    if not status:
                        return status

                return self.skipper.obs_config.pull(langs=langs)

        def push_obs_config(self) -> ExecutionStatus:
            return self.skipper.obs_config.push()

        def delete_minions(self) -> ExecutionStatus:
            if self.skipper.registry.infrastructure_lock:
                return ExecutionStatus(False, "Cannot delete minions, infrastructure is locked")
            try:
                # with self.registry_lock:
                self.skipper.registry.server_status = State.disposing
                with self.skipper.infrastructure_lock:
                    self.skipper.timing.remove()
                    for lang, minion in self.skipper.minions.items():
                        minion.close()
                    self.skipper.minions = {}
                    # with self.registry_lock:
                    self.skipper.registry.minion_configs = {}
                    self.skipper.registry.server_status = State.sleeping
                    return ExecutionStatus(self.skipper.infrastructure.delete_servers())
            except Exception as ex:
                # with self.registry_lock:
                self.skipper.registry.server_status = State.sleeping
                return ExecutionStatus(False, f"Something happened while disposing. Details: {ex}")

    class HTTPApi:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper

        def setup_event_handlers(self):
            pass

    class BGWorker:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper
            self._last_registry_state = self.skipper.registry.json()

        def track_registry_change(self):
            """
            This function has to be started as a background worker. It tracks registry changes and broadcasts
            registry change to all changes.
            """
            while True:
                try:
                    with self.skipper.registry_lock:
                        new_registry_state = self.skipper.registry.json()
                        if self._last_registry_state != new_registry_state:
                            self._last_registry_state = new_registry_state
                            self.skipper.sio.emit("on_registry_change",
                                                  data=orjson_dumps({"registry": self.skipper.registry.dict()}),
                                                  broadcast=True)
                except Exception as ex:
                    print(f"E Skipper::BGWorker::track_registry_change(): "
                          f"Couldn't broadcast registry change. Details: {ex}")  # TODO: handle and log
                self.skipper.sio.sleep(0.2)

    def __init__(self, port=None):
        self.registry: Registry = Registry()
        self.infrastructure: Skipper.Infrastructure = Skipper.Infrastructure(self)
        self.obs_config: Skipper.OBSSheets = Skipper.OBSSheets(self)
        self.minions: Dict[str, Skipper.Minion] = {}
        self.timing: Skipper.Timing = Skipper.Timing(self)
        self.command: Skipper.Command = Skipper.Command(self)
        self.http_api: Skipper.HTTPApi = Skipper.HTTPApi(self)
        self.bg_worker: Skipper.BGWorker = Skipper.BGWorker(self)

        self.registry_lock = RLock()
        self.infrastructure_lock = RLock()

        self.port = port
        self.sio = socketio.Server(async_mode="threading", cors_allowed_origins="*")
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)

        self._sid_envs = {}

    def activate_registry(self) -> ExecutionStatus:
        with self.registry_lock:
            # check if there are all minions deployed
            status: ExecutionStatus = self.infrastructure.activate_registry()
            if not status:
                return status

            try:
                # with self.registry_lock:
                # select only those configs which have been changed (not active)
                configs_to_activate = [
                    [lang, minion_config]
                    for lang, minion_config in self.registry.minion_configs.items()
                    if not minion_config.active()
                ]
                with self.infrastructure_lock:
                    # collect websocket responses
                    responses = [
                        [lang, self.minions[lang].apply_config(minion_config=minion_config)]
                        for lang, minion_config in configs_to_activate
                        if lang in self.minions
                    ]
                    # wait until websocket callback or timeout
                    WebsocketResponse.wait_for(responses=[r for _, r in responses])

                    statuses: Dict[str, ExecutionStatus] = {}  # lang: ExecutionStatus

                    for lang, response in responses:
                        if response.result():
                            statuses[lang] = ExecutionStatus.from_json(response.result())  # convert result into status
                            if statuses[lang]:  # and check it
                                # with self.registry_lock:
                                self.registry.minion_configs[lang].activate()
                        else:
                            statuses[lang] = ExecutionStatus(False, "Minion didn't return")

                    return ExecutionStatus(
                        all([status.status for status in statuses.values()]),  # one vs all
                        serializable_object={  # form status as a dictionary of statuses
                            lang: status.dict() for lang, status in statuses.items()
                        },
                    )
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while activating skipper's registry. Details: {ex}")

    def save_to_disk(self):
        registry_json = self.registry.json()
        infra_json = self.infrastructure.json()
        minions_json = json.dumps({lang: minion.json() for lang, minion in self.minions})

        with open("./dump_registry.json", "wt") as fp:
            fp.write(registry_json)
        with open("./dump_infra.json", "wt") as fp:
            fp.write(infra_json)
        with open("./dump_minions.json", "wt") as fp:
            fp.write(minions_json)

    def load_from_disk(self):
        # Load registry
        self.registry = Registry()
        if os.path.isfile("./dump_registry.json"):
            with open("./dump_registry.json", "rt") as fp:
                content = fp.read()
                if content:
                    self.registry = Registry.parse_raw(content)
        # Load infrastructure
        self.infrastructure = Skipper.Infrastructure(self)
        if os.path.isfile("./dump_infra.json"):
            with open("dump_infra.json", "rt") as fp:
                content = fp.read()
                if content:
                    self.infrastructure = Skipper.Infrastructure.from_json(self, content)
        # Load minions
        del self.minions
        self.minions = {}
        if os.path.isfile("./dump_minions.json"):
            with open("dump_minions.json", "rt") as fp:
                content = fp.read()
                if content:
                    self.minions = {
                        lang: Skipper.Minion.from_json(minion_json, self)
                        for lang, minion_json in json.loads(fp.read()).items()
                    }

        self.activate_registry()

    def _setup_event_handlers(self):
        self.sio.on("connect", handler=self._on_connect)
        self.sio.on("disconnect", handler=self._on_disconnect)
        self.sio.on("command", handler=self._on_command)
        self.http_api.setup_event_handlers()

    def _setup_background_tasks(self):
        self.sio.start_background_task(self.bg_worker.track_registry_change)

    def _on_connect(self, sid, environ):
        self._sid_envs[sid] = environ
        # self.sio.save_session(sid, {'env': environ})

    def _on_disconnect(self, sid):
        self._sid_envs.pop(sid)

    def _on_command(self, sid, data):
        try:
            session = self._sid_envs[sid]
            return self.command.exec_raw(data, environ=session).json()
        except Exception as ex:
            return ExecutionStatus(False, f"Details: {ex}").json()

    def run(self):
        self._setup_event_handlers()
        self._setup_background_tasks()
        self.app.run(host="0.0.0.0", port=self.port)
