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
    logs: deque[dict] = []
    _lock = PrivateAttr()

    def __init__(self):
        self._lock = RLock()
        self.logs = deque()

    def append(self, log: Log):
        print('Append log:', log)
        with self._lock:
            self.logs.append(log.dict())
            if len(self.logs) > 1000:  # TODO: Extract logs limit to config
                self.logs.popleft()

    def get(self, count: int) -> list[dict]:
        with self._lock:
            print('self.logs', self.logs)
            return list(self.logs)[-count:]
