from models import MinionSettings
from pydantic import BaseModel
from flask import Flask
import socketio
import eventlet
import json
import os
from obs import OBSController
from threading import Lock
from googleapiclient.discovery import build
from util import ExecutionStatus, generate_file_md5
import random
import gdown
from dotenv import load_dotenv
from typing import Dict


class Minion:
    class Registry(BaseModel):
        minion_settings: MinionSettings = MinionSettings.default("localhost")

    class GDriveFileWorker:
        def __init__(self, streamer):
            self.streamer: Minion = streamer
            self.lock = Lock()
            self.files = {}

            load_dotenv()
            self.service_file = os.getenv("SERVICE_ACCOUNT_FILE", "/home/stream/sa.json")

        def activate_registry(self):
            settings = self.streamer.registry.minion_settings.gdrive_settings

            if settings.is_active():
                return True

            if not settings.api_key or \
                    not self.streamer.command.obs.set_media_dir(settings.media_dir):
                return False

            settings.activate()
            return True

        def check_files(self):
            if not self.activate_registry():
                return

            settings = self.streamer.registry.minion_settings.gdrive_settings
            media_dir = settings.media_dir

            with build("drive", "v3", developerKey=settings.api_key) as service:
                # list the drive files, the response is like the following structure:
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

                for fileinfo in gfiles["files"]:  # for each file on google drive
                    fid, fname = fileinfo["id"], fileinfo["name"]
                    if "md5Checksum" not in fileinfo:
                        continue  # if there is no md5Checksum that means it's not a file, probably a folder
                    if fname not in self.files:  # if we haven't downloaded it before
                        with self.lock:
                            self.files[fname] = False

                gdrive_files = [f["name"] for f in gfiles["files"] if f["name"] in self.files]
                for fname in self.files:  # for each file we have already downloaded
                    # if we have downloaded this file, but it doesn't appear on google drive
                    if fname not in gdrive_files:
                        os.system(f"rm {os.path.join(media_dir, fname)}")  # remove it
                        with self.lock:
                            self.files.pop(fname)

                print(f"I PYSERVER::run_drive_sync(): Sync {len(gdrive_files)} files")
                for fileinfo in gfiles["files"]:
                    if "md5Checksum" not in fileinfo:
                        continue
                    fid, fname, fmd5Checksum = fileinfo["id"], fileinfo["name"], fileinfo["md5Checksum"]
                    # if file already exists - check its md5
                    flocal = os.path.join(media_dir, fname)
                    if not self.files[fname] and os.path.isfile(flocal):
                        if generate_file_md5(flocal) == fmd5Checksum:
                            with self.lock:
                                self.files[fname] = True

                    if not self.files[fname]:
                        try:
                            self.streamer.sio.sleep(random.randint(3, 7))
                            gdown.download_via_gdrive_api(fid, flocal, self.service_file)

                            if generate_file_md5(flocal) == fmd5Checksum:
                                with self.lock:
                                    self.files[fname] = True
                                print(f"I PYSERVER::run_drive_sync(): Downloaded {fname} => {flocal}")
                            else:
                                print(f"E PYSERVER::run_drive_sync(): Couldn't verify checksum for {fname}")
                        except Exception as ex:
                            print(f"Couldn't download file {fid} via gdown. Details: {ex}")

        def list_files(self) -> Dict[str, bool]:
            with self.lock:
                return self.files

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

        def __init__(self, streamer):
            self.streamer = streamer
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
                if "command" not in command or "details" not in command:
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
                raise ValueError("Invalid command")

            command = json.loads(command)
            command, details = command["command"], command["details"]

            if command == "get info":
                # returns "... minion_settings json ..."
                return ExecutionStatus(True, serializable_object=self.streamer.registry.minion_settings.dict())
            elif command == "set info":
                # input: {"info": "... minion_settings json ..."}
                if not details or "info" not in details:
                    return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")
                self.streamer.registry.minion_settings.modify_from(MinionSettings.parse_raw(details["info"]))
                return self.obs.apply_info(minion_settings=self.streamer.registry.minion_settings)
            elif command == "cleanup":
                # TODO
                return ExecutionStatus(False, "Not implemented")
            elif command == "play media":
                # details: {"name": "... 01_video_name.mp4 ...", "search_by_num": True/False, "mode": "check_same|..."}
                if not details or "name" not in details:
                    return ExecutionStatus(False, f"Invalid details for command '{command}':\n '{details}'")
                search_by_num = None if "search_by_num" not in details else details["search_by_num"]
                mode = None if "mode" not in details else details["mode"]
                return self.obs.run_media(name=details["name"], search_by_num=search_by_num, mode=mode)
            elif command == "stop media":
                return self.obs.stop_media()
            elif command == "refresh source":
                return self.obs.refresh_media_source()
            elif command == "list gdrive files":
                # returns ExecutionStatus(True, serializable_object={"video_1.mp4": True/False, ...})
                return ExecutionStatus(True, "Ok", serializable_object=self.streamer.gdrive_worker.list_files())
            else:
                return ExecutionStatus(False, "Invalid command")

    def __init__(self, port=6006):
        self.port = port
        self.sio = socketio.Server(async_mode="threading")
        self.app = Flask(__name__)
        self.app.wsgi_app = socketio.WSGIApp(self.sio, self.app.wsgi_app)

        # self.app = socketio.WSGIApp(self.sio, Flask("__main__"))
        self.registry = Minion.Registry()
        self.gdrive_worker = Minion.GDriveFileWorker(self)
        self.command = Minion.Command(self)

    def _do_background_work(self):
        self.sio.sleep(0.5)

    def _background_worker(self):
        while True:
            try:
                self._do_background_work()
            except Exception as ex:
                pass

    def _gdrive_sync_worker(self):
        while True:
            try:
                self.gdrive_worker.check_files()
                self.sio.sleep(60)
            except Exception as ex:
                print(f"E Streamer::_gdrive_sync_worker: {ex}")

    def _setup_event_handlers(self):
        self.sio.on("connect", handler=self._on_connect)
        self.sio.on("disconnect", handler=self._on_disconnect)
        self.sio.on("command", handler=self._on_command)

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
        self.sio.start_background_task(self._background_worker)
        self.sio.start_background_task(self._gdrive_sync_worker)
        self.app.run(host="0.0.0.0", port=self.port)
        # eventlet.wsgi.server(eventlet.listen(('', self.port)), self.app)
