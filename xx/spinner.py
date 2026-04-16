from __future__ import annotations

import itertools
import sys
import threading
import time


class Spinner:
    def __init__(self, message: str) -> None:
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = sys.stderr.isatty()

    def __enter__(self) -> Spinner:
        if not self._enabled:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def _run(self) -> None:
        for frame in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if self._stop.is_set():
                break
            sys.stderr.write(f"\r{self.message} {frame}")
            sys.stderr.flush()
            time.sleep(0.1)
