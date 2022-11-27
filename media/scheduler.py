from datetime import datetime, timezone
from threading import Lock

from util.util import CallbackThread, ExecutionStatus


class MediaScheduler:
    class Status:
        running = False
        timestamp: datetime = datetime.now(timezone.utc)

        def to_dict(self):
            return {
                'running': self.running,
                'timestamp': self.timestamp.replace(microsecond=0).isoformat(sep='T')
            }

    def __init__(self):
        self.cb_thread = CallbackThread()
        self.cb_thread.start()

        """
        Schedule: dictionary of
        {
            id: {
                "name": name,
                "timestamp": float(timestamp),
                "is_enabled": True,
                "is_played": False,
                "foo": foo_wrap
            }
        }
        """
        self.schedule = {}
        self._lock = Lock()
        self.status = MediaScheduler.Status()

    def __del__(self):
        self.status.running = False
        self.cb_thread.running = False

    def get_schedule(self):
        schedule = self.schedule.copy()
        for id in list(schedule.keys()):
            schedule[id] = {
                "name": schedule[id]["name"],
                "timestamp": schedule[id]["timestamp"],
                "is_enabled": schedule[id]["is_enabled"],
                "is_played": schedule[id]["is_played"],
            }
        return schedule

    def create_schedule(self, schedule, foo):
        """
        :param foo: a callback to invoke on every timestamp arguments passed:
                    (id, name, timestamp, is_enabled, is_played)
        :param schedule: list of [name, timestamp]
        :return: ExecutionStatus
        """
        self.cb_thread.clean_callbacks()
        self.status.running = False
        self.status.timestamp = None

        def foo_wrap(id_, name):
            """
            :param id_: schedule id
            :param name: schedule video name
            :return:
            """
            timestamp = self.schedule[id_]["timestamp"]
            is_enabled = self.schedule[id_]["is_enabled"]
            is_played = self.schedule[id_]["is_played"]

            result = foo(id_, name, timestamp, is_enabled, is_played)
            if result:
                self.schedule[id_]["is_played"] = True

        try:
            # [id, name, timestamp, is_enabled, is_played]
            self.schedule = {
                i: {
                    "name": name,
                    "timestamp": float(timestamp),
                    "is_enabled": True,
                    "is_played": False,
                    "foo": foo_wrap,
                }
                for i, (name, timestamp) in enumerate(schedule)
            }

            return ExecutionStatus(True, message="Ok")
        except ValueError as ex:
            msg = f"The schedule structure is invalid, required [..., [name, timestamp], ...]. Details: {ex}"
            print(f"E PYSERVER::MediaScheduler::create_schedule(): {msg}")
            return ExecutionStatus(False, message=msg)

    def start_schedule(self, delay=0.0):
        """
        Starts the schedule
        :param delay:
        :return:
        """
        try:

            def timestamp_foo(id_):
                return lambda: int(self.schedule[id_]["timestamp"]) + delay

            for id_, data in self.schedule.items():
                self.cb_thread.append_callback(foo=data["foo"], args=(id_, data["name"]), delay=timestamp_foo(id_))

            self.status.running = True
            self.status.timestamp = datetime.utcnow()
            return ExecutionStatus(True, message="Ok")
        except Exception as ex:
            msg = f"Couldn't start the schedule. Details: {ex}"
            print(f"E PYSERVER::MediaScheduler::start_schedule(): {msg}")
            self.status.running = False
            return ExecutionStatus(False, message=msg)

    def modify_schedule(self, id_, name=None, timestamp=None, is_enabled=None, is_played=None):
        """
        Modifies a schedule entry by a given id, modifies only those parameters which are not None
        :return: ExecutionStatus
        """
        if id_ not in self.schedule:
            return ExecutionStatus(False, message="No such id found in schedule")
        try:
            if name:
                self.schedule[id_]["name"] = name
            if timestamp:
                self.schedule[id_]["timestamp"] = float(timestamp)
            if is_enabled is not None:
                self.schedule[id_]["is_enabled"] = bool(is_enabled)
            if is_played is not None:
                self.schedule[id_]["is_played"] = bool(is_played)
            return ExecutionStatus(True, message="Ok")
        except BaseException as ex:
            msg = f"Something happened, details: {ex}"
            print(f"E PYSERVER::MediaScheduler::modify_schedule(): {msg}")
            return ExecutionStatus(False, message=msg)

    def delete_schedule(self):
        self.cb_thread.clean_callbacks()
        self.schedule = {}
        self.status.running = False
        return ExecutionStatus(True, message="Ok")
