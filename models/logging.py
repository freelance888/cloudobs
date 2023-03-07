from collections import deque
from datetime import datetime
from enum import Enum
from threading import RLock

from pydantic.fields import PrivateAttr


class LogLevel(Enum):
    info = 1
    warn = 2
    error = 3


class Log:
    def __init__(self, level: LogLevel, type: str, message: str, error: Exception = None, extra: dict = None):
        self.level: LogLevel = level
        self.type: str = type
        self.message: str = message
        self.error: str = str(error) if error else None
        self.timestamp: datetime = datetime.now()
        self.extra: dict = extra

    def dict(self):
        return {
            "level": self.level.name,
            "type": self.type,
            "message": self.message,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "extra": self.extra,
        }


class LogsStorage:
    logs: deque[Log] = []
    _lock = PrivateAttr()

    def __init__(self):
        self._lock = RLock()
        self.logs = deque()

    def append(self, log: Log):
        with self._lock:
            self.logs.append(log)
            if len(self.logs) > 1000:  # TODO: Extract logs limit to config
                self.logs.popleft()

    def get(self, count: int):
        with self._lock:
            return self.logs[-count:]
