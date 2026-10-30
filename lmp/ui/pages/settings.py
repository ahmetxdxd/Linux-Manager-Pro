from __future__ import annotations

import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QSpinBox

from lmp import APP_NAME, __version__, i18n
from lmp.core import pacman, privileged
from lmp.core.util import data_dir, tilde
from lmp.i18n import tr, tr_noop
from lmp.ui import icons
from lmp.ui.widgets import Card, Page, button, label


class SettingsPage(Page):
    title = tr_noop("Settings")
    subtitle = tr_noop("Appearance and behaviour preferences")
    icon_name = "settings"

    def __init__(self, ctx, on_theme_change, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.on_theme_change = on_theme_change    # also used for language changes
        s = ctx.settings
        # changes are written once the user stops fiddling (a spin box emits on every step)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._write)

        general = Card()
        general.body.addWidget(label(tr("General"), "h2"))
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Language names are shown in their own language so they stay recognisable
        self.language = QComboBox()
        for code, name in i18n.LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(s.language)))
        self.language.currentIndexChanged.connect(self._language_changed)
        form.addRow(tr("Language"), self.language)

        self.theme = QComboBox()
        self.theme.addItem(tr("Dark"), "dark")
        self.theme.addItem(tr("Light"), "light")
        self.theme.setCurrentIndex(1 if s.theme == "light" else 0)
        self.theme.currentIndexChanged.connect(self._theme_changed)
        form.addRow(tr("Theme"), self.theme)

        self.delete_mode = QComboBox()
        self.delete_mode.addItem(tr("Move to Trash (recoverable)"), "trash")
        self.delete_mode.addItem(tr("Delete permanently"), "permanent")
        self.delete_mode.setCurrentIndex(1 if s.delete_mode == "permanent" else 0)
        self.delete_mode.currentIndexChanged.connect(self._save)
        form.addRow(tr("Leftover files"), self.delete_mode)

        self.scan_after = QCheckBox(tr("Scan for leftovers automatically after uninstalling"))
        self.scan_after.setChecked(s.scan_leftovers_after_uninstall)
        self.scan_after.toggled.connect(self._save)
        form.addRow("", self.scan_after)
        self.scan_system = QCheckBox(tr("Also scan system locations (/etc, /var/lib, /opt …)"))
        self.scan_system.setChecked(s.include_system_leftovers)
        self.scan_system.toggled.connect(self._save)
        form.addRow("", self.scan_system)

        self.aur = QComboBox()
        detected = pacman.aur_helper("auto")
        self.aur.addItem(tr("Automatic ({detected})").format(detected=detected or tr("not found")),
                         "auto")
        self.aur.addItem("yay", "yay")
        self.aur.addItem("paru", "paru")
        self.aur.addItem(tr("Don't use"), "none")
        self.aur.setCurrentIndex(max(0, self.aur.findData(s.aur_helper)))
        self.aur.currentIndexChanged.connect(self._save)
        form.addRow(tr("AUR helper"), self.aur)

        self.keep = QSpinBox()
        self.keep.setRange(0, 5)
        self.keep.setValue(s.cache_keep)
        self.keep.setSuffix(" " + tr("versions"))
        self.keep.valueChanged.connect(self._save)
        form.addRow(tr("Versions kept in cache"), self.keep)
        for w in (self.language, self.theme, self.delete_mode, self.aur, self.keep):
            w.setMinimumWidth(320)
        general.body.addLayout(form)
        self.content.addWidget(general)

        about = Card()
        row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(64))
        logo.setFixedSize(68, 68)
        row.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        col_text = (f"<b style='font-size:13pt'>{APP_NAME}</b> &nbsp;"
                    f"{tr('version {v}').format(v=__version__)}<br>"
                    + tr("All-in-one system manager for Arch Linux: complete uninstaller, "
                         "startup and service manager, profile backup/transfer, external-disk "
                         "installs and local builds."))
        text = label(col_text, wrap=True)
        text.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(text, 1)
        about.body.addLayout(row)
        mode = (tr("Installed version: privileged tasks run through "
                   "/usr/lib/linux-manager-pro/lmp-helper.")
                if privileged.is_installed() else
                tr("Development mode: privileged tasks run through helper.py in the source "
                   "folder. Run ./install.sh in the project folder for a full install."))
        about.body.addWidget(label(mode, "small", wrap=True))
        about.body.addWidget(label(tr("Data: {path}").format(path=tilde(data_dir())), "small"))
        links = QHBoxLayout()
        links.addWidget(button(tr("Open data folder"), "folder", on_click=lambda: subprocess.Popen(
            ["xdg-open", str(data_dir())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        links.addStretch()
        about.body.addLayout(links)
        self.content.addWidget(about)
        self.content.addStretch()

    def _save(self) -> None:
        self._save_timer.start()

    def _write(self) -> None:
        s = self.ctx.settings
        s.delete_mode = self.delete_mode.currentData()
        s.scan_leftovers_after_uninstall = self.scan_after.isChecked()
        s.include_system_leftovers = self.scan_system.isChecked()
        s.aur_helper = self.aur.currentData()
        s.cache_keep = self.keep.value()
        self.ctx.save_settings()
        self.notify(tr("Settings saved."), "ok")

    def _flush(self) -> None:
        """Pending changes must not be lost when the window is rebuilt."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._write()

    def hideEvent(self, event) -> None:  # noqa: N802 - leaving the page or closing the window
        self._flush()
        super().hideEvent(event)

    def _theme_changed(self) -> None:
        self._flush()
        self.ctx.settings.theme = self.theme.currentData()
        self.ctx.save_settings()
        self.on_theme_change()

    def _language_changed(self) -> None:
        self._flush()
        self.ctx.settings.language = self.language.currentData()
        self.ctx.save_settings()
        self.on_theme_change()    # rebuilds the whole window in the new language
