from __future__ import print_function

import io
import json
import os.path
import time
import random
import gdown
from flask import Flask
from flask import request
import threading
from dotenv import load_dotenv

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError
from util.util import GDriveFiles
from util.util import generate_file_md5
from util.util import log

load_dotenv()
MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 120))
b_init, drive_id, media_dir, api_key, sync_seconds = False, None, "", None, 2
lock = threading.Lock()

app = Flask(__name__)

class DriveSync(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.files = GDriveFiles(with_lock=True)
        self.creds = None

    def run(self):
        """
        Every `sync_period_seconds` lists child files within
        a specified `drive_id`, compares with a list of files
        in `local_dir` directory, and downloads the new ones.
        Does not raise exceptions only if `sync_seconds` is
        a correct number, greater than zero.
        """
        while True:
            try:
                if not b_init:
                    time.sleep(sync_seconds)
                    continue
                with lock:
                    with build("drive", "v3", developerKey=api_key) as service:
                        # list the drive files, the response is like the following structure:
                        """
                        {'kind': 'drive#fileList',
                         'incompleteSearch': False,
                         'files': [{'kind': 'drive#file',
                           'id': '1HCFWjOE-XTAh_Mau7DrSYsR3_uu0Ei3r',
                           'name': 'electron_edited.wav',
                           'mimeType': 'audio/wav'}]}
                        """
                        time.sleep(random.randint(1, 5))
                        files = service.files().list(q=f"'{drive_id}' in parents",
                                                     supportsAllDrives=True,
                                                     supportsTeamDrives=True,
                                                     includeItemsFromAllDrives=True,
                                                     includeTeamDriveItems=True,
                                                     fields="files(id,name,md5Checksum)").execute()
                        # if something went wrong
                        if "files" not in files:
                            raise Exception(f"Couldn't list files in specified driveId. Error: {files}")

                        for fileinfo in files["files"]:
                            fid, fname = fileinfo["id"], fileinfo["name"]
                            if fname not in self.files:
                                self.files[fname] = False

                        log(f"I PYSERVER::run_drive_sync(): Sync {len(files['files'])} files")
                        for fileinfo in files["files"]:
                            fid, fname, fmd5Checksum = fileinfo["id"], fileinfo["name"], fileinfo["md5Checksum"]
                            # if file already exists - check its md5
                            flocal = os.path.join(media_dir, fname)
                            if not self.files[fname] and os.path.isfile(flocal):
                                if generate_file_md5(flocal) == fmd5Checksum:
                                    self.files[fname] = True

                            if not self.files[fname]:
                                gdown.download(id=fid, output=flocal, quiet=True)
                                # time.sleep(random.randint(1, 5))
                                # request_ = service.files().get_media(fileId=fid)
                                #
                                # with io.FileIO(flocal, mode="w") as fh:
                                #     downloader = MediaIoBaseDownload(fh, request_)
                                #     done = False
                                #     while not done:
                                #         status, done = downloader.next_chunk()
                                #         # print("Download %d%%." % int(status.progress() * 100))
                                if generate_file_md5(flocal) == fmd5Checksum:
                                    self.files[fname] = True
                                    log(f"I PYSERVER::run_drive_sync(): Downloaded {fname} => {flocal}")
                                else:
                                    log(f"E PYSERVER::run_drive_sync(): Couldn't verify checksum for {fname}")
            except Exception as ex:
                log(f"E PYSERVER::run_drive_sync(): {ex}")
            time.sleep(sync_seconds)


@app.route('/init', methods=['POST'])
def init():
    global drive_id, media_dir, api_key, sync_seconds, b_init

    with lock:
        drive_id = request.args.get("drive_id", "")
        media_dir = request.args.get("media_dir", MEDIA_DIR)
        api_key = request.args.get("api_key", "")
        sync_seconds = request.args.get("sync_seconds", SYNC_SECONDS)

        media_dir = os.path.join(media_dir, "media")
        sync_seconds = max(10, int(sync_seconds))

        if not drive_id:
            drive_id = "#"

        b_init = True
        os.system(f"mkdir -p {media_dir}")
    return 'Ok', 200


@app.route('/files', methods=['GET'])
def get_files():
    data = [[fname, state] for fname, state in drive_sync.files.items()]
    return json.dumps(data), 200


@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return '', 200


drive_sync = DriveSync()

if __name__ == "__main__":
    drive_sync.start()
    app.run("0.0.0.0", 7000)
