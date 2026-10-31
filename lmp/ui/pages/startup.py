from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                             QSpinBox, QVBoxLayout, QWidget)

from lmp.core import autostart
from lmp.i18n import tr, tr_noop, trn
from lmp.ui import icons
from lmp.ui.background import run_async
from lmp.ui.widgets import (Banner, Card, EmptyState, Page, ToggleSwitch, UsageBar, button, confirm,
                            label, tool_button)


class AddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Add to startup"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.addWidget(label(tr("Program to run at login"), "h2"))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("e.g. My sync script"))
        self.command = QLineEdit()
        self.command.setPlaceholderText(tr("e.g. /home/user/bin/sync.sh  or  discord --start-minimized"))
        self.comment = QLineEdit()
        self.delay = QSpinBox()
        self.delay.setRange(0, 600)
        self.delay.setSuffix(tr(" s"))
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Command"), self.command)
        form.addRow(tr("Description"), self.comment)
        form.addRow(tr("Delay"), self.delay)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button(tr("Cancel"), on_click=self.reject))
        self.ok = button(tr("Add"), "plus", "primary", on_click=self.accept)
        row.addWidget(self.ok)
        lay.addLayout(row)


class AutostartRow(QWidget):
    def __init__(self, item: autostart.AutostartItem, page: "StartupPage"):
        super().__init__()
        self.item = item
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 6, 4, 6)
        row.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(icons.app_icon(item.icon, "power", 28).pixmap(28, 28))
        ic.setFixedSize(30, 30)
        row.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        title = label(item.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setMinimumWidth(1)        # a long name is cut, it must not push the switch away
        title.setToolTip(item.name)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(title)
        if item.running:
            running = label(tr("Running now"), "success")
            if item.runner:
                running.setToolTip(item.runner)
            title_row.addWidget(running)
        title_row.addStretch()
        col.addLayout(title_row)
        detail = item.comment or item.command
        meta = f"{item.origin_label} · {detail}"
        if item.desktop_mismatch:
            meta += tr(" · would not run on this desktop anyway")
        sub = label(meta, "small", wrap=True)
        sub.setTextFormat(Qt.TextFormat.PlainText)
        sub.setToolTip(tr("Command: {cmd}\nFile: {file}").format(
            cmd=item.command, file=item.user_path or item.system_path or item.source))
        col.addWidget(sub)
        if item.other_start and not item.enabled:
            via = label(tr("Switched off here, but {unit} still starts it.").format(unit=item.runner),
                        "warning", wrap=True)
            via.setTextFormat(Qt.TextFormat.PlainText)
            via.setToolTip(tr("This program is also started by a systemd service of your desktop. "
                              "Switching off its startup entry does not stop that service."))
            col.addWidget(via)
        row.addLayout(col, 1)
        if item.kind == autostart.APP and item.origin == "user":
            row.addWidget(tool_button("uninstall", tr("Delete this entry"),
                                      lambda: page.remove(item)))
        self.switch = ToggleSwitch(item.enabled)
        tips = {
            autostart.APP: tr("Run at login. Switched off here, it stays off even if the app "
                              "turns itself back on."),
            autostart.SERVICE: tr("Start this background service at login "
                                  "(systemctl --user enable/disable)."),
            autostart.WM: tr("Run this command when the window manager starts. Switching it off "
                             "comments the line out in {file}; switching it on restores it.").format(
                                 file=item.user_path.name if item.user_path else ""),
        }
        self.switch.setToolTip(tips[item.kind])
        if item.readonly:
            self.switch.setEnabled(False)
            self.switch.setToolTip(tr("This system service cannot be switched off from here."))
        self.switch.toggled.connect(lambda on: page.toggle(item, on, self.switch))
        row.addWidget(self.switch)


class StartupPage(Page):
    title = tr_noop("Startup")
    subtitle = tr_noop("Programs that run at login, and services that slow down boot")
    icon_name = "startup"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.items: list[autostart.AutostartItem] = []
        self._xdg_runs = True
        self.disable_all_btn = button(tr("Disable all"), "power", on_click=self.disable_all,
                                      tooltip=tr("Turn off every application that starts at "
                                                 "login, in one click (you can undo it). "
                                                 "Window-manager commands and background "
                                                 "services are not touched."))
        self.restore_btn = button(tr("Undo"), "undo", on_click=self.restore,
                                  tooltip=tr("Turn back on what “Disable all” switched off"))
        self.restore_btn.setVisible(False)
        self.header_actions.addWidget(self.restore_btn)
        self.header_actions.addWidget(self.disable_all_btn)
        self.header_actions.addWidget(button(tr("Add program"), "plus", "primary",
                                             on_click=self.add))
        self.header_actions.addWidget(tool_button("refresh", tr("Refresh"), self.refresh))

        row = QHBoxLayout()
        row.setSpacing(14)

        left = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("Started at login"), "h2"))
        head.addStretch()
        self.count = label("", "muted")
        head.addWidget(self.count)
        left.body.addLayout(head)
        left.body.addWidget(label(tr("Switch off only what you don't want. What you switch off "
                                     "stays off, even if the app (Discord, Steam…) tries to turn "
                                     "itself back on. System files are never modified."),
                                  "small", wrap=True))
        self.xdg_banner = Banner(tr("Your window manager does not start application entries "
                                    "(XDG autostart) by itself; they only run if your config "
                                    "starts <b>dex</b> or you use <b>uwsm</b>. Your real start "
                                    "commands are the window-manager entries in this list."),
                                 "warning")
        self.xdg_banner.setVisible(False)
        left.body.addWidget(self.xdg_banner)
        self.other_banner = Banner("", "warning")
        self.other_banner.setVisible(False)
        left.body.addWidget(self.other_banner)
        self.show_other = QCheckBox()
        self.show_other.setVisible(False)
        self.show_other.toggled.connect(lambda _on: self._render())
        self.list_area = QScrollArea()
        self.list_area.setWidgetResizable(True)
        self.list_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_host.setObjectName("AutostartList")
        self.list_host.setStyleSheet("#AutostartList { background: transparent; }")
        self.list_area.viewport().setAutoFillBackground(False)
        self.list_area.setStyleSheet("QScrollArea { background: transparent; }")
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_area.setWidget(self.list_host)
        left.body.addWidget(self.list_area, 1)
        left.body.addWidget(self.show_other)
        row.addWidget(left, 3)

        right = Card()
        right.body.addWidget(label(tr("Boot time"), "h2"))
        self.boot_total = label("…", "stat")
        right.body.addWidget(self.boot_total)
        self.boot_detail = label("", "small", wrap=True)
        right.body.addWidget(self.boot_detail)
        right.body.addWidget(label(tr("Slowest units to start"), "muted"))
        self.blame_lay = QVBoxLayout()
        self.blame_lay.setSpacing(6)
        right.body.addLayout(self.blame_lay)
        right.body.addStretch()
        go = button(tr("Manage services"), "services",
                    on_click=lambda: self.navigate.emit("services"))
        right.body.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(right, 2)
        self.content.addLayout(row, 1)

    def refresh(self) -> None:
        def work():
            autostart.enforce_blocked()          # undo apps that switched themselves back on
            autostart.sync_guard()
            items = autostart.inspect_items(autostart.list_items())
            return items, autostart.xdg_entries_run_here(), bool(autostart.disabled_snapshot())

        run_async(work, on_done=self._show_items, key=(id(self), "items"),
                  on_error=lambda e: self.notify(tr("Could not read startup entries: {e}").format(e=e), "error"))
        run_async(autostart.boot_report, on_done=self._show_boot, key=(id(self), "boot"),
                  on_error=lambda e: self._show_boot(None))

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_items(self, result) -> None:
        items, self._xdg_runs, can_undo = result
        self.items = items
        relevant = [i for i in items if not i.desktop_mismatch]
        enabled = sum(1 for i in relevant if i.enabled)
        self.count.setText(tr("{n} enabled / {total}").format(n=enabled, total=len(relevant)))
        self.disable_all_btn.setEnabled(any(i.enabled and i.kind == autostart.APP
                                            for i in relevant))
        self.restore_btn.setVisible(can_undo)
        self.xdg_banner.setVisible(not self._xdg_runs and any(i.kind == autostart.APP
                                                              for i in relevant))
        still_started = sum(1 for i in relevant if i.other_start and not i.enabled)
        self.other_banner.text.setText(
            trn("{n} program you switched off here is still started by a service of your "
                "desktop (marked below). Switching off its startup entry does not stop that "
                "service yet.",
                "{n} programs you switched off here are still started by services of your "
                "desktop (marked below). Switching off their startup entries does not stop "
                "those services yet.", still_started).format(n=still_started))
        self.other_banner.setVisible(still_started > 0)
        other = len(items) - len(relevant)
        self.show_other.setText(trn("Show {n} entry that only runs on other desktops",
                                    "Show {n} entries that only run on other desktops",
                                    other).format(n=other))
        self.show_other.setVisible(other > 0)
        self._render()

    def _render(self) -> None:
        # the list is rebuilt from scratch; keep the user where they were (e.g. after a toggle)
        scroll = self.list_area.verticalScrollBar()
        position = scroll.value()
        self.list_host.setUpdatesEnabled(False)
        self._clear(self.list_lay)
        shown = [i for i in self.items if not i.desktop_mismatch or self.show_other.isChecked()]
        if not shown:
            self.list_lay.addWidget(EmptyState("power", tr("No startup entries")))
        headers = {autostart.APP: tr("Applications"),
                   autostart.WM: tr("Window manager start commands"),
                   autostart.SERVICE: tr("Background services")}
        last_kind = None
        for item in sorted(shown, key=lambda i: i.desktop_mismatch):
            if item.kind != last_kind and not item.desktop_mismatch:
                header = label(headers[item.kind], "muted")
                header.setContentsMargins(4, 10 if last_kind else 0, 0, 2)
                self.list_lay.addWidget(header)
                last_kind = item.kind
            elif item.desktop_mismatch and last_kind != "other":
                header = label(tr("Only on other desktops"), "muted")
                header.setContentsMargins(4, 10, 0, 2)
                self.list_lay.addWidget(header)
                last_kind = "other"
            self.list_lay.addWidget(AutostartRow(item, self))
        self.list_lay.addStretch()
        self.list_host.setUpdatesEnabled(True)
        QTimer.singleShot(0, lambda: scroll.setValue(min(position, scroll.maximum())))

    def _show_boot(self, report) -> None:
        self._clear(self.blame_lay)
        if not report:
            self.boot_total.setText("—")
            self.boot_detail.setText(tr("Could not run systemd-analyze."))
            return
        self.boot_total.setText(tr("{s:.1f} s").format(s=report.total_seconds)
                                  if report.total_seconds else "—")
        self.boot_detail.setText(report.summary)
        top = report.blame[:10]
        longest = max((s for _, s in top), default=1) or 1
        for unit, secs in top:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            h = QHBoxLayout()
            h.addWidget(label(unit, "small"))
            h.addStretch()
            h.addWidget(label(tr("{s:.2f} s").format(s=secs), "small"))
            v.addLayout(h)
            bar = UsageBar(secs / longest * 100)
            bar.setFixedHeight(5)
            if secs >= 5:
                bar.setProperty("level", "danger")
            elif secs >= 2:
                bar.setProperty("level", "warning")
            else:
                bar.setProperty("level", "")
            bar.style().polish(bar)
            v.addWidget(bar)
            self.blame_lay.addWidget(w)

    def toggle(self, item, on: bool, switch: ToggleSwitch) -> None:
        try:
            autostart.set_enabled(item, on)
        except (OSError, ValueError) as exc:
            switch.blockSignals(True)
            switch.setChecked(not on)
            switch.blockSignals(False)
            self.notify(tr("Could not change it: {exc}").format(exc=exc), "error")
            return
        self.notify((tr("{name} will now run at login.") if on
                     else tr("{name} will no longer run at login.")).format(name=item.name), "ok")
        self.refresh()

    def disable_all(self) -> None:
        targets = [i for i in self.items
                   if i.kind == autostart.APP and i.enabled and not i.desktop_mismatch]
        if not targets:
            return
        names = ", ".join(i.name for i in targets[:8]) + (" …" if len(targets) > 8 else "")
        if not confirm(self, tr("Disable all startup programs"),
                       trn("{n} program will no longer start at login.",
                           "{n} programs will no longer start at login.",
                           len(targets)).format(n=len(targets)),
                       informative=names + "\n\n" + tr(
                           "Some of them (input methods, network or volume applets) may be "
                           "needed by your desktop. “Undo” turns them back on."),
                       ok_text=tr("Disable all")):
            return
        try:
            changed = autostart.disable_all(targets)
        except OSError as exc:
            self.notify(tr("Could not change it: {exc}").format(exc=exc), "error")
            return
        self.notify(trn("{n} program disabled.", "{n} programs disabled.",
                        len(changed)).format(n=len(changed)), "ok")
        self.refresh()

    def restore(self) -> None:
        n = autostart.restore_disabled()
        self.notify(trn("{n} program turned back on.", "{n} programs turned back on.",
                        n).format(n=n), "ok")
        self.refresh()

    def add(self) -> None:
        dlg = AddDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            autostart.add_item(dlg.name.text(), dlg.command.text(), dlg.comment.text(),
                               dlg.delay.value())
        except (ValueError, OSError) as exc:
            self.notify(str(exc), "error")
            return
        self.notify(tr("Added to startup."), "ok")
        self.refresh()

    def remove(self, item) -> None:
        if not confirm(self, tr("Delete entry"),
                       tr("The startup entry '{name}' will be moved to the Trash.").format(
                           name=item.name), ok_text=tr("Delete"), danger=True):
            return
        try:
            autostart.remove_item(item)
        except OSError as exc:
            self.notify(str(exc), "error")
            return
        self.refresh()
