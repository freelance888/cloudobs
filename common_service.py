import os
from dotenv import load_dotenv
from server import Skipper

load_dotenv()
COMMON_SERVICE_PORT = int(os.getenv("COMMON_SERVICE_PORT", 5000))

# Setup Sentry
# ------------
# if env var set - setup integration
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
    )

if __name__ == "__main__":
    skipper = Skipper(port=COMMON_SERVICE_PORT)
    skipper.run()

"""
from server import Skipper
skipper = Skipper()
status = skipper.pull_obs_config("https://docs.google.com/spreadsheets/d/10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME",
                                 "table_4")


# -- server side -- #

from flask import Flask
import socketio
import eventlet
import time

sio = socketio.Server(async_mode='threading')
app = Flask(__name__)
app.wsgi_app = socketio.WSGIApp(sio, app.wsgi_app)


from pydantic import BaseModel, PrivateAttr
class T(BaseModel):
    num = 123
    data = {}
    _lock = PrivateAttr()
    def __init__(self, *args, **kwargs):
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)
    def __getattr__(self, item):
        if item == "_lock":
            return super(T, self).__getattr__(item)
        with self._lock:
            print("getattr", self._lock)
            return super(T, self).__getattr__(item)
    def __setattr__(self, key, value):
        if key == "_lock":
            super(T, self).__setattr__(key, value)
        else:
            with self._lock:
                print("setattr")
                super(T, self).__setattr__(key, value)
t = T()
# sio = socketio.Server()
# app = socketio.WSGIApp(sio, Flask(__name__))

def worker_eventlet():
    app.run(port=8088)
    # eventlet.wsgi.server(eventlet.listen(('', 8088)), app)

def worker_1():
    while True:
        sio.sleep(5)
        # print("ttt")
        # sio.emit("message", "msg")

@sio.event
def connect(sid, environ):
    print('connect ', sid)
    print(sio.environ)

@sio.on("message1")
def on_message(sid, data):
    print(f"Server received a message \"{data}\"...")
    time.sleep(10)
    sio.emit("message", data)
    print(f"Sent back message \"{data}\"")
@sio.on("message2")
def on_message(sid, data):
    print(f"Server received a message \"{data}\"...")
    sio.emit("message", data)
    print(f"Sent back message \"{data}\"")

sio.start_background_task(worker_1)
worker_eventlet()


# -- client
import socketio
sio = socketio.Client()
sio.connect('http://localhost:8088')
sio.emit("message2", "msg2")

class Responder:
    def __init__(self, name):
        self.name = name
        sio.on("message", self.on_message)

    def on_message(self, data):
        print(f"{self.name} received message: {data}")

r = Responder("R1")


# -------------------------------- TESTING callbacks
# --- Server side:

from flask import Flask
import socketio
import eventlet
import time


sio = socketio.Server()
app = socketio.WSGIApp(sio, Flask(__name__))

def worker_eventlet():
    eventlet.wsgi.server(eventlet.listen(('', 8088)), app)

@sio.on("message")
def on_message(sid, data):
    print(f"Server received a message \"{data}\", sending it back")
    time.sleep(1)
    return "asd", 123, 345

worker_eventlet()

# --- Client side

import socketio
import time
sio = socketio.Client()
sio.connect('http://localhost:8088')

class Cls:
    def __init__(self, name):
        self.name = name
    def callback(self, *data):
        print(f"Callback {self.name} received: {data}")

c = Cls("B1")
sio.emit("message", "test", callback=c.callback)
print("t1")
time.sleep(2)
print("t2")
"""
