from __future__ import print_function

import io
import os.path
import time
from flask import Flask
from flask import request
import threading

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

b_init, drive_id, media_dir, api_key, sync_seconds = False, None, None, None, 2
lock = threading.Lock()

app = Flask(__name__)


class DriveSync(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

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
                        files = service.files().list(q=f"'{drive_id}' in parents").execute()
                        # if something went wrong
                        if "files" not in files:
                            raise Exception(f"Couldn't list files in specified driveId. Error: {files}")

                        print(f"I PYSERVER::run_drive_sync(): Sync {len(files['files'])} files")
                        for fileinfo in files["files"]:
                            fid, fname = fileinfo["id"], fileinfo["name"]
                            # if such file is not found locally, download it
                            flocal = os.path.join(media_dir, fname)
                            if not os.path.isfile(flocal):
                                request_ = service.files().get_media(fileId=fid)

                                with io.FileIO(flocal, mode="w") as fh:
                                    downloader = MediaIoBaseDownload(fh, request_)
                                    done = False
                                    while not done:
                                        status, done = downloader.next_chunk()
                                        # print("Download %d%%." % int(status.progress() * 100))
                                    print(
                                        f"I PYSERVER::run_drive_sync(): Downloaded {fname} => {flocal}, status: {status}")
            except Exception as ex:
                print(f"E PYSERVER::run_drive_sync(): {ex}")
            time.sleep(sync_seconds)


@app.route('/init', methods=['POST'])
def init():
    global drive_id, media_dir, api_key, sync_seconds, b_init

    with lock:
        drive_id = request.args.get("drive_id", "")
        media_dir = request.args.get("media_dir", "/home/stream/content")
        api_key = request.args.get("api_key", "")
        sync_seconds = request.args.get("sync_seconds", "120")

        media_dir = os.path.join(media_dir, 'media')
        sync_seconds = max(1, int(sync_seconds))

        b_init = True
        os.system(f"mkdir -p {media_dir}")
    return 'Ok', 200


@app.route('/healthcheck', methods=['GET'])
def healthcheck():
    return '', 200


drive_sync = DriveSync()

if __name__ == "__main__":
    drive_sync.start()
    app.run("0.0.0.0", 7000)
