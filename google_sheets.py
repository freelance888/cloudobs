import pygsheets
import pandas as pd
from server import ServerSettings
import server
from dotenv import load_dotenv
import os
import re
from util import MultilangParams

load_dotenv()
MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
API_KEY = os.getenv("GDRIVE_API_KEY", "")
SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 120))
GDRIVE_SYNC_ADDR = "http://localhost:7000"
SERVICE_FILE = os.getenv("SERVICE_ACCOUNT_FILE")


class OBSGoogleSheets:
    def __init__(self):
        self.service_file = SERVICE_FILE
        self.gc = pygsheets.authorize(self.service_file)

        self.sheet = None
        self.ws = None
        self.settings = {}  # dictionary of Lang: ServerSettings()

        self._ok = False

    def ok(self):
        return self._ok

    def set_sheet(self, sheet_url, worksheet_name):
        self.sheet = self.gc.open_by_url(sheet_url)
        self.ws = self.sheet.worksheet_by_title(worksheet_name)
        self.settings = {}
        self._ok = True

    def langs(self):
        return list(self.settings.keys())

    def pull(self):
        data = self.ws.get_as_df()  # load data from google sheets
        self.from_df(data)

    def push(self):
        df = self.to_df()
        self.ws.set_dataframe(df, (1, 1))

    def from_info(self, lang, info):
        """
        Updates server settings from the given `info`. Also pushes the dataframe to the google sheet
        """
        # TODO
        langs = self.langs()
        if lang not in langs:  # skip lang if it is not in our langs
            raise KeyError(f"Invalid lang {lang}")
        for subject, data in info.items():  # for each subject
            for k, v in data.items():  # for each key-value pair
                self._set_subject_value(lang, subject, k, v)
        self.push()

    def dump_info(self, lang):
        # TODO
        return self.settings[lang].to_dict()

    def from_df(self, df):
        for lang in df["lang"]:  # for each lang
            data_ = df.loc[df["lang"] == lang]
            source_url = data_["source_url"].values[0]
            target_server = data_["target_server"].values[0]
            target_key = data_["target_key"].values[0]
            gdrive_folder_url = data_["gdrive_folder_url"].values[0]
            if gdrive_folder_url:
                url = re.search(r"\/folders\/(?P<id>[a-zA-Z0-9\_\-]+)", gdrive_folder_url)
                if len(url.groups()) != 1:
                    raise ValueError(f"Invalid link: {gdrive_folder_url}")
                gdrive_folder_id = url.group("id")
            else:
                gdrive_folder_id = ""

            self._update_lang(lang,
                             source_url=source_url,
                             target_server=target_server,
                             target_key=target_key,
                             gdrive_folder_id=gdrive_folder_id)

    def to_df(self):
        """
        Forms a dataframe based on self.settings.
        Has the following structure:
        | lang    | source_url    | target_server | target_key    | gdrive_folder_url   |
        """
        data = []
        for lang in self.settings:
            # settings: ServerSettings = self.settings[lang]
            source_url = self._get_value(lang, "source_url")
            target_server = self._get_value(lang, "target_server")
            target_key = self._get_value(lang, "target_key")
            gdrive_folder_id = self._get_value(lang, "gdrive_folder_id")
            gdrive_folder_url = f"https://drive.google.com/drive/u/0/folders/{gdrive_folder_id}"
            data.append([lang, source_url, target_server, target_key, gdrive_folder_url])
        return pd.DataFrame(data, columns=["lang", "source_url", "target_server", "target_key", "gdrive_folder_url"])

    def to_multilang_params(self, subjects=(server.SUBJECT_SERVER_LANGS,
                                            server.SUBJECT_GDRIVE_SETTINGS,
                                            server.SUBJECT_STREAM_SETTINGS)):
        params = {}
        for lang in self.settings:
            info_ = self.dump_info(lang)
            for subject in list(info_.keys()):
                if subject not in subjects:  # leave only those subjects specified in `subjects` parameter
                    info_.pop(subject)
            params[lang] = info_
        return MultilangParams(params, langs=list(self.settings.keys()))

    def _update_lang(self, lang, **kwargs):
        """
        Updates a single language settings.
        **kwargs available keys: [source_url, target_server, target_key, gdrive_folder_id]
        """
        if lang not in self.settings:
            self._init_settings(lang)
        for k, v in kwargs.items():
            self._set_value(lang, k, v)

    def _get_value(self, lang, k):
        if k == "source_url":
            return self.settings[lang].get(server.SUBJECT_SERVER_LANGS, "original_media_url")
        elif k == "target_server":
            return self.settings[lang].get(server.SUBJECT_STREAM_SETTINGS, "server")
        elif k == "target_key":
            return self.settings[lang].get(server.SUBJECT_STREAM_SETTINGS, "key")
        elif k == "gdrive_folder_id":
            return self.settings[lang].get(server.SUBJECT_GDRIVE_SETTINGS, "drive_id")
        elif k == "media_dir":
            return self.settings[lang].get(server.SUBJECT_GDRIVE_SETTINGS, "media_dir")
        elif k == "api_key":
            return self.settings[lang].get(server.SUBJECT_GDRIVE_SETTINGS, "api_key")
        elif k == "sync_seconds":
            return self.settings[lang].get(server.SUBJECT_GDRIVE_SETTINGS, "sync_seconds")
        elif k == "gdrive_sync_addr":
            return self.settings[lang].get(server.SUBJECT_GDRIVE_SETTINGS, "gdrive_sync_addr")
        else:
            raise KeyError(f"Invalid key: {k}")

    def _set_value(self, lang, k, v):
        if k == "source_url":
            self.settings[lang].set(server.SUBJECT_SERVER_LANGS, "original_media_url", v)
        elif k == "target_server":
            self.settings[lang].set(server.SUBJECT_STREAM_SETTINGS, "server", v)
        elif k == "target_key":
            self.settings[lang].set(server.SUBJECT_STREAM_SETTINGS, "key", v)
        elif k == "gdrive_folder_id":
            self.settings[lang].set(server.SUBJECT_GDRIVE_SETTINGS, "drive_id", v)
        elif k == "media_dir":
            self.settings[lang].set(server.SUBJECT_GDRIVE_SETTINGS, "media_dir", v)
        elif k == "api_key":
            self.settings[lang].set(server.SUBJECT_GDRIVE_SETTINGS, "api_key", v)
        elif k == "sync_seconds":
            self.settings[lang].set(server.SUBJECT_GDRIVE_SETTINGS, "sync_seconds", v)
        elif k == "gdrive_sync_addr":
            self.settings[lang].set(server.SUBJECT_GDRIVE_SETTINGS, "gdrive_sync_addr", v)
        else:
            raise KeyError(f"Invalid key: {k}")

    def _set_subject_value(self, lang, subject, k, v):
        if lang not in self.settings:
            self._init_settings(lang)
        self.settings[lang].set(subject, k, v)

    def _init_settings(self, lang):
        self.settings[lang] = ServerSettings()
        self._set_value(lang, "media_dir", MEDIA_DIR)
        self._set_value(lang, "api_key", API_KEY)
        self._set_value(lang, "sync_seconds", SYNC_SECONDS)
        self._set_value(lang, "gdrive_sync_addr", GDRIVE_SYNC_ADDR)
