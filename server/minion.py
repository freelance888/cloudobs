from typing import List, Dict
from models import MinionSettings
from pydantic import BaseModel
from util.util import ExecutionStatus, WebsocketResponse
from flask import Flask
import socketio
import eventlet
import time


class Streamer:
    def __init__(self, port):
        self.port = port
        self.sio = socketio.Server()
        self.app = socketio.WSGIApp(self.sio, Flask("__main__"))

    def _do_background_work(self):
        pass

    def _background_worker(self):
        while True:
            try:
                self._do_background_work()
            except Exception as ex:
                pass

    def _setup_event_handlers(self):
        pass

    def run(self):
        self._setup_event_handlers()
        self.sio.start_background_task(self._background_worker)
        eventlet.wsgi.server(eventlet.listen(('', self.port)), self.app)
