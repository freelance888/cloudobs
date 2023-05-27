# REFACTORING: delete this file, use sheets.py

import os
import re
from datetime import timedelta
from typing import Dict

import pandas as pd
import pygsheets
from dotenv import load_dotenv

from models import MinionSettings, User

load_dotenv()
MEDIA_DIR = os.getenv("MEDIA_DIR", "./content")
API_KEY = os.getenv("GDRIVE_API_KEY", "")
SYNC_SECONDS = int(os.getenv("GDRIVE_SYNC_SECONDS", 120))
GDRIVE_SYNC_ADDR = "http://localhost:7000"
SERVICE_FILE = os.getenv("SERVICE_ACCOUNT_FILE")


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
                raise KeyError(f'Multiple entries for lang "{lang}"')

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
            rows.append([lang, source_url, target_server, target_key, gdrive_folder_url])
        return pd.DataFrame(rows, columns=["lang", "source_url", "target_server", "target_key", "gdrive_folder_url"])


class TimingGoogleSheets:
    def __init__(self):
        self.service_file = SERVICE_FILE
        self.gc = pygsheets.authorize(service_account_file=self.service_file)

        self.sheet = None
        self.ws = None

        self.setup_status = False

    @classmethod
    def to_timedelta(cls, timestamp_str):
        """
        :param timestamp_str: string representation of time. Format of 00:00:00[.000]
        :return:
        """
        if re.fullmatch("\d{1,2}\:\d{2}\:\d{2}\.\d{1,6}", timestamp_str):  # format of 00:00:00.000000
            r = re.search(r"(?P<hour>\d{1,2})\:(?P<minute>\d{2})\:(?P<second>\d{2})\.(?P<microsecond>\d{1,6})",
                          timestamp_str)
            hour, minute, second = int(r.group("hour")), int(r.group("minute")), int(r.group("second"))
            microseconds = int(r.group("microsecond").ljust(6, "0"))
            return timedelta(hours=hour, minutes=minute, seconds=second, microseconds=microseconds)
        elif re.fullmatch("\d{1,2}\:\d{2}\:\d{2}", timestamp_str):  # format of 00:00:00
            r = re.search(r"(?P<hour>\d{1,2})\:(?P<minute>\d{2})\:(?P<second>\d{2})", timestamp_str)
            hour, minute, second = int(r.group("hour")), int(r.group("minute")), int(r.group("second"))
            return timedelta(hours=hour, minutes=minute, seconds=second)
        else:
            raise f"Timestamp has invalid format: {timestamp_str}"

    def set_sheet(self, sheet_url, worksheet_name):
        self.sheet = self.gc.open_by_url(sheet_url)
        self.ws = self.sheet.worksheet_by_title(worksheet_name)
        self.setup_status = True

    def pull(self) -> pd.DataFrame:
        """
        df - dataframe, e.g.:
        timestamp   name
        0:12:00     01_video.mp4
        05:10:12    02_video.mp4
        12:01:01    03_video.mp4
        """
        df = self.ws.get_as_df()  # load data from google sheets

        df["timestamp"] = df["timestamp"].apply(TimingGoogleSheets.to_timedelta)
        df = df[["timestamp", "name"]]

        return df


class UsersGoogleSheets:
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

    def get_user_by_login(self, login: str) -> User | None:
        all_logins = self.ws.get_col(1, include_tailing_empty=False)
        index = next((i + 1
                      for i, val in enumerate(all_logins)
                      if val == login), -1)
        if index == -1:
            return None

        row = self.ws.get_row(index, include_tailing_empty=False)
        return User(
            login=row[0],
            passwd=row[1],
            passwd_hash=row[2],
            permissions=[] if row[3].strip() == "" else row[3].split(" "),
        )

    def set_passwd(self, index: int, passwd_placeholder: str, passwd_hash: str):
        self.ws.update_value(f"B{index}", passwd_placeholder)
        self.ws.update_value(f"C{index}", passwd_hash)

    def reset_passwd_hash(self, index: int):
        self.ws.update_value(f"C{index}", "")

    def get_all_passwds(self) -> list:
        passwd_col = self.ws.get_col(2, include_tailing_empty=False)[3:]
        hashes_col = self.ws.get_col(3, include_tailing_empty=False)[3:]
        passwd_len = len(passwd_col)
        hashes_len = len(hashes_col)
        return [{
            "col": i + 4,
            "passwd": passwd_col[i] if i < passwd_len else "",
            "hash": hashes_col[i] if i < hashes_len else "",
        } for i in range(max(passwd_len, hashes_len))]
