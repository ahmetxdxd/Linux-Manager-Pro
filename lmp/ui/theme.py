"""Colour tokens and the Qt stylesheet generated from them.

Note: in Qt stylesheets, custom classes are matched with the `[role="..."]` property selector;
widgets get it through `setProperty("role", "...")`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface2: str
    sidebar: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    danger: str
    danger_hover: str
    success: str
    warning: str
    info: str
    selection: str
    input_bg: str


DARK = Palette(
    name="dark", bg="#0f1319", surface="#161b23", surface2="#1c222c", sidebar="#0b0e13",
    border="#262d39", text="#e6e9ef", muted="#8a93a5", accent="#3b82f6",
    accent_hover="#2f6fde", accent_text="#ffffff", danger="#e5484d", danger_hover="#cc3a3f",
    success="#2fbf71", warning="#f2a43a", info="#22d3ee", selection="#1f3a63",
    input_bg="#11161d",
)

LIGHT = Palette(
    name="light", bg="#f4f6f9", surface="#ffffff", surface2="#f0f2f6", sidebar="#e9edf3",
    border="#d9dee7", text="#1a1f29", muted="#5f6878", accent="#2563eb",
    accent_hover="#1d4fd0", accent_text="#ffffff", danger="#d93036", danger_hover="#bb262b",
    success="#178a4f", warning="#b86e00", info="#0e8fa8", selection="#d6e4ff",
    input_bg="#ffffff",
)

_current: Palette = DARK


def current() -> Palette:
    return _current


def set_current(name: str) -> Palette:
    global _current
    _current = LIGHT if name == "light" else DARK
    return _current


def stylesheet(p: Palette, check_icon: str = "", down_icon: str = "", up_icon: str = "") -> str:
    check = f"image: url({check_icon});" if check_icon else ""
    down = f"image: url({down_icon});" if down_icon else ""
    up = f"image: url({up_icon});" if up_icon else ""
    return f"""
* {{ outline: none; }}
QWidget {{
    color: {p.text};
    font-size: 10pt;
}}
QMainWindow, QDialog, QWidget#Page, QStackedWidget, QScrollArea > QWidget > QWidget {{
    background: {p.bg};
}}
QToolTip {{
    background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 6px;
}}

/* Sidebar */
QWidget#Sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
QLabel#AppTitle {{ font-size: 12pt; font-weight: 700; }}
QLabel#AppSubtitle {{ color: {p.muted}; font-size: 8.5pt; }}
QPushButton[role="nav"] {{
    text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
    background: transparent; color: {p.muted}; font-weight: 500;
}}
QPushButton[role="nav"]:hover {{ background: {p.surface2}; color: {p.text}; }}
QPushButton[role="nav"]:checked {{ background: {p.selection}; color: {p.text}; font-weight: 600; }}

/* Page header */
QLabel[role="h1"] {{ font-size: 17pt; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 12pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {p.muted}; }}
QLabel[role="small"] {{ color: {p.muted}; font-size: 8.5pt; }}
QLabel[role="stat"] {{ font-size: 18pt; font-weight: 700; }}
QLabel[role="danger"] {{ color: {p.danger}; }}
QLabel[role="success"] {{ color: {p.success}; }}
QLabel[role="warning"] {{ color: {p.warning}; }}

/* Kartlar */
QFrame[role="card"] {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
}}
QFrame[role="banner"] {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 10px;
}}
QFrame[role="banner-warning"] {{
    background: {p.surface2}; border: 1px solid {p.warning}; border-radius: 10px;
}}
QFrame[role="actionbar"] {{
    background: {p.surface}; border-top: 1px solid {p.border};
}}

/* Buttons */
QPushButton {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 7px 14px; color: {p.text};
}}
QPushButton:hover {{ border-color: {p.muted}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; border-color: {p.border}; }}
QPushButton[role="primary"] {{
    background: {p.accent}; border: 1px solid {p.accent}; color: {p.accent_text}; font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background: {p.accent_hover}; }}
QPushButton[role="danger"] {{
    background: {p.danger}; border: 1px solid {p.danger}; color: #ffffff; font-weight: 600;
}}
QPushButton[role="danger"]:hover {{ background: {p.danger_hover}; }}
QPushButton[role="primary"]:disabled, QPushButton[role="danger"]:disabled {{
    background: {p.surface2}; border-color: {p.border}; color: {p.muted};
}}
QPushButton[role="ghost"] {{ background: transparent; border: none; color: {p.accent}; padding: 4px 6px; }}
QPushButton[role="ghost"]:hover {{ color: {p.accent_hover}; }}
QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 5px;
}}
QToolButton:hover {{ background: {p.surface2}; border-color: {p.border}; }}

/* Inputs */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p.input_bg}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {p.accent}; selection-color: #ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {p.accent}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ {down} width: 12px; height: 12px; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    border: none; background: transparent; width: 20px; subcontrol-origin: border;
}}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ {up} width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ {down} width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.selection};
    selection-color: {p.text}; padding: 4px;
}}
QPlainTextEdit[role="log"] {{
    font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace; font-size: 9pt;
    background: {p.sidebar};
}}

/* Tablolar */
QTableView, QTreeView, QListWidget {{
    background: {p.surface}; alternate-background-color: {p.surface2};
    border: 1px solid {p.border}; border-radius: 10px; gridline-color: transparent;
    selection-background-color: {p.selection}; selection-color: {p.text};
}}
QTableView::item, QTreeView::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{
    background: {p.surface}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
    padding: 7px 8px; font-weight: 600; font-size: 9pt;
}}
QTableCornerButton::section {{ background: {p.surface}; border: none; }}

/* Scrollbars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Progress */
QProgressBar {{
    background: {p.surface2}; border: none; border-radius: 4px; height: 8px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QProgressBar[level="warning"]::chunk {{ background: {p.warning}; }}
QProgressBar[level="danger"]::chunk {{ background: {p.danger}; }}

/* Sekmeler */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {p.muted}; padding: 8px 14px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QTabBar::tab:hover {{ color: {p.text}; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QTableView::indicator, QListView::indicator, QTreeView::indicator {{
    width: 15px; height: 15px; border: 1px solid {p.muted}; border-radius: 4px;
    background: {p.input_bg};
}}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked,
QTreeView::indicator:checked {{
    background: {p.accent}; border-color: {p.accent}; {check}
}}
QCheckBox::indicator:disabled {{ background: {p.surface2}; border-color: {p.border}; }}
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.selection}; }}
QLabel#Toast {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 10px;
    padding: 10px 16px;
}}
"""
