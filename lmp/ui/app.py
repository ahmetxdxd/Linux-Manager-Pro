from __future__ import annotations

import os
import signal
import sys
import traceback
from datetime import datetime

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from lmp import APP_ID, APP_NAME, i18n
from lmp.core.util import cache_dir, tilde
from lmp.ui import icons, theme
from lmp.i18n import tr
from lmp.ui.context import AppContext


def _ensure_icon_theme() -> None:
    """On tiling WMs (Hyprland, Sway) the Qt icon theme may be empty; pick an installed one."""
    from PyQt6.QtGui import QIcon
    paths = QIcon.themeSearchPaths()
    for extra in (os.path.expanduser("~/.local/share/icons"), "/usr/share/icons"):
        if extra not in paths and os.path.isdir(extra):
            paths.append(extra)
    QIcon.setThemeSearchPaths(paths)
    if QIcon.themeName() not in ("", "hicolor"):
        return
    for name in ("breeze", "breeze-dark", "Papirus", "Adwaita"):
        for base in ("/usr/share/icons", os.path.expanduser("~/.local/share/icons")):
            if os.path.isdir(os.path.join(base, name)):
                QIcon.setThemeName(name)
                return


class Application:
    def __init__(self, argv: list[str]):
        self.qt = QApplication(argv)
        self.qt.setApplicationName(APP_ID)
        self.qt.setApplicationDisplayName(APP_NAME)
        self.qt.setDesktopFileName(APP_ID)
        self.qt.setWindowIcon(icons.logo_icon())
        self.qt.setStyle("Fusion")
        _ensure_icon_theme()
        self.ctx = AppContext()
        i18n.set_language(self.ctx.settings.language)
        self.window = None
        self._in_hook = False
        # PyQt aborts the whole application on an exception escaping a slot unless a hook is
        # installed; report it instead and keep running.
        sys.excepthook = self._excepthook
        self.apply_theme()

    def _excepthook(self, exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        try:
            with open(cache_dir() / "errors.log", "a", encoding="utf-8") as log:
                log.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}")
        except OSError:
            pass
        if self._in_hook or exc_type is KeyboardInterrupt:
            return
        self._in_hook = True
        try:
            window = self.window
            if window is not None and not sip.isdeleted(window):
                window.toast(tr("Unexpected error: {e}. Details were written to {path}.").format(
                    e=str(exc) or exc_type.__name__, path=tilde(cache_dir() / "errors.log")),
                    "error")
        finally:
            self._in_hook = False

    def apply_theme(self) -> None:
        palette = theme.set_current(self.ctx.settings.theme)
        check = cache_dir() / "check.svg"
        check.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                         '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="#fff" stroke-width="2" '
                         'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        arrows = {}
        for name, d in (("down", "M4 6l4 4 4-4"), ("up", "M4 10l4-4 4 4")):
            f = cache_dir() / f"chevron-{name}-{palette.name}.svg"
            f.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                         f'<path d="{d}" fill="none" stroke="{palette.muted}" stroke-width="2" '
                         'stroke-linecap="round" stroke-linejoin="round"/></svg>')
            arrows[name] = str(f)
        self.qt.setStyleSheet(theme.stylesheet(palette, str(check), arrows["down"], arrows["up"]))
        icons.clear_caches()

    def build_window(self, start_page: str = "dashboard") -> None:
        from lmp.ui.main_window import MainWindow
        old = self.window
        geometry = old.saveGeometry() if old else None
        self.window = MainWindow(self.ctx, self.on_theme_change, start_page)
        if geometry is not None:
            self.window.restoreGeometry(geometry)
        self.window.show()
        if old:
            old.close()
            old.deleteLater()

    def on_theme_change(self) -> None:
        # Icons are painted for the theme when created, so the window is rebuilt; the same
        # rebuild applies a language change
        i18n.set_language(self.ctx.settings.language)
        self.apply_theme()
        self.build_window("settings")

    def exec(self) -> int:
        if os.geteuid() == 0:
            QMessageBox.warning(None, APP_NAME,
                                tr("Do not run Linux Manager Pro as root. You are asked for "
                                   "your password whenever a task needs privileges."))
        self.build_window()
        return self.qt.exec()


def run(argv: list[str]) -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    page = "dashboard"
    for arg in argv[1:]:
        if arg.startswith("--page="):
            page = arg.split("=", 1)[1]
    app = Application(argv)
    if page != "dashboard":
        app.build_window(page)
        return app.qt.exec()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run(sys.argv))
