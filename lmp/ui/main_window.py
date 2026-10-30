from __future__ import annotations

from PyQt6.QtCore import QSize, QTimer
from PyQt6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QStackedWidget,
                             QVBoxLayout, QWidget)

from lmp import APP_NAME, __version__
from lmp.core.util import is_arch_based, os_release
from lmp.i18n import tr, tr_noop
from lmp.ui import icons
from lmp.ui.context import AppContext
from lmp.ui.pages.builder import BuilderPage
from lmp.ui.pages.dashboard import DashboardPage
from lmp.ui.pages.external import ExternalPage
from lmp.ui.pages.profile import ProfilePage
from lmp.ui.pages.services import ServicesPage
from lmp.ui.pages.settings import SettingsPage
from lmp.ui.pages.startup import StartupPage
from lmp.ui.pages.uninstaller import UninstallerPage
from lmp.ui.widgets import Banner, Toast, button, label

NAV = [
    ("dashboard", tr_noop("Overview"), "dashboard"),
    ("uninstall", tr_noop("Uninstaller"), "uninstall"),
    ("startup", tr_noop("Startup"), "startup"),
    ("services", tr_noop("Services"), "services"),
    ("profile", tr_noop("Backup & Transfer"), "backup"),
    ("external", tr_noop("External Disk"), "disk"),
    ("builder", tr_noop("Builder"), "build"),
    ("settings", tr_noop("Settings"), "settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, on_theme_change, start_page: str = "dashboard"):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.logo_icon())
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)

        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(232)
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(14, 18, 14, 14)
        sv.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(36))
        logo.setFixedSize(38, 38)
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        t = QLabel(APP_NAME)
        t.setObjectName("AppTitle")
        names.addWidget(t)
        st = QLabel(tr("System manager · v{version}").format(version=__version__))
        st.setObjectName("AppSubtitle")
        names.addWidget(st)
        brand.addLayout(names, 1)
        sv.addLayout(brand)
        sv.addSpacing(18)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        factories = {
            "dashboard": lambda: DashboardPage(ctx),
            "uninstall": lambda: UninstallerPage(ctx),
            "startup": lambda: StartupPage(ctx),
            "services": lambda: ServicesPage(ctx),
            "profile": lambda: ProfilePage(ctx),
            "external": lambda: ExternalPage(ctx),
            "builder": lambda: BuilderPage(ctx),
            "settings": lambda: SettingsPage(ctx, on_theme_change),
        }
        for key, text, icon_name in NAV:
            if key == "settings":
                sv.addStretch()
            btn = button(tr(text).replace("&", "&&"), icon_name, "nav")   # "&&" = literal ampersand
            btn.setCheckable(True)
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
            group.addButton(btn)
            sv.addWidget(btn)
            self.nav_buttons[key] = btn
            page = factories[key]()
            page.toast.connect(self.toast)
            page.navigate.connect(self.show_page)
            self.pages[key] = page
            self.stack.addWidget(page)

        distro = os_release().get("PRETTY_NAME", "")
        sv.addSpacing(8)
        sv.addWidget(label(distro, "small"))
        h.addWidget(sidebar)

        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        if not is_arch_based():
            warn = Banner(tr("This system does not look Arch-based. For now Linux Manager Pro only works "
                             "fully on Arch and its derivatives (CachyOS, EndeavourOS, Manjaro…); "
                             "features that need pacman will be disabled."), "warning")
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(28, 16, 28, 0)
            wl.addWidget(warn)
            mv.addWidget(wrap)
        mv.addWidget(self.stack, 1)
        h.addWidget(main, 1)

        self._toast = Toast(self)
        self.show_page(start_page)
        QTimer.singleShot(50, ctx.reload_apps)

    def show_page(self, key: str) -> None:
        action = None
        if ":" in key:
            key, action = key.split(":", 1)
        page = self.pages.get(key)
        if not page:
            return
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page)
        if action == "orphans" and isinstance(page, UninstallerPage):
            QTimer.singleShot(200, page.scan_orphans)

    def current_page_key(self) -> str:
        for k, p in self.pages.items():
            if p is self.stack.currentWidget():
                return k
        return "dashboard"

    def toast(self, text: str, kind: str = "info") -> None:
        self._toast.show_message(text, kind)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._toast.isVisible():
            self._toast.move(self.width() - self._toast.width() - 24,
                             self.height() - self._toast.height() - 24)
