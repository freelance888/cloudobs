from threading import Thread

import os
from dotenv import load_dotenv
from server import Skipper

skipper = Skipper(port=5010)


class T(Thread):
    def __init__(self, skipper):
        super().__init__()
        self.skipper = skipper

    def run(self):
        self.skipper.run()


t = T(skipper)
t.start()
