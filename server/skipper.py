# REFACTORING: build a new class which manages all the functionality listed below
import json
import os
import re
from datetime import datetime, timedelta
from threading import RLock, Thread
from typing import List, Dict

import socketio
from flask import Flask, request
from socketio.exceptions import ConnectionError

from deployment import Spawner, IPDict
from googleapi import OBSGoogleSheets, TimingGoogleSheets, UsersGoogleSheets
from models import (MinionSettings, VmixPlayer, Registry, State, TimingEntry,
                    orjson_dumps, User, SessionContext, passwd_placeholder)
from models.logging import LogsStorage, Log, LogLevel
from obs import OBS
from util import ExecutionStatus, WebsocketResponse, CallbackThread, hash_passwd

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
                    self.skipper.event_sender.send_registry_change({"server_status": State.initializing})

                    langs = self.skipper.registry.list_langs()
                    self.spawner.ensure_langs(langs=langs, wait_for_provision=True)  # [... [lang, ip], ...]
                except Exception as ex:
                    message = "Something happened while deploying minions"
                    self.skipper.logger.log_minion_setup_error(message, ex)
                    # TODO: log
                    # with self.skipper.registry_lock:
                    self.skipper.registry.revert_server_state()
                    return ExecutionStatus(False, message=f"{message}: {ex}")

                # lang_ips = self.spawner.ip_dict.ip_list()
                try:
                    self._ensure_minions()
                except ConnectionError as ex:
                    message = "Something happened while creating Minion instances"
                    self.skipper.logger.log_minion_setup_error(message, ex)
                    # with self.skipper.registry_lock:
                    self.skipper.registry.revert_server_state()
                    return ExecutionStatus(False, f"{message}. Details: {ex}")

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

                if langs == "*":
                    langs = None

                minion_configs: Dict[str, MinionSettings] = self.obs_sheets.pull()

                if self.skipper.registry.infrastructure_lock:  # if infrastructure is locked
                    registry_langs = list(self.skipper.registry.minion_configs.keys())
                    for lang in list(minion_configs.keys()):  # drop new langs
                        if lang not in registry_langs:
                            minion_configs.pop(lang)

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

    class SecurityWorker:
        def __init__(self, skipper: "Skipper"):
            self.users_sheet = UsersGoogleSheets()
            master_user = User.master()
            self.users: dict[str, User] = {master_user.login: master_user}
            self.authorized_users: dict[str, User] = {}
            self.skipper = skipper
            self.public_commands = Skipper.SecurityWorker.get_public_commands()

        @staticmethod
        def get_public_commands() -> set[str]:
            """Returns a list of commands available for users without 'admin' permission"""
            return {
                "get info",
                "set stream settings",
                "set teamspeak offset",
                "set teamspeak volume",
                "set source volume",
                "set vmix speaker background volume",
                "set sidechain settings",
                "set transition settings",
                "start streaming",
                "stop streaming",
                "refresh source",
                "get logs",
            }

        def authorize(self, sid: str, login: str, passwd: str) -> bool:
            """Checks if user is authorized"""
            if login in self.users and self.users[login].passwd_hash == hash_passwd(passwd):
                self.authorized_users.update({sid: self.users[login]})
                return True
            return False

        def logout(self, sid: str):
            """Deletes user from authorized"""
            if sid in self.authorized_users.keys():
                del self.authorized_users[sid]

        def sync_from_sheets(self):
            """Syncs users with Google Sheet"""
            if not self.users_sheet.setup_status:
                return
            errors = []
            try:
                sheet_data = self.users_sheet.fetch_all()
                prev_users = self.users
                # Reset users list
                master_user = User.master()
                self.users = {master_user.login: master_user}
                for i in range(len(sheet_data)):
                    row_data = sheet_data[i]
                    col_index = row_data["col"]
                    login = row_data["login"]
                    passwd = row_data["passwd"]
                    passwd_hash = row_data["hash"]
                    permissions = row_data["permissions"]
                    if login.strip() == "":
                        continue
                    # Get cached user by login equality
                    if not self._validate_user(errors, login, permissions):
                        continue
                    cur_user = self._sync_cur_user(login, passwd_hash, permissions, prev_users)

                    # Disallow to change manually hash if password is a placeholder
                    if passwd == passwd_placeholder:
                        if cur_user and passwd_hash != cur_user.passwd_hash:
                            self.users_sheet.set_passwd(col_index, passwd_placeholder, cur_user.passwd_hash)
                    # If password is empty - reset hash
                    elif passwd.strip() == "":
                        errors.append(f"{login}: has no password")
                        if passwd_hash.strip() != "":
                            self.users_sheet.reset_passwd_hash(col_index)
                    # Update password and hash
                    else:
                        passwd_hash = hash_passwd(passwd)
                        if passwd != passwd_placeholder or passwd_hash != cur_user.passwd_hash:
                            cur_user.passwd_hash = passwd_hash
                            self.users_sheet.set_passwd(col_index, passwd_placeholder, passwd_hash)
            except Exception as e:
                errors.append(str(e))
            # TODO: DEBUG ONLY
            # print('users:', " ".join([str(u) for u in self.users]))
            if len(errors) == 0:
                message = "Success"
            else:
                message = "\n".join(errors)
            self.users_sheet.set_sync_status(message)

        def _validate_user(self, errors, login, permissions):
            if not re.match(r"^(?=.{2,50}$)(?:[a-zA-Z\d]+(?:(?:-|_)[a-zA-Z\d])*)+$", login):
                errors.append(f"{login}: invalid login (allowed only alphanumeric and _ or -)")
                return False
            if not permissions or len(permissions) < 1:
                errors.append(f"{login}: has no permissions")
                return False
            if login in self.users:
                errors.append(f"{login}: duplicated login")
                return False
            return True

        def _sync_cur_user(self, login, passwd_hash, permissions, prev_users):
            if login in prev_users:
                cur_user = prev_users[login]
                cur_user.permissions = permissions
                self.users.update({cur_user.login: cur_user})
                return cur_user
            elif login.strip() != "":
                cur_user = User(
                    login=login,
                    passwd=passwd_placeholder,
                    passwd_hash=passwd_hash,
                    permissions=permissions,
                )
                self.users.update({cur_user.login: cur_user})
                return cur_user
            return None

        def set_sheets(self, sheet_url: str, sheet_name: str) -> ExecutionStatus:
            """Binds users with Google Sheet table"""
            try:
                with self.skipper.registry_lock:
                    self.users_sheet.set_sheet(sheet_url, sheet_name)
                    self.skipper.registry.users_sheet_url = sheet_url
                    self.skipper.registry.users_sheet_name = sheet_name
                self.sync_from_sheets()
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Couldn't set up users sheet config.\nDetails: {ex}")

        def get_user(self, sid: str):
            """Returns a list of user permissions"""
            if sid in self.authorized_users.keys():
                return self.authorized_users[sid]
            return None

        def get_user_langs(self, sid: str):
            if sid in self.authorized_users:
                user: User = self.authorized_users[sid]
                return user.langs()
            raise KeyError(f"No such sid in self.authorized_users: {sid}")

        def copy_registry_for_user(self, user: User, masked=True) -> dict:
            """Returns a copy of the original registry without data user has no access"""
            with self.skipper.registry_lock:
                registry = self.skipper.registry.masked_dict() if masked else self.skipper.registry.dict()
                return self.adjust_registry_for_user(registry, user)

        @staticmethod
        def adjust_registry_for_user(registry: dict, user: User) -> dict:
            """Returns a copy of the original registry without data user has no access"""
            if user.is_admin():
                return registry
            perms = user.permissions
            # Select only langs from user permissions
            if "minion_configs" in registry:
                registry["minion_configs"] = {k: v for k, v in registry["minion_configs"].items() if k in perms}
            if "gdrive_files" in registry:
                registry["gdrive_files"] = {k: v for k, v in registry["gdrive_files"].items() if k in perms}
            if "vmix_players" in registry:
                del registry["vmix_players"]
            if "active_vmix_player" in registry:
                del registry["active_vmix_player"]
            return registry

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
                message = f"Connection error for ip {self.minion_ip} lang {self.lang}"
                self.skipper.logger.log_minion_error(
                    message=message,
                    error=ex,
                    extra={
                        "minion_ip": self.minion_ip,
                        "minion_lang": self.lang,
                    }
                )
                # TODO: log
                raise ConnectionError(f"{message}. Details:\n{ex}")

        def _register_event_handlers(self):
            self.sio.on("on_gdrive_files_changed", handler=self._on_gdrive_files_changed)

        def _on_gdrive_files_changed(self, data):
            with self.skipper.registry_lock:
                self.skipper.registry.gdrive_files[self.lang] = json.loads(data)

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
                        details={"name": entry.name, "search_by_num": True, "mode": OBS.PLAYBACK_MODE_CHECK_SAME},
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
                self.skipper.command.exec("stop media")
                # with self.skipper.registry_lock:
                self.skipper.registry.timing_start_time = None
                for entry in self.skipper.registry.timing_list:
                    entry.is_played = False
                self.cb_thread.delete_cb_type("timing")
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"Something happened while stopping the timing. Details: {ex}")

        def remove(self) -> ExecutionStatus:
            status = self.stop()
            self.skipper.registry.timing_list = []
            return status

    class EventHandler:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper
            self.event_handlers = []

        def add_or_replace_on_command_completed_event(self, foo, id, command=None, run_in_new_thread=True):
            """
            Args structure:
            (command, details, lang, result: ExecutionStatus, ip)
            """
            self.event_handlers = [
                [command, new_thread_sign, foo, id_]
                for command, new_thread_sign, foo, id_ in self.event_handlers
                if id_ != id
            ]
            self.event_handlers.append([command, run_in_new_thread, foo, id])

        def on_command_completed(self, command, details, lang, result: ExecutionStatus, session: SessionContext):
            ip = self.skipper.registry.get_ip_name(
                session.ip if session and session.ip else "0.0.0.0"
            )
            self.skipper.logger.log_command_completed(
                status=result.status,
                extra={
                    "command": command,
                    "details": details,
                    "message": result.message,
                    "lang": lang,
                    "ip": ip
                })

            for foo_command, run_in_new_thread, foo, _ in self.event_handlers:
                if foo_command is None or foo_command == command:
                    try:
                        if run_in_new_thread:
                            Thread(target=foo, args=(command, details, lang, result, ip)).start()
                        else:
                            foo(command, details, lang, result, ip)
                    except Exception as ex:
                        pass

    class EventSender:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper

        def send_registry_change(self, registry: dict):
            """Sends a registry changes to every client who has enough access rights"""
            for sid, user in self.skipper.security.authorized_users.items():
                registry = self.skipper.security.adjust_registry_for_user(registry, user)
                if len(registry.keys()) > 0:
                    self._send_event("on_registry_change", {"registry": registry}, sid=sid)

        def send_log(self, log: Log):
            """Sends log events to all admins"""
            for sid, user in self.skipper.security.authorized_users.items():
                if user.is_admin():
                    self._send_event("on_log", {"log": log.dict()}, sid=sid)
                else:
                    if log.extra and "command" in log.extra \
                            and log.extra["command"] in Skipper.SecurityWorker.get_public_commands():
                        self._send_event("on_log", {"log": log.dict()}, sid=sid)

        def send_auth_result(self, sid: str, status: bool):
            """Sends an auth result to user himself"""
            if status:
                data = {"status": True, "message": "User successfully authorized"}
            else:
                data = {"status": False, "message": "Login or password is not valid"}
            self._send_event("on_auth", data, sid)

        def _send_event(self, event: str, data: dict, sid: str = None):
            """Sends an event to specific client by SessionID (SID) or broadcasts an event it SID didn't specify"""
            try:
                data = orjson_dumps(data)
                if sid:
                    self.skipper.sio.emit(event, data, to=sid)
                else:
                    self.skipper.sio.emit(event, data, broadcast=True)
            except Exception as ex:
                print(f"Error occurred while sending ws event: {ex}")

    class Logger:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper
            self.logs: LogsStorage = LogsStorage()

        def log_command_started(self, extra: dict):
            self._add_log(Log(
                level=LogLevel.info,
                type="command_started",
                message=f"Command '{extra['command']}' started",
                extra=extra,
            ))

        def log_command_completed(self, status: bool, extra: dict):
            m = "completed" if status else "failed"
            self._add_log(Log(
                level=LogLevel.info,
                type="command_completed",
                message=f"Command '{extra['command']}' {m}",
                extra=extra,
            ))

        def log_failed_login_attempt(self, message: str, login: str):
            self._add_log(Log(
                level=LogLevel.error,
                type="login_failure",
                message=message,
                extra={"login": login},
            ))

        def log_success_login_attempt(self, message: str, login: str):
            self._add_log(Log(
                level=LogLevel.error,
                type="login_success",
                message=message,
                extra={"login": login},
            ))

        def log_server_error(self, message: str, error: Exception, extra: dict = None):
            self._add_log(Log(
                level=LogLevel.error,
                type="skipper_error",
                message=message,
                error=error,
                extra=extra,
            ))

        def log_minion_setup_error(self, message: str, error: Exception):
            self._add_log(Log(
                level=LogLevel.error,
                type="minion_setup_error",
                message=message,
                error=error
            ))

        def log_minion_error(self, message: str, error: Exception, extra: dict):
            self._add_log(Log(
                level=LogLevel.error,
                type="minion_error",
                message=message,
                error=error,
                extra=extra
            ))

        def _add_log(self, log: Log):
            self.logs.append(log)
            self.skipper.event_sender.send_log(log)

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

        def _check_caller(self, session: SessionContext, command, details=None, lang=None) -> ExecutionStatus:
            """
            Checks if the command is allowed given the command, ip, lang and details.
            If returned ExecutionStatus(True) - the command is allowed, otherwise not allowed
            """
            # if the server is sleeping only allow the following commands
            server_status = self.skipper.registry.server_status
            if server_status == State.sleeping and \
                    command not in ("pull config", "get info", "infrastructure unlock", "get logs"):
                return ExecutionStatus(False, f"Command {command} is not allowed while server is '{server_status}'")

            # if the server is initializing or disposing - only allow 'get info' command
            if server_status in (State.initializing, State.disposing) and command != "get info":
                return ExecutionStatus(False, f"Command {command} is not allowed while server is '{server_status}'")

            if session:
                is_active_vmix_player = self.skipper.registry.active_vmix_player == session.ip
                if not is_active_vmix_player and not session.user:
                    return ExecutionStatus(False, "User is not authorized")
                is_admin = session.user.is_admin()
            else:  # if session is None
                return ExecutionStatus(True)

            if is_admin or (command == "play media" and is_active_vmix_player):
                return ExecutionStatus(True)

            # if user didn't authorize or is not a vmix player -> deny
            if not is_active_vmix_player and (session.user is None or not session.user.passwd_hash):
                return ExecutionStatus(False, "User is not authorized")

            # if command - is a public command -> allow
            if command in self.skipper.security.public_commands:
                return ExecutionStatus(True)

            # other commands should be accessible only for admin
            if not is_admin:
                return ExecutionStatus(False, "Permission denied")

            # True - allows the command
            return ExecutionStatus(True)

        def _adjust_user_langs(self, session: SessionContext, langs=None, command=None, details=None):
            """
            This function checks if session has permissions to languages specified and returns an adjusted
            list of langs: '*' - if all langs are allowed, ['Lang1', 'Lang2', ...] - if few of langs allowed
            :param session: user session
            :param langs: langs can be either list of lang codes, '*', None or a single language code
            :param command: optional
            :param details: optional
            :return:
            """
            if session:
                is_vmix_player = self.skipper.registry.active_vmix_player == session.ip
                if not is_vmix_player and not session.user:
                    return []
                is_admin = session.user.is_admin()
                user_langs = session.user.langs()
                # raise RuntimeError("SecurityWorker::_adjust_user_langs(): Session should not be null")
            else:  # if session is None
                is_admin = True
                is_vmix_player = False
                user_langs = "*"

            if is_vmix_player:
                if command == "play media":
                    return "*"
                else:
                    return []

            if not langs:
                langs = "*"

            if is_admin or "*" in user_langs:  # if everything is allowed for user
                if "*" in langs:
                    return "*"

                if isinstance(langs, str):  # if lang code passed is not a list, if it is a single value
                    return [langs]
                return langs
            else:  # if user is not admin
                if "*" in langs:  # if all langs are requested, return only accessible ones
                    return user_langs
                if isinstance(langs, str):  # if single value is passed
                    if langs not in user_langs:  # check one
                        raise ValueError(f"Lang '{langs}' in not allowed for user '{session.user.login}'")
                    return [langs]
                else:  # multiple langs are passed
                    # if there is any lang code which is not accessible
                    denied_langs = [lang for lang in langs if lang not in user_langs]
                    if denied_langs:
                        raise ValueError(f"Langs '{denied_langs}' are not allowed for user '{session.user.login}'")
                    return langs

        def _fix_infrastructure_lock(self):
            """
            Checks if the infrastructure lock's state is valid for current server status
            """
            if self.skipper.registry.server_status == State.sleeping:
                self.skipper.registry.infrastructure_lock = False

        def exec_raw(self, command_raw, session: SessionContext = None) -> ExecutionStatus:
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
                if session.user:
                    command["lang"] = session.user.langs()[0]  # TODO: Nice to have multi-language support
                else:
                    command["lang"] = "*"
            command, details, lang = command["command"], command["details"], command["lang"]

            return self.exec(command, details=details, lang=lang, session=session)

        def exec(self, command, details=None, lang=None, session: SessionContext = None) -> ExecutionStatus:
            """
            Command structure for a Streamer:
            {
                "command": "play media|media stop|set ts volume|get ts volume|...",
                "details": ... may be dict, str, list, int, bool, etc.
            }
            :return: ExecutionStatus
            """
            langs = self._adjust_user_langs(langs=lang, session=session, command=command)  # list of langs or '*'
            result = self._exec(command, details, langs, session)
            self.skipper.event_handler.on_command_completed(command, details, lang, result, session)
            return result

        def _exec(self, command, details=None, langs=None, session: SessionContext = None) -> ExecutionStatus:
            check_result = self._check_caller(session, command)

            if not check_result.status:
                return check_result

            # prefetch minion_configs for specified langs, for the purpose of not duplicating the code
            minion_configs: List[MinionSettings] = [
                minion_config
                for lang_, minion_config in self.skipper.registry.minion_configs.items()
                if (langs == "*" or lang_ in langs)
            ]

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
                    users_sheet_name = None if not details or "users_sheet_name" not in details \
                        else details["users_sheet_name"]
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
                    # fetch users from Google Sheet
                    status = self.pull_users_config(sheet_url, users_sheet_name)
                    if not status:
                        return status
                    # pull_obs_config() manages registry lock itself
                    status = self.pull_obs_config(sheet_url, sheet_name, langs)
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
                    if not details:  # validate
                        masked = True
                    elif "masked_info" in details:
                        masked = bool(details["masked_info"])
                    else:
                        return ExecutionStatus(False, "Invalid details provided for command 'get info'")

                    registry = self.skipper.security.copy_registry_for_user(session.user, masked=masked)
                    return ExecutionStatus(True, serializable_object=orjson_dumps({"registry": registry}))
                elif command in (
                        "set stream settings",
                        "set teamspeak offset",
                        "set teamspeak volume",
                        "set source volume",
                        "set vmix speaker background volume",
                        "set sidechain settings",
                        "set transition settings",
                        "set teamspeak limiter settings",
                        "set teamspeak gain settings",
                ):
                    return self.set_info(command=command, details=details, langs=langs, session=session)
                elif command == "get logs":
                    count = None
                    try:
                        count = 100 if details is None or "count" not in details else int(details["count"])
                    except:
                        return ExecutionStatus(False, message=f"details.count is not valid: '{count}'")
                    return ExecutionStatus(True, serializable_object={
                        "logs": self.skipper.logger.logs.get(count)
                    })
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

                    # check if name doesn't already added to vmix_players
                    # vmix_players names should be unique
                    name = details["name"].upper()
                    if name in [v.name for v in self.skipper.registry.vmix_players.values()]:
                        return ExecutionStatus(False, f"Can't add vmix player. "
                                                      f"Reason: name '{name}' already exists")
                    if not re.fullmatch(r"[a-zA-Zа-яА-Я0-9\s\.]+", name):
                        return ExecutionStatus(False, f"Can't add vmix player. "
                                                      f"Reason: invalid characters are provided. Refer to the docs")
                    self.skipper.registry.vmix_players[details["ip"]] = VmixPlayer(
                        name=name,
                        active=False
                    )
                    return ExecutionStatus(True)
                elif command == "vmix players remove":
                    # details: {"ip": "ip address" | "name": "ip name"}
                    if not details or not ("ip" in details or "name" in details):
                        return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")

                    try:
                        ip = self._fetch_ip_for_vmix_player(details)
                    except Exception as ex:
                        return ExecutionStatus(False, str(ex))

                    # with self.registry_lock:
                    if ip not in self.skipper.registry.vmix_players:
                        return ExecutionStatus(True)
                    if ip == "*":
                        return ExecutionStatus(False, "Removing '*' is not allowed")

                    # with self.registry_lock:
                    self.skipper.registry.vmix_players.pop(ip)
                    return ExecutionStatus(True)
                # elif command == "vmix players list":
                #     # no details needed
                #     # returns ExecutionStatus(True, serializable_object={ip: vmix_player.dict(), ...})
                #     # with self.registry_lock:
                #     return ExecutionStatus(True, serializable_object={
                #         ip: vmix_player.dict() for ip, vmix_player in self.skipper.registry.vmix_players.items()
                #     })
                elif command == "vmix players set active":
                    # details: {"ip": "ip address" | "name": "ip name"}. ip address may be '*' - all active
                    if not details or not ("ip" in details or "name" in details):
                        return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")

                    try:
                        ip = self._fetch_ip_for_vmix_player(details)
                    except Exception as ex:
                        return ExecutionStatus(False, str(ex))

                    self.skipper.registry.active_vmix_player = ip
                    for ip_ in self.skipper.registry.vmix_players:
                        self.skipper.registry.vmix_players[ip_].active = (ip == ip_)
                    return ExecutionStatus(True)
                elif command == "start streaming":
                    for minion_config in minion_configs:
                        if minion_config.stream_settings.key and minion_config.stream_settings.server:
                            minion_config.stream_on.value = True
                    return self.skipper.activate_registry()
                elif command == "stop streaming":
                    for minion_config in minion_configs:
                        if minion_config.stream_settings.key and minion_config.stream_settings.server:
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
                            daytime = details["daytime"]
                            # ljust - 6 digits after the dot
                            if '.' in daytime:  # if milliseconds are specified
                                _ = daytime.index(".")
                                # make sure daytime - is a string with microseconds
                                daytime = daytime[:_ + 1] + daytime[_ + 1:].ljust(6, "0")
                            else:
                                # make sure daytime - is a string with microseconds
                                daytime = daytime + ".000000"
                            # parse daytime (microseconds are included)
                            dt = datetime.strptime(daytime, "%H:%M:%S.%f")
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
                        return self._minion_command(command=command, details=details, langs=langs, session=session)

            except Exception as ex:
                self.skipper.logger.log_server_error(
                    message=f"Something happened while executing the command '{command}'",
                    error=ex,
                    extra={
                        "command": command,
                        "details": details,
                        "lang": langs,
                        "ip": self.skipper.registry.get_ip_name(session.ip)
                    })

                return ExecutionStatus(
                    False,
                    f"Something happened while executing the command.\n"
                    f"Command '{command}', details '{details}'.\n"
                    f"Error details: {ex}",
                )

        def set_info(self, command, details, langs=None, session: SessionContext = None) -> ExecutionStatus:
            langs = self._adjust_user_langs(langs=langs, session=session)  # list of langs or '*'

            minion_settings: List[MinionSettings] = [
                settings
                for lang, settings in self.skipper.registry.minion_configs.items()
                if (langs == "*" or lang in langs)
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
            elif command == "set vmix speaker background volume":
                # details: {"value": numeric_value}  - volume in decibels
                if "value" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    value = float(details["value"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse source volume: {details}")
                for settings in minion_settings:
                    settings.vmix_speaker_background_volume.value = value
                return self.skipper.activate_registry()
            elif command == "set sidechain settings":
                # details: {"ratio": ..., "release_time": ..., "threshold": ..., "output_gain": ...}
                # all parameters are numeric
                if (
                        "ratio" not in details
                        and "release_time" not in details
                        and "threshold" not in details
                        and "output_gain" not in details
                        and "enabled" not in details
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
                        if "enabled" in details:
                            settings.sidechain_settings.enabled = bool(details["enabled"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse sidechain settings: {details}")
                return self.skipper.activate_registry()
            elif command == "set teamspeak limiter settings":
                # details: {"threshold": ..., "release_time": ...}
                # all parameters are numeric
                if (
                        "threshold" not in details
                        and "release_time" not in details
                        and "enabled" not in details
                ):
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    for settings in minion_settings:
                        if "threshold" in details:
                            settings.ts_limiter_settings.threshold = float(details["threshold"])
                        if "release_time" in details:
                            settings.ts_limiter_settings.release_time = int(details["release_time"])
                        if "enabled" in details:
                            settings.ts_limiter_settings.enabled = bool(details["enabled"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse limiter settings: {details}")
                return self.skipper.activate_registry()
            elif command == "set teamspeak gain settings":
                # details: {"gain": ...}
                # all parameters are numeric
                if "gain" not in details and "enabled" not in details:
                    return ExecutionStatus(False, f"Invalid details provided for '{command}': {details}")
                try:
                    for settings in minion_settings:
                        if "gain" in details:
                            settings.ts_gain_settings.gain = float(details["gain"])
                        if "enabled" in details:
                            settings.ts_gain_settings.enabled = bool(details["enabled"])
                except Exception as ex:
                    return ExecutionStatus(False, f"Couldn't parse gain settings: {details}")
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

        def _minion_command(self, command, details=None, langs=None, session: SessionContext = None) -> ExecutionStatus:
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
                langs = self._adjust_user_langs(langs=langs, session=session, command=command)  # list of langs or '*'
                if langs == "*":
                    langs = [lang for lang in self.skipper.minions]

                # if langs are specified, and some of them not present in self.minions
                if [lang for lang in langs if lang not in self.skipper.minions]:
                    invalid_langs = [lang for lang in langs if lang not in self.skipper.minions]
                    return ExecutionStatus(
                        False,
                        f"Invalid langs '{invalid_langs}'",
                        serializable_object={
                            lang: ExecutionStatus(
                                False, (f"Invalid lang '{lang}'" if lang in invalid_langs
                                        else f"Please fix invalid langs first")
                            ).dict()
                            for lang in langs
                        },
                    )
                else:  # if langs are specified and are present in self.minions
                    # emit commands
                    ws_responses = [
                        [lang, self.skipper.minions[lang].command(command=command, details=details)]
                        for lang in langs
                    ]  # emit commands
                    WebsocketResponse.wait_for([ws_response for _, ws_response in ws_responses])  # wait for responses

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
            except Exception as ex:
                return ExecutionStatus(
                    False,
                    message=f"Something happened while sending a command.\n"
                            f"Command: '{command}', details: '{details}'.\n"
                            f"Error details: {ex}",
                    serializable_object={"*": ExecutionStatus(False, "Exception has been thrown").dict()},
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

        def pull_users_config(self, sheet_url=None, sheet_name=None) -> ExecutionStatus:
            if sheet_url and sheet_name:
                return self.skipper.security.set_sheets(sheet_url, sheet_name)
            self.skipper.security.sync_from_sheets()
            return ExecutionStatus(True)

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
                    self.skipper.registry.server_status = State.sleeping  # server_status is logging in other place
                    return ExecutionStatus(self.skipper.infrastructure.delete_servers())
            except Exception as ex:
                # with self.registry_lock:
                self.skipper.registry.server_status = State.sleeping
                return ExecutionStatus(False, f"Something happened while disposing. Details: {ex}")

        def _fetch_ip_for_vmix_player(self, details) -> str:
            if "ip" in details:
                if details["ip"] not in self.skipper.registry.vmix_players:
                    raise KeyError("Couldn't set active vmix player. No such ip found")
                return details["ip"]
            elif "name" in details:
                name = details["name"].upper()
                if name not in [x.name for x in self.skipper.registry.vmix_players.values()]:
                    raise KeyError("Couldn't set active vmix player. No such name found")
                return [ip for ip, vmix_player in self.skipper.registry.vmix_players.items()
                        if vmix_player.name == name][0]
            else:
                raise RuntimeError("This block of code should not be executed")

    class HTTPApi:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper

            self.skipper.app.add_url_rule("/media/play", view_func=self._http_api_play_media, methods=["POST"])

        def setup_event_handlers(self):
            pass

        def _http_api_play_media(self):
            params = request.args.get("params", None)
            params = json.loads(params)

            name = params["name"]
            search_by_num = True if "search_by_num" not in params else params["search_by_num"]
            mode = "mode" if "mode" not in params else params["mode"]

            command, details = "play media", {"name": name, "search_by_num": search_by_num, "mode": mode}
            return self.skipper.command.exec(
                command=command,
                details=details,
                session=SessionContext(ip=request.remote_addr)
            ).to_http_status()

    class BGWorker:
        def __init__(self, skipper):
            self.skipper: Skipper = skipper
            self._last_registry_state = self.skipper.registry.masked_json()
            self._last_registry_dict = self.skipper.registry.dict()

        def track_registry_change(self):
            """
            This function has to be started as a background worker. It tracks registry changes and broadcasts
            registry change to all changes.
            """
            while True:
                try:
                    with self.skipper.registry_lock:
                        new_registry_state = self.skipper.registry.masked_json()
                        # Compare prev state json and current state json
                        if self._last_registry_state != new_registry_state:
                            # Get changed values
                            new_registry_dict = self.skipper.registry.masked_dict()
                            changes = self._get_registry_changes(new_registry_dict)
                            # Save current state
                            self._last_registry_state = new_registry_state
                            self._last_registry_dict = new_registry_dict
                            # Send registry changed event
                            self.skipper.event_sender.send_registry_change(changes)
                except Exception as ex:
                    print(f"E Skipper::BGWorker::track_registry_change(): "
                          f"Couldn't broadcast registry change. Details: {ex}")  # TODO: handle and log
                    self.skipper.logger.log_server_error(
                        message="Couldn't broadcast registry change",
                        error=ex
                    )
                self.skipper.sio.sleep(0.2)

        def track_gsheet_users_change(self):
            """
            This function has to be started as a background worker. It tracks user password changes in Google Sheets
            """
            while True:
                try:
                    with self.skipper.registry_lock:
                        if self.skipper.security.users_sheet.setup_status:
                            self.skipper.security.sync_from_sheets()
                except Exception as ex:
                    print(f"E Skipper::BGWorker::track_gsheet_users_change(): "
                          f"Couldn't track user password changes. Details: {ex}")  # TODO: handle and log
                    self.skipper.logger.log_server_error(
                        message="Couldn't track user password changes",
                        error=ex
                    )
                self.skipper.sio.sleep(seconds=30)

        def start_sending_time(self):
            while True:
                try:
                    self.skipper.sio.emit("on_datetime_update",
                                          data=datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M:%S"),
                                          broadcast=True)
                except Exception as ex:
                    print(f"BGWorker::start_sending_time(): Couldn't send time. Details: {ex}")
                self.skipper.sio.sleep(1)

        def _get_registry_changes(self, current: dict) -> dict:
            prev = self._last_registry_dict
            diff = {}
            for root_key, current_val in current.items():
                if orjson_dumps(current_val) != orjson_dumps(prev[root_key]):
                    diff[root_key] = current_val
            return diff

    def __init__(self, port=None):
        self.event_sender: Skipper.EventSender = Skipper.EventSender(self)
        self.event_handler: Skipper.EventHandler = Skipper.EventHandler(self)
        self.logger: Skipper.Logger = Skipper.Logger(self)
        self.registry: Registry = Registry()
        self.infrastructure: Skipper.Infrastructure = Skipper.Infrastructure(self)
        self.obs_config: Skipper.OBSSheets = Skipper.OBSSheets(self)
        self.security: Skipper.SecurityWorker = Skipper.SecurityWorker(self)
        self.minions: Dict[str, Skipper.Minion] = {}
        self.timing: Skipper.Timing = Skipper.Timing(self)
        self.command: Skipper.Command = Skipper.Command(self)
        self.bg_worker: Skipper.BGWorker = Skipper.BGWorker(self)

        self.registry_lock = RLock()
        self.infrastructure_lock = RLock()

        self.port = port
        self.sio = socketio.Server(async_mode="threading", cors_allowed_origins="*", )
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)

        self.http_api: Skipper.HTTPApi = Skipper.HTTPApi(self)

        self._sessions: dict[str, SessionContext] = {}

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
        self.sio.start_background_task(self.bg_worker.track_gsheet_users_change)
        self.sio.start_background_task(self.bg_worker.start_sending_time)

    def _setup_session_context(self, sid: str, environ: dict, auth=None) -> SessionContext:
        login = auth["HTTP_LOGIN"]
        password = auth["HTTP_PASSWORD"]
        ip = environ["REMOTE_ADDR"] if "REMOTE_ADDR" in environ else "*"

        if login and password:
            authorized_status = self.security.authorize(sid, login, password)
            if authorized_status:
                # Notify user about success
                self.event_sender.send_auth_result(sid, status=True)
                self.logger.log_success_login_attempt("User authorized", login)
            else:
                # Notify user about failed auth
                self.event_sender.send_auth_result(sid, status=False)
                self.logger.log_failed_login_attempt("Invalid login or password", login)

        user = self.security.get_user(sid)
        return SessionContext(sid=sid, ip=ip, user=user)

    def _on_connect(self, sid, environ: dict, auth):
        if environ is None or not isinstance(environ, dict):
            environ = {}
        self._sessions[sid] = self._setup_session_context(sid, environ, auth)

        # self.sio.save_session(sid, {'env': environ})

    def _on_disconnect(self, sid):
        if sid in self._sessions:
            self.security.logout(sid)
            self._sessions.pop(sid)

    def _on_command(self, sid, data):
        try:
            if sid not in self.security.authorized_users:
                raise PermissionError("User is not authorized")
            return self.command.exec_raw(data, session=self._sessions[sid]).json()
        except Exception as ex:
            return ExecutionStatus(False, f"Details: {ex}").json()

    def run(self):
        self._setup_event_handlers()
        self._setup_background_tasks()
        self.app.run(host="0.0.0.0", port=self.port)
