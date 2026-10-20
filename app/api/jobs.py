"""Single background migration job with honest status (never 'completed' until the runner returns)."""
from __future__ import annotations
import datetime as dt, threading

class JobRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"state": "idle", "started_at": None, "finished_at": None, "progress": [], "report": None, "error": None}
        self._thread = None

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._state, "progress": list(self._state["progress"])}

    def start(self, fn) -> bool:
        """fn(progress_cb) -> report dict. Returns False if a job is already running."""
        with self._lock:
            if self._state["state"] == "running":
                return False
            self._state = {"state": "running", "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "finished_at": None, "progress": [], "report": None, "error": None}
        def progress(*a):
            with self._lock: self._state["progress"].append(" ".join(str(x) for x in a))
        def run():
            try:
                rep = fn(progress)
                state = "completed" if rep.get("status") == "COMPLETED" else "failed"
                err = None if state == "completed" else rep.get("message")
            except Exception as e:  # noqa
                rep, state, err = None, "failed", f"{type(e).__name__}: {e}"
            with self._lock:
                self._state.update(state=state, report=rep, error=err,
                                   finished_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        self._thread = threading.Thread(target=run, name="schemashift-migration", daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float = 30.0):
        if self._thread: self._thread.join(timeout)
