# REFACTORING: delete this file, use sheets.py

import os
import re

import pandas as pd
import pygsheets
from dotenv import load_dotenv
from typing import Dict
from models import MinionSettings

load_dotenv()
MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
API_KEY = os.getenv("GDRIVE_API_KEY", "")
SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 120))
GDRIVE_SYNC_ADDR = "http://localhost:7000"
SERVICE_FILE = os.getenv("SERVICE_ACCOUNT_FILE")


# class LangConfig(BaseModel):
#     lang: str
#     source_url: str = ""
#     target_server: str = ""
#     target_key: str = ""
#     gdrive_folder_id: str = ""
#     media_dir: str = MEDIA_DIR
#     api_key: str = API_KEY
#     sync_seconds: int = SYNC_SECONDS
#     gdrive_sync_addr: str = GDRIVE_SYNC_ADDR
#
#     @classmethod
#     def validate_lang(cls, lang):
#         return re.fullmatch(r"[A-Za-z\_]+", lang)
#
#     @validator('lang')
#     def _valid_lang(cls, v):
#         if not LangConfig.validate_lang(v):
#             raise ValueError(f"Invalid lang \"{v}\"")
#         return v


# class SheetConfig:
#     def __init__(self):
#         self._langs = {}
#
#     def __getitem__(self, item) -> LangConfig:
#         if item not in self._langs:
#             raise KeyError(f"No config found for lang \"{item}\"")
#         return self._langs[item]
#
#     def __len__(self):
#         return len(self._langs)
#
#     def set_lang_config(self, lang_config: LangConfig):
#         self._langs[lang_config.lang] = lang_config
#
#     def list_langs(self) -> List[str]:
#         return list(self._langs.keys())
#
#     def list_configs(self) -> List[LangConfig]:
#         return list(self._langs.values())
#
#     def items(self) -> List[List]:
#         return list(self._langs.items())


class OBSGoogleSheets:
    def __init__(self):
        self.service_file = SERVICE_FILE
        self.gc = pygsheets.authorize(service_account_file=self.service_file)

        self.sheet = None
        self.ws = None

        self.setup_status = False

    def set_sheet(self, sheet_url, worksheet_name):
        self.sheet = self.gc.open_by_url(sheet_url)
        self.ws = self.sheet.worksheet_by_title(worksheet_name)
        self.setup_status = True

    def pull(self) -> Dict[str, MinionSettings]:
        df = self.ws.get_as_df()  # load data from google sheets
        return self.parse_df(df)

    def push(self, sheet_config: Dict[str, MinionSettings]):
        df = self.to_df(sheet_config)
        self.ws.set_dataframe(df, (1, 1))

    def parse_df(self, df: pd.DataFrame) -> Dict[str, MinionSettings]:
        langs = {}

        for id in df.index:  # for each lang
            lang = df.loc[id, "lang"]
            source_url = df.loc[id, "source_url"]
            target_server = df.loc[id, "target_server"]
            target_key = df.loc[id, "target_key"]
            gdrive_folder_url = df.loc[id, "gdrive_folder_url"]

            if gdrive_folder_url:  # validate gdrive_folder_url format
                url = re.search(r"\/folders\/(?P<id>[a-zA-Z0-9\_\-]+)", gdrive_folder_url)
                if len(url.groups()) != 1:
                    raise ValueError(f"Invalid link: {gdrive_folder_url}")
                gdrive_folder_id = url.group("id")
            else:
                gdrive_folder_id = ""

            settings = MinionSettings.get_none()

            settings.addr_config.original_media_url = source_url
            settings.stream_settings.server = target_server
            settings.stream_settings.key = target_key
            settings.gdrive_settings.media_dir = MEDIA_DIR
            settings.gdrive_settings.api_key = API_KEY
            settings.gdrive_settings.sync_seconds = SYNC_SECONDS
            settings.gdrive_settings.gdrive_sync_addr = GDRIVE_SYNC_ADDR
            settings.gdrive_settings.folder_id = gdrive_folder_id

            if lang in langs:
                raise KeyError(f"Multiple entries for lang \"{lang}\"")

            langs[lang] = settings

        return langs

    def to_df(self, sheet_config: Dict[str, MinionSettings]) -> pd.DataFrame:
        rows = []
        for lang, settings in sheet_config.items():  # for each lang
            source_url = settings.addr_config.original_media_url
            target_server = settings.stream_settings.server
            target_key = settings.stream_settings.key
            if settings.gdrive_settings.folder_id:
                gdrive_folder_url = f"https://drive.google.com/drive/folders/{settings.gdrive_settings.folder_id}"
            else:
                gdrive_folder_url = ""
            rows.append([
                lang, source_url, target_server, target_key, gdrive_folder_url
            ])
        return pd.DataFrame(rows, columns=["lang", "source_url", "target_server", "target_key",
                                           "gdrive_folder_url"])


class TimingGoogleSheets:
    def __init__(self):
        self.service_file = SERVICE_FILE
        self.gc = pygsheets.authorize(service_account_file=self.service_file)

        self.sheet = None
        self.ws = None

        self.setup_status = False

    @classmethod
    def to_seconds(cls, timestamp_str):
        """
        :param timestamp_str: string representation of time. Format of 00:00:00
        :return:
        """
        if not re.fullmatch(r"\d{1,2}\:\d{2}\:\d{2}", timestamp_str):
            raise f"Timestamp has invalid format: {timestamp_str}"
        r = re.search(r"(?P<hour>\d{1,2})\:(?P<minute>\d{2})\:(?P<second>\d{2})", timestamp_str)
        hour, minute, second = r.group("hour"), r.group("minute"), r.group("second")
        hour, minute, second = int(hour), int(minute), int(second)

        return hour * 3600 + minute * 60 + second * 1

    def set_sheet(self, sheet_url, worksheet_name):
        self.sheet = self.gc.open_by_url(sheet_url)
        self.ws = self.sheet.worksheet_by_title(worksheet_name)
        self.setup_status = True

    def pull(self):
        """
        df - dataframe, e.g.:
        timestamp   name
        0:12:00     01_video.mp4
        05:10:12    02_video.mp4
        12:01:01    03_video.mp4
        """
        df = self.ws.get_as_df()  # load data from google sheets

        df["timestamp"] = df["timestamp"].apply(TimingGoogleSheets.to_seconds)
        df = df[["timestamp", "name"]]

        return df
