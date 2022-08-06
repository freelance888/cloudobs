from util import CallbackThread
from util import ExecutionStatus
from threading import Lock


class MediaScheduler:
    def __init__(self):
        self.cb_thread = CallbackThread()
        self.cb_thread.start()
        self.schedule = {}
        self._lock = Lock()

    def __del__(self):
        self.cb_thread.running = False

    def get_schedule(self):
        return self.schedule

    def create_schedule(self, schedule, foo):
        """
        :param foo: a callback to invoke on every timestamp arguments passed:
                    (id, name, timestamp, is_enabled, is_played)
        :param schedule: list of [name, timestamp]
        :return: ExecutionStatus
        """
        self.cb_thread.clean_callbacks()

        def foo_wrap(id_, name):
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
                    "timestamp": timestamp,
                    "is_enabled": True,
                    "is_played": False
                }
                for i, (name, timestamp) in enumerate(schedule)
            }

            def timestamp_foo(id_):
                return lambda: self.schedule[id_]["timestamp"]

            for id_, data in self.schedule.items():
                self.cb_thread.append_callback(foo=foo_wrap,
                                               args=(id_, data["name"]),
                                               delay=timestamp_foo(id_))
            return ExecutionStatus(True, message="Ok")
        except ValueError as ex:
            msg = f"The schedule structure is invalid, required [..., [name, timestamp], ...]. Details: {ex}"
            print(f"E PYSERVER::MediaScheduler::create_schedule(): {msg}")
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
                self.schedule[id_]["timestamp"] = timestamp
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
        return ExecutionStatus(True, message="Ok")
