"""Small components shared by the pages."""

from __future__ import annotations

from html import escape
from typing import Callable

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                          pyqtProperty, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QProgressBar, QPushButton, QSizePolicy, QToolButton,
                             QVBoxLayout, QWidget)

from lmp.i18n import tr
from lmp.ui import icons, theme


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


def button(text: str, icon_name: str | None = None, role: str | None = None,
           on_click: Callable | None = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if icon_name:
        color = "#ffffff" if role in ("primary", "danger") else None
        btn.setIcon(icons.icon(icon_name, color))
        btn.setIconSize(QSize(16, 16))
    if role:
        btn.setProperty("role", role)
    if on_click:
        btn.clicked.connect(on_click)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def tool_button(icon_name: str, tooltip: str, on_click: Callable | None = None) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(icons.icon(icon_name))
    btn.setIconSize(QSize(18, 18))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, role: str = "card", margins: int = 16):
        super().__init__(parent)
        self.setProperty("role", role)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(margins, margins, margins, margins)
        self.body.setSpacing(8)


class StatTile(Card):
    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.icon(icon_name, theme.current().accent, 18).pixmap(18, 18))
        top.addWidget(ic)
        top.addWidget(label(title, "muted"), 1)
        self.body.addLayout(top)
        self.value = label("…", "stat")
        self.body.addWidget(self.value)
        self.detail = label("", "small", wrap=True)
        self.body.addWidget(self.detail)
        self.body.addStretch()
        self.action_row = QHBoxLayout()
        self.action_row.addStretch()
        self.body.addLayout(self.action_row)

    def set(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class UsageBar(QProgressBar):
    def __init__(self, percent: float = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setFixedHeight(8)
        self.set_percent(percent)

    def set_percent(self, percent: float) -> None:
        self.setValue(int(percent))
        level = "danger" if percent >= 90 else "warning" if percent >= 75 else ""
        self.setProperty("level", level)
        repolish(self)


class SearchField(QLineEdit):
    def __init__(self, placeholder: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder or tr("Search…"))
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("search", theme.current().muted),
                       QLineEdit.ActionPosition.LeadingPosition)
        self.setMinimumWidth(240)


class Banner(Card):
    """In-page info/warning box."""

    def __init__(self, text: str, kind: str = "info", parent: QWidget | None = None):
        super().__init__(parent, role="banner-warning" if kind == "warning" else "banner",
                         margins=12)
        row = QHBoxLayout()
        p = theme.current()
        ic = QLabel()
        color = p.warning if kind == "warning" else p.info
        ic.setPixmap(icons.icon("warning" if kind == "warning" else "info", color, 18)
                     .pixmap(18, 18))
        ic.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(ic)
        self.text = label(text, wrap=True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setOpenExternalLinks(True)
        row.addWidget(self.text, 1)
        self.body.addLayout(row)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.addStretch()
        ic = QLabel()
        ic.setPixmap(icons.icon(icon_name, theme.current().muted, 40).pixmap(40, 40))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic)
        t = label(title, "h2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        self.text = label(text, "muted", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.text)
        lay.addStretch()


class ToggleSwitch(QAbstractButton):
    """On/off switch (easier to read than a checkbox)."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = pyqtProperty(float, get_knob, set_knob)

    def setChecked(self, on: bool) -> None:  # noqa: N802 (Qt name)
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 22)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on_color, off_color = QColor(p.accent), QColor(p.border)
        track = QColor(
            int(off_color.red() + (on_color.red() - off_color.red()) * self._pos),
            int(off_color.green() + (on_color.green() - off_color.green()) * self._pos),
            int(off_color.blue() + (on_color.blue() - off_color.blue()) * self._pos))
        knob = QColor("#ffffff")
        if not self.isEnabled():
            track.setAlpha(90)
            knob.setAlpha(140)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        painter.setBrush(knob)
        x = 3 + self._pos * 18
        painter.drawEllipse(QRectF(x, 3, 16, 16))
        painter.end()


class Toast(QLabel):
    """Notification shown for a few seconds at the bottom of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWordWrap(True)
        self.setMaximumWidth(460)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, kind: str = "info", ms: int = 3800) -> None:
        p = theme.current()
        color = {"ok": p.success, "error": p.danger, "warning": p.warning}.get(kind, p.text)
        # the text often contains file or package names; it must never be read as markup
        self.setText(f'<span style="color:{color}">●</span>&nbsp; {escape(text)}')
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            self.move(parent.width() - self.width() - 24, parent.height() - self.height() - 24)
        self.raise_()
        self.show()
        self._timer.start(ms)


class Page(QWidget):
    """Base of all pages: title + subtitle + actions on the right + content area."""

    toast = pyqtSignal(str, str)
    navigate = pyqtSignal(str)

    title = ""
    subtitle = ""
    icon_name = "info"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Page")
        self._loaded = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr(self.title), "h1"))
        self.subtitle_lbl = label(tr(self.subtitle), "muted", wrap=True)
        titles.addWidget(self.subtitle_lbl)
        header.addLayout(titles, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        header.addLayout(self.header_actions)
        outer.addLayout(header)
        self.content = QVBoxLayout()
        self.content.setSpacing(12)
        outer.addLayout(self.content, 1)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        """Subclasses load their data here (in the background)."""

    def notify(self, text: str, kind: str = "info") -> None:
        self.toast.emit(text, kind)


def confirm(parent: QWidget, title: str, text: str, ok_text: str = "",
            danger: bool = False, informative: str = "") -> bool:
    box = QMessageBox(parent)
    ok_text = ok_text or tr("Continue")
    box.setWindowTitle(title)
    box.setText(text)
    if informative:
        box.setInformativeText(informative)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    ok.setProperty("role", "danger" if danger else "primary")
    cancel = box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel if danger else ok)
    box.exec()
    return box.clickedButton() is ok


def error_box(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def hspacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return w
