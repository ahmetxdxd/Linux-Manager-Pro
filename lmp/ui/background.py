"""Running Python functions in the background without freezing the UI.

Results are delivered on the main thread. Two safeguards keep late results from causing
trouble:

* ``owner``: if the widget that started the job has been destroyed in the meantime (for
  example the window was rebuilt after a theme or language change), the result is dropped.
  When ``owner`` is not given, the QObject behind a bound-method callback is used.
* ``key``: only the most recent job started with the same key delivers its result, so a
  slow, older request can never overwrite the answer of a newer one.
"""

from __future__ import annotations

import inspect
import traceback
from typing import Any, Callable, Hashable

from PyQt6 import sip
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)


class _Job(QRunnable):
    def __init__(self, fn: Callable, args, kwargs, signals: _Signals):
        super().__init__()
        self.fn, self.args, self.kwargs, self.signals = fn, args, kwargs, signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - forwarded to the UI
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.done.emit(result)


_alive: set[_Signals] = set()
_latest: dict[Hashable, int] = {}
_counter = 0


def _owner_of(*callbacks) -> QObject | None:
    for cb in callbacks:
        target = getattr(cb, "__self__", None)
        if isinstance(target, QObject):
            return target
    return None


def is_alive(obj: QObject | None) -> bool:
    """False once the C++ side of a Qt object has been destroyed."""
    return obj is not None and not sip.isdeleted(obj)


def run_async(fn: Callable, *args, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None,
              on_progress: Callable[[int | None, str], None] | None = None,
              owner: QObject | None = None, key: Hashable | None = None, **kwargs) -> None:
    """`fn` runs in the background; the result is delivered to `on_done` on the main thread.

    If `on_progress` is given and `fn` accepts a `progress` parameter, `fn` receives a
    callable ``progress(percent, message)``; ``percent`` is an int 0-100 or None when only a
    message is reported.
    """
    global _counter
    owner = owner or _owner_of(on_done, on_error, on_progress)
    generation = 0
    if key is not None:
        _counter += 1
        generation = _latest[key] = _counter

    def wanted() -> bool:
        if owner is not None and sip.isdeleted(owner):
            return False
        return key is None or _latest.get(key) == generation

    def guard(cb):
        def call(*a):
            if wanted():
                cb(*a)
        return call

    signals = _Signals()
    _alive.add(signals)

    def finish(_=None):
        _alive.discard(signals)
        if key is not None and _latest.get(key) == generation:
            del _latest[key]

    if on_done:
        signals.done.connect(guard(on_done))
    if on_error:
        signals.failed.connect(guard(on_error))
    signals.done.connect(finish)
    signals.failed.connect(finish)
    if on_progress:
        report = guard(on_progress)
        signals.progress.connect(lambda payload: report(*payload))
        try:
            accepts = "progress" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            accepts = False
        if accepts:
            def progress(percent: int | None = None, message: str = "") -> None:
                signals.progress.emit((percent, message))
            kwargs["progress"] = progress
    QThreadPool.globalInstance().start(_Job(fn, args, kwargs, signals))
