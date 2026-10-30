"""In-app line icons (24×24, coloured to match the theme) and the logo."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from lmp.ui import theme

LOGO_PATH = Path(__file__).resolve().parent.parent / "data" / "icons" / "linux-manager-pro.svg"

_PATHS = {
    "dashboard": '<rect x="3" y="3" width="8" height="9" rx="2"/><rect x="13" y="3" width="8" height="5" rx="2"/>'
                 '<rect x="13" y="10" width="8" height="11" rx="2"/><rect x="3" y="14" width="8" height="7" rx="2"/>',
    "uninstall": '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6 7l1 13h10l1-13"/>'
                 '<path d="M10 11v5.5"/><path d="M14 11v5.5"/>',
    "startup": '<path d="M12 3v8"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    "services": '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>'
                '<path d="M7 7.5h.01"/><path d="M7 16.5h.01"/><path d="M11 7.5h6"/><path d="M11 16.5h6"/>',
    "backup": '<path d="M3 8l2-4h14l2 4"/><rect x="3" y="8" width="18" height="12" rx="2"/>'
              '<path d="M12 11v6"/><path d="M9 14l3 3 3-3"/>',
    "disk": '<path d="M5 13l2.5-8h9l2.5 8"/><rect x="3" y="13" width="18" height="7" rx="2"/>'
            '<path d="M17 16.5h.01"/><path d="M7 16.5h5"/>',
    "build": '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 10h4v4h-4z"/>'
             '<path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
                '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "restart": '<path d="M4 12a8 8 0 1 0 2.34-5.66"/><path d="M4 4v5h5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    "warning": '<path d="M12 4l9 16H3z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
            '<path d="M9 13h6M9 17h4"/>',
    "log": '<path d="M5 5h14M5 10h14M5 15h9M5 20h6"/>',
    "broom": '<path d="M14 4l6 6"/><path d="M17 7l-6.5 6.5"/><path d="M10.5 13.5L5 15l-2 6 6-2 1.5-5.5"/>',
    "export": '<path d="M12 15V3"/><path d="M8 7l4-4 4 4"/><path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "import": '<path d="M12 3v12"/><path d="M8 11l4 4 4-4"/><path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "move": '<path d="M4 12h14"/><path d="M14 8l4 4-4 4"/><path d="M20 5v14"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "package": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5"/><path d="M12 12v9"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "cpu": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9z"/>',
    "power": '<path d="M12 3v8"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
}


def _svg(name: str, color: str, stroke: float = 1.8) -> bytes:
    body = _PATHS.get(name, _PATHS["info"])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=512)
def _pixmap(name: str, color: str, size: int, ratio: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    p = theme.current()
    ic = QIcon()
    for ratio in (1.0, 2.0):
        ic.addPixmap(_pixmap(name, color or p.text, size, ratio), QIcon.Mode.Normal)
        ic.addPixmap(_pixmap(name, p.muted, size, ratio), QIcon.Mode.Disabled)
    return ic


def logo_icon() -> QIcon:
    themed = QIcon.fromTheme("linux-manager-pro")
    if not themed.isNull():
        return themed
    return QIcon(str(LOGO_PATH))


def logo_pixmap(size: int, ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(str(LOGO_PATH))
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(ratio)
    return px


@lru_cache(maxsize=4096)
def app_icon(name: str, fallback: str = "package", size: int = 24) -> QIcon:
    """The application's theme icon; otherwise (or if a path is given) a suitable fallback.

    Cached: table models ask for it on every repaint, and theme lookups are slow."""
    if name:
        if name.startswith("/") and Path(name).exists():
            return QIcon(name)
        themed = QIcon.fromTheme(name)
        if not themed.isNull():
            return themed
    return icon(fallback, theme.current().muted, size)


def clear_caches() -> None:
    """Called after a theme change; icons are coloured for the active theme."""
    _pixmap.cache_clear()
    app_icon.cache_clear()


ICON_SIZE = QSize(18, 18)
