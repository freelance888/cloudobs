import clipboard as clip
text = clip.paste()

ip_langs = [[x[6:], x[1:4]] for x in [x.strip() for x in text.split('\n')]]

import socketio
import json
import time
from util import WebsocketResponse
import clipboard as clip

sio = socketio.Client()
sio.connect('http://sa_main:port', auth={"HTTP_LOGIN": "", "HTTP_PASSWORD": ""})

ws_response = WebsocketResponse()
command = {
    "command": "pull config",
    "details": {
        "sheet_url": "https://docs.google.com/spreadsheets/d/10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME",
        "sheet_name": "Master",
        "users_sheet_name": "Users (sample)",
        "ip_langs": dict(ip_langs)
    }
}

ws_response = WebsocketResponse()
command = {
    "command": "pull config",
    "details": {
        "sheet_url": "https://docs.google.com/spreadsheets/d/10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME",
        "sheet_name": "table_4",
        "users_sheet_name": "Users (sample)",
        "ip_langs": {dict([x[::-1] for x in ip_langs])['Fra']: 'Fra'}
    }
}