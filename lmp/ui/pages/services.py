from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QPlainTextEdit,
                             QSplitter, QTabBar, QVBoxLayout)

from lmp.core import systemd
from lmp.i18n import tr, tr_noop
from lmp.ui import theme
from lmp.ui.background import run_async
from lmp.ui.models import OBJECT_ROLE, Column, ObjectTableModel, make_table
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import Card, Page, SearchField, button, confirm, label, tool_button

FILTERS = [("all", tr_noop("All")), ("running", tr_noop("Running services")),
           ("enabled", tr_noop("Enabled at boot")), ("failed", tr_noop("Failed services")),
           ("disabled", tr_noop("Disabled"))]


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.addWidget(label(title, "h2"))
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setProperty("role", "log")
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(view, 1)
        view.moveCursor(view.textCursor().MoveOperation.End)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button(tr("Close"), role="primary", on_click=self.accept))
        lay.addLayout(row)


class ServicesPage(Page):
    title = tr_noop("Services")
    subtitle = tr_noop("Start, stop and manage systemd services and their boot behaviour")
    icon_name = "services"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.user_scope = False
        self.header_actions.addWidget(tool_button("refresh", tr("Refresh"), self.refresh))

        self.scope = QTabBar()
        self.scope.addTab(tr("System services"))
        self.scope.addTab(tr("User services"))
        self.scope.setExpanding(False)
        self.scope.currentChanged.connect(self._scope_changed)
        self.content.addWidget(self.scope)

        bar = QHBoxLayout()
        self.search = SearchField(tr("Search service name or description…"))
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 2)
        self.filter = QComboBox()
        for key, text in FILTERS:
            self.filter.addItem(tr(text), key)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.filter)
        bar.addStretch(1)
        self.count = label("", "muted")
        bar.addWidget(self.count)
        self.content.addLayout(bar)

        p = theme.current()
        active_colors = {"active": p.success, "failed": p.danger, "activating": p.warning}
        state_colors = {"enabled": p.accent, "masked": p.danger}
        columns = [
            Column(tr("Service"), lambda u: u.name[:-8] if u.name.endswith(".service") else u.name,
                   tooltip=lambda u: u.name),
            Column(tr("Description"), lambda u: u.description),
            Column(tr("Status"), lambda u: u.active_label + (f" ({u.sub})" if u.sub and
                                                        u.sub not in ("running", "dead") else ""),
                   color=lambda u: active_colors.get(u.active)),
            Column(tr("At boot"), lambda u: u.file_state_label,
                   color=lambda u: state_colors.get(u.file_state)),
        ]
        self.model = ObjectTableModel(columns)
        self.table, self.proxy = make_table(self.model)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)

        self.panel = Card()
        self.panel.setMinimumWidth(260)
        self.panel.setMaximumWidth(320)
        self.p_name = label(tr("Select a service"), "h2", wrap=True)
        self.p_desc = label("", "muted", wrap=True)
        self.p_state = label("", wrap=True)
        self.panel.body.addWidget(self.p_name)
        self.panel.body.addWidget(self.p_desc)
        self.panel.body.addWidget(self.p_state)
        self.panel.body.addSpacing(6)
        self.action_buttons = {}
        for action, icon_name in (("start", "play"), ("stop", "stop"), ("restart", "restart"),
                                  ("enable", "power"), ("disable", "close"),
                                  ("mask", "warning"), ("unmask", "undo")):
            b = button(tr(systemd.ACTION_LABELS[action]), icon_name,
                       on_click=lambda _=False, a=action: self.run_action(a))
            self.action_buttons[action] = b
            self.panel.body.addWidget(b)
        self.panel.body.addSpacing(6)
        self.log_btn = button(tr("Show journal"), "log", on_click=self.show_journal)
        self.file_btn = button(tr("Unit file"), "file", on_click=self.show_unit_file)
        self.panel.body.addWidget(self.log_btn)
        self.panel.body.addWidget(self.file_btn)
        self.panel.body.addStretch()
        self.panel.body.addWidget(label(tr("Changing system services requires your administrator password."),
                                        "small", wrap=True))

        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 1)
        split.setChildrenCollapsible(False)
        self.content.addWidget(split, 1)
        self._update_panel(None)

    def _scope_changed(self, index: int) -> None:
        self.user_scope = index == 1
        self.refresh()

    def refresh(self) -> None:
        self.count.setText(tr("Loading…"))
        current = self._current()
        keep = current.name if current else None
        # key: switching between the system and user tabs quickly must not let the slower,
        # older answer overwrite the list of the tab that is now shown
        run_async(systemd.list_units, self.user_scope, owner=self, key=(id(self), "units"),
                  on_done=lambda units: self._show(units, keep),
                  on_error=lambda e: self.notify(tr("Could not read services: {e}").format(e=e), "error"))

    def _show(self, units, keep=None) -> None:
        self.model.set_rows(units)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._apply_filter()
        if keep:
            for r in range(self.proxy.rowCount()):
                if self.proxy.index(r, 0).data(OBJECT_ROLE).name == keep:
                    self.table.selectRow(r)
                    break
        # the model reset dropped the old row; show the fresh object (or nothing), never the
        # stale one with buttons that would act on a unit that is no longer selected
        self._update_panel(self._current())

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter.currentData()

        def pred(u: systemd.Unit) -> bool:
            if text and text not in u.name.lower() and text not in u.description.lower():
                return False
            if mode == "running":
                return u.active == "active"
            if mode == "enabled":
                return u.file_state.startswith("enabled")
            if mode == "failed":
                return u.active == "failed"
            if mode == "disabled":
                return u.file_state == "disabled"
            return True

        self.proxy.set_predicate(pred)
        self.count.setText(tr("{n} services").format(n=self.proxy.rowCount()))
        self._update_panel(self._current())

    def _current(self) -> systemd.Unit | None:
        idx = self.table.currentIndex()
        return idx.data(OBJECT_ROLE) if idx.isValid() else None

    def _row_changed(self, current, _prev) -> None:
        self._update_panel(current.data(OBJECT_ROLE) if current.isValid() else None)

    def _update_panel(self, unit: systemd.Unit | None) -> None:
        for b in list(self.action_buttons.values()) + [self.log_btn, self.file_btn]:
            b.setEnabled(unit is not None)
        if unit is None:
            self.p_name.setText(tr("Select a service"))
            self.p_desc.setText("")
            self.p_state.setText("")
            return
        self.p_name.setText(unit.name)
        self.p_desc.setText(unit.description)
        self.p_state.setText(
            tr("Status: {state}").format(state=unit.active_label)
            + (f" ({unit.sub})" if unit.sub else "")
            + "\n" + tr("At boot: {state}").format(state=unit.file_state_label))
        masked = unit.file_state.startswith("masked")
        running = unit.active in ("active", "activating", "reloading")
        self.action_buttons["start"].setEnabled(not running and not masked)
        self.action_buttons["stop"].setEnabled(running)
        self.action_buttons["restart"].setEnabled(running)
        self.action_buttons["enable"].setEnabled(unit.file_state in ("disabled", "linked"))
        self.action_buttons["disable"].setEnabled(unit.file_state.startswith("enabled"))
        self.action_buttons["mask"].setEnabled(not masked)
        self.action_buttons["unmask"].setEnabled(masked)

    def run_action(self, action: str) -> None:
        unit = self._current()
        if not unit:
            return
        if action in ("stop", "mask", "disable") and not unit.user:
            critical = ("dbus", "systemd-", "NetworkManager", "sddm", "gdm", "polkit",
                        "display-manager", "udisks2", "lightdm")
            if any(unit.name.startswith(c) for c in critical):
                if not confirm(self, tr("Critical service"),
                               tr("{name} may be required for your desktop or the system to "
                                  "work. Continue anyway?").format(name=unit.name),
                               ok_text=tr("Continue"), danger=True):
                    return
        try:
            cmd = systemd.action_cmd(unit, action)
        except ValueError as exc:
            self.notify(str(exc), "error")
            return
        label_text = tr(systemd.ACTION_LABELS[action])
        done = tr("{unit}: {action} completed.").format(unit=unit.name, action=label_text)
        ok = TaskDialog(f"{unit.name}: {label_text}", [Step(label_text, cmd)], self,
                        success_text=done).run()
        if ok:
            self.notify(done, "ok")
        self.refresh()

    def show_journal(self) -> None:
        unit = self._current()
        if unit:
            run_async(systemd.journal, unit, owner=self,
                      on_error=lambda e: self.notify(str(e), "error"),
                      on_done=lambda text: TextDialog(tr("Journal: {name}").format(name=unit.name), text, self).exec())

    def show_unit_file(self) -> None:
        unit = self._current()
        if unit:
            run_async(systemd.unit_file, unit, owner=self,
                      on_error=lambda e: self.notify(str(e), "error"),
                      on_done=lambda text: TextDialog(tr("Unit file: {name}").format(name=unit.name),
                                                      text, self).exec())
