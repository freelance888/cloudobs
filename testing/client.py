import json
import time

import socketio

from util import WebsocketResponse


def create_client():
    time.sleep(3)
    sio = socketio.Client()

    @sio.event
    def connect():
        print("I'm connected!")
        print()

    @sio.event
    def disconnect():
        print("I'm disconnected :(")
        print()

    @sio.event
    def authenticated():
        print('Authentication successful')
        # Handle authenticated state

    @sio.event
    def authentication_failed():
        print('Authentication failed')
        # Handle authentication failure

    @sio.event
    def connect_error():
        print("The connection failed!")
        print()

    @sio.on("command")
    def on_command(data):
        print("command", data)
        print()

    @sio.on("on_registry_change")
    def on_registry_change(data):
        print("registry change", data)
        print()

    @sio.on("on_log")
    def on_log(data):
        print("log", data)
        print()

    @sio.on("on_auth")
    def on_log(data):
        print("auth result:", data)
        print()

    return sio


def setup_gsheet():
    sio = create_client()
    sio.connect("http://localhost:5010", headers={
        "login": "master",
        "password": "master"
    }, wait_timeout=60)

    ws_response = WebsocketResponse()
    command = {
        "command": "pull config",
        "details": {
            "sheet_url": "https://docs.google.com/spreadsheets/d/1FvMlcSdsitQrzgyLvD58QnzR3ym1k8eyTsSRcqnWOQA",
            "sheet_name": "Sheet2",
            "users_sheet_name": "Users",
        }
    }
    sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
    time.sleep(2)
    ws_response.result()

    time.sleep(3)

    sio.disconnect()


def connect_and_test():
    sio = create_client()
    sio.connect("http://localhost:5010", headers={
        "login": "test1",
        "password": "test1234"
    }, wait_timeout=60)
    time.sleep(1)

    # ----- get info
    ws_response = WebsocketResponse()
    command = {"command": "get info"}
    sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
    time.sleep(2)
    resp = ws_response.result()
    result = json.loads(resp)
    print('first result:', result)

    time.sleep(2)
    ws_response = WebsocketResponse()
    command = {"command": "get logs", "details": {"count": 1000}}
    sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
    time.sleep(2)
    result = json.loads(ws_response.result())
    print('second result:', result)

    # ----- pull config
    # ws_response = WebsocketResponse()
    # command = {
    #     "command": "pull config",
    #     "details": {
    #         "sheet_url": "https://docs.google.com/spreadsheets/d/10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME",
    #         "sheet_name": "table_4"
    #     }
    # }
    # sio.emit(event="command", data=json.dumps(command), callback=ws_response.callback)
    # time.sleep(2)
    # result = json.loads(ws_response.result())
    # print(result)


# setup_gsheet()
connect_and_test()
