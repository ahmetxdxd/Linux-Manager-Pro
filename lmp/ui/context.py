"""State shared between pages: settings and the application-list cache."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from lmp.core import apps as apps_core
from lmp.core.settings import Settings
from lmp.ui.background import is_alive, run_async

AppsCallback = Callable[[list | None], None]


class AppContext(QObject):
    apps_loaded = pyqtSignal(list)
    apps_loading = pyqtSignal()
    apps_load_failed = pyqtSignal(str)
    settings_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.apps: list[apps_core.AppEntry] = []
        self._loading = False
        self._pending = False
        # callbacks waiting for a list read AFTER they asked (never an older, in-flight one)
        self._waiters: list[tuple[AppsCallback, QObject | None]] = []

    def reload_apps(self, on_loaded: AppsCallback | None = None,
                    owner: QObject | None = None) -> None:
        """Re-reads the application list in the background.

        `apps_loaded` is emitted when done. `on_loaded` (optional) is called once with a list
        that was read after this call, or with None if reading failed. A request made while a
        read is already running is queued and runs as soon as that read finishes, so it is
        never lost or answered with stale data.
        """
        if on_loaded is not None:
            self._waiters.append((on_loaded, owner))
        if self._loading:
            self._pending = True
            return
        self._start()

    def _start(self) -> None:
        self._loading = True
        self._pending = False
        waiters, self._waiters = self._waiters, []
        self.apps_loading.emit()

        def notify(result) -> None:
            for cb, owner in waiters:
                if owner is None or is_alive(owner):
                    cb(result)

        def done(result) -> None:
            self._loading = False
            self.apps = result
            self.apps_loaded.emit(result)
            notify(result)
            if self._pending:
                self._start()

        def failed(msg) -> None:
            self._loading = False
            self.apps_load_failed.emit(msg)
            notify(None)
            if self._pending:
                self._start()

        run_async(apps_core.load_apps, on_done=done, on_error=failed, owner=self)

    @property
    def loading(self) -> bool:
        return self._loading

    def save_settings(self) -> None:
        self.settings.save()
        self.settings_changed.emit()
