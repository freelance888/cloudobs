from models import MinionSettings
from pydantic import BaseModel
from flask import Flask
import socketio

import json
import os
import time
from obs import OBSController
from threading import RLock, Thread
from googleapiclient.discovery import build
from util import ExecutionStatus, generate_file_md5
import random
import gdown
from dotenv import load_dotenv
from typing import Dict
from multiprocessing import Process


class Minion:
    class Registry(BaseModel):
        minion_settings: MinionSettings = MinionSettings.default("localhost")

    class GDriveFileWorker(Thread):
        def __init__(self, minion):
            super(Minion.GDriveFileWorker, self).__init__()

            self.minion: Minion = minion
            self.lock = RLock()
            self.files = {}  # filename: true/false

            load_dotenv()
            self.service_file = os.getenv("SERVICE_ACCOUNT_FILE", "/home/stream/sa.json")

        def run(self) -> None:
            while True:
                try:
                    self.check_files()
                    time.sleep(60)
                    self.minion.command.send_gdrive_files()
                except Exception as ex:
                    print(f"E Minion::GDriveFileWorker::run(): {ex}")

        def activate_registry(self):
            with self.minion.registry_lock:
                settings = self.minion.registry.minion_settings.gdrive_settings

                if settings.is_active():
                    return True

                if not settings.api_key or not self.minion.command.obs.set_media_dir(settings.media_dir):
                    return False

                settings.activate()
                return True

        def check_files(self):
            if not self.activate_registry():
                return

            with self.minion.registry_lock:
                settings = self.minion.registry.minion_settings.gdrive_settings
                media_dir = settings.media_dir

            with build("drive", "v3", developerKey=settings.api_key) as service:
                # list the drive files, the response has the following structure:
                """
                {'kind': 'drive#fileList',
                 'incompleteSearch': False,
                 'files': [{'kind': 'drive#file',
                   'id': '1HCFWjOE-XTAh_Mau7DrSYsR3_uu0Ei3r',
                   'name': 'electron_edited.wav',
                   'mimeType': 'audio/wav'}]}
                """
                gfiles = (
                    service.files()
                    .list(
                        q=f"'{settings.folder_id}' in parents",
                        supportsAllDrives=True,
                        supportsTeamDrives=True,
                        includeItemsFromAllDrives=True,
                        includeTeamDriveItems=True,
                        fields="files(id,name,md5Checksum)",
                        pageSize=1000,
                    )
                    .execute()
                )
                # if something went wrong
                if "files" not in gfiles:
                    raise RuntimeError(f"Couldn't list files in specified driveId. Error: {gfiles}")

                downloaded_files = os.listdir(media_dir)

                for fileinfo in gfiles["files"]:  # for each file in Google Drive
                    fid, fname = fileinfo["id"], fileinfo["name"]
                    if "md5Checksum" not in fileinfo:
                        continue  # if there is no md5Checksum that means it's not a file, probably a folder, skip
                    with self.lock:
                        if fname not in self.files:  # if we haven't downloaded it before
                            self.files[fname] = False  # append to registry
                            # self.on_files_changed()

                gdrive_files = [f["name"] for f in gfiles["files"] if f["name"] in self.files]
                with self.lock:
                    for downloaded_fname in downloaded_files:  # iterate downloaded files
                        if downloaded_fname not in gdrive_files:  # if such file doesn't exist in google drive
                            downloaded_fname_ = f"'{downloaded_fname}'"
                            os.system(f"rm {os.path.join(media_dir, downloaded_fname_)}")  # remove it

                    for fname in self.files:  # for each file we have already downloaded
                        # if we have downloaded this file, but it doesn't appear in Google Drive
                        if fname not in gdrive_files:
                            fname_ = f"'{fname}'"
                            os.system(f"rm {os.path.join(media_dir, fname_)}")  # remove it
                            if not os.path.isfile(os.path.join(media_dir, fname_)):  # check if file has been removed
                                self.files.pop(fname)

                print(f"I PYSERVER::run_drive_sync(): Sync {len(gdrive_files)} files")
                for fileinfo in gfiles["files"]:
                    if "md5Checksum" not in fileinfo:
                        continue
                    fid, fname, fmd5Checksum = fileinfo["id"], fileinfo["name"], fileinfo["md5Checksum"]
                    # if file already exists - check its md5
                    flocal = os.path.join(media_dir, fname)
                    with self.lock:
                        if not self.files[fname] and os.path.isfile(flocal):  # if there is already such file
                            if generate_file_md5(flocal) == fmd5Checksum:  # and hash sums are ok
                                self.files[fname] = True  # don't download - just mark as downloaded
                                # self.on_files_changed()
                            else:  # if we see that the file is different
                                os.system(f"rm {flocal}")

                    if not self.files[fname]:  # if not downloaded
                        try:
                            time.sleep(random.randint(3, 7))  # try to avoid google ban
                            # and run downloading function in a separate process
                            # if we don't use a separate process - we'll have troubles with
                            # download speed
                            p = Process(target=gdown.download_via_gdrive_api, args=(fid, flocal, self.service_file))
                            p.start()
                            p.join()
                            # check hash sum of downloaded file
                            if generate_file_md5(flocal) == fmd5Checksum:
                                with self.lock:
                                    self.files[fname] = True
                                    # self.on_files_changed()
                                print(f"I PYSERVER::run_drive_sync(): Downloaded {fname} => {flocal}")
                            else:
                                print(f"E PYSERVER::run_drive_sync(): Couldn't verify checksum for {fname}")
                        except Exception as ex:
                            print(f"Couldn't download file {fid} via gdown. Details: {ex}")
                # self.on_files_changed()

        def list_files(self) -> Dict[str, bool]:
            with self.lock:
                return self.files.copy()  #

    class Command:
        """
        Command structure for a Streamer:
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

        def __init__(self, minion):
            self.minion: Minion = minion
            self.obs = OBSController()

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

        def exec(self, command: str) -> ExecutionStatus:
            """
            Command structure for a Streamer:
            {
                "command": "play media|media stop|set ts volume|get ts volume|...",
                "details": ... may be dict, str, list, int, bool, etc.
            }
            :return: ExecutionStatus
            """
            if not Minion.Command.valid(command):
                raise ValueError(f"MINION: Validation error. Invalid command '{command}'")

            command = json.loads(command)
            if "details" not in command:
                command["details"] = {}
            command, details = command["command"], command["details"]

            if command == "get config":
                # returns "... minion_settings json ..."
                with self.minion.registry_lock:
                    return ExecutionStatus(True, serializable_object=self.minion.registry.minion_settings.dict())
            elif command == "set config":
                # input: {"info": "... minion_settings json ..."}
                if not details or "info" not in details:
                    return ExecutionStatus(False, f"MINION: Invalid details for command '{command}':\n '{details}'")
                with self.minion.registry_lock:
                    self.minion.registry.minion_settings.modify_from(MinionSettings.parse_raw(details["info"]))
                    return self.obs.apply_info(minion_settings=self.minion.registry.minion_settings)
            elif command == "dispose":
                # TODO
                return ExecutionStatus(False, "MINION: Not implemented")
            elif command == "play media":
                # details: {"name": "... 01_video_name.mp4 ...", "search_by_num": True/False, "mode": "check_same|..."}
                if not details or "name" not in details:
                    return ExecutionStatus(False, f"MINION: Invalid details for command '{command}':\n '{details}'")
                search_by_num = None if "search_by_num" not in details else details["search_by_num"]
                mode = None if "mode" not in details else details["mode"]
                return self.obs.run_media(name=details["name"], search_by_num=search_by_num, mode=mode)
            elif command == "stop media":
                return self.obs.stop_media()
            elif command == "refresh source":
                return self.obs.refresh_media_source()
            elif command == "list gdrive files":
                # returns ExecutionStatus(True, serializable_object={"video_1.mp4": True/False, ...})
                return ExecutionStatus(True, "Ok", serializable_object=self.minion.gdrive_worker.list_files())
            else:
                return ExecutionStatus(False, f"MINION: Invalid command {command}")

        def send_gdrive_files(self) -> ExecutionStatus:
            try:
                new_gdrive_files_state = json.dumps(self.minion.gdrive_worker.files)
                self.minion.sio.emit("on_gdrive_files_changed",
                                     data=new_gdrive_files_state,
                                     broadcast=True)
                return ExecutionStatus(True)
            except Exception as ex:
                return ExecutionStatus(False, f"{ex}")

    class BGWorker:
        def __init__(self, minion):
            self.minion: Minion = minion
            self._last_gdrive_files = json.dumps(self.minion.gdrive_worker.files)

        def track_gdrive_files_change(self):
            """
            This function has to be started as a background worker. It tracks google drive files changes
            and broadcasts all changes.
            """
            while True:
                try:
                    with self.minion.gdrive_worker.lock:
                        new_gdrive_files_state = json.dumps(self.minion.gdrive_worker.files)
                        if self._last_gdrive_files != new_gdrive_files_state:
                            self._last_gdrive_files = new_gdrive_files_state

                            self.minion.command.send_gdrive_files()
                            # self.minion.sio.emit("on_gdrive_files_changed",
                            #                      data=new_gdrive_files_state,
                            #                      broadcast=True)

                except Exception as ex:
                    print(f"E Minion::BGWorker::track_gdrive_files_change(): "
                          f"Couldn't broadcast gdrive files change. Details: {ex}")  # TODO: handle and log
                self.minion.sio.sleep(0.2)

    def __init__(self, port=6006):
        self.port = port
        self.sio = socketio.Server(async_mode="threading", cors_allowed_origins="*")
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)

        # self.app = socketio.WSGIApp(self.sio, Flask("__main__"))
        self.registry = Minion.Registry()
        self.gdrive_worker = Minion.GDriveFileWorker(self)
        self.command = Minion.Command(self)
        self.bg_worker = Minion.BGWorker(self)

        self.registry_lock = RLock()

    def _setup_event_handlers(self):
        self.sio.on("connect", handler=self._on_connect)
        self.sio.on("disconnect", handler=self._on_disconnect)
        self.sio.on("command", handler=self._on_command)

    def _setup_background_tasks(self):
        self.sio.start_background_task(self.bg_worker.track_gdrive_files_change)

    def _on_connect(self, sid, environ):
        """
        The connect event is an ideal place to perform user authentication, and any necessary mapping
        between user entities in the application and the sid that was assigned to the client.
        The environ argument is a dictionary in standard WSGI format containing the request information,
        including HTTP headers. The auth argument contains any authentication details passed by the client,
        or None if the client did not pass anything. After inspecting the request, the connect event
        handler can return False to reject the connection with the client.

        Sometimes it is useful to pass data back to the client being rejected. In that case instead of
        returning False socketio.exceptions.ConnectionRefusedError can be raised, and all of its arguments
        will be sent to the client with the rejection message:

        @sio.event
        def connect(sid, environ):
            raise ConnectionRefusedError('authentication failed')
        """
        pass

    def _on_disconnect(self, sid):
        # disconnect
        pass

    def _on_command(self, sid, data):
        try:
            return self.command.exec(data).json()
        except Exception as ex:
            return ExecutionStatus(False, f"Details: {ex}").json()

    def run(self):
        self._setup_event_handlers()
        self._setup_background_tasks()
        self.gdrive_worker.start()
        self.app.run(host="0.0.0.0", port=self.port)
        # eventlet.wsgi.server(eventlet.listen(('', self.port)), self.app)
