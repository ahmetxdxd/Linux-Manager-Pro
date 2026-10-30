"""External Disk page: move apps (program files + data) and large folders to another disk.

One disk is chosen at the top; everything on the page moves to that disk. An app is moved in
one step (whatever it consists of: pacman folders, a Flatpak, data folders such as AI
models), and it keeps starting from the menu like before. The disk can be set to mount
automatically, so moved apps also start after a reboot without opening the disk first.
"""

from __future__ import annotations

import os
from html import escape

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                             QHeaderView, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from lmp.core import automount, knowledge, moves, relocate
from lmp.core.apps import AppEntry
from lmp.core.privileged import helper_cmd
from lmp.core.util import HOME, dir_size, format_size, tilde, untilde
from lmp.i18n import tr, tr_noop, trn
from lmp.ui import icons, theme
from lmp.ui.background import run_async
from lmp.ui.models import OBJECT_ROLE, Column, ObjectTableModel, make_table
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import (Banner, Card, Page, SearchField, button, confirm, label,
                            tool_button)

MIN_USER_DIR_SIZE = 50 * 1024**2    # personal folders worth suggesting
FILTERS = [("all", tr_noop("All apps")), ("moved", tr_noop("On an external disk"))]
STATUS_TEXT = {moves.SYSTEM: tr_noop("System disk"), moves.EXTERNAL: tr_noop("External disk"),
               moves.PARTIAL: tr_noop("Partly moved"),
               moves.DISCONNECTED: tr_noop("Disk not connected")}


def _move_data(path: str, mountpoint: str):
    def work(progress):
        item = relocate.move_user_dir(path, mountpoint, progress)
        return tr("Moved: {path}").format(path=item.stored)
    return work


def _restore_data(item: relocate.UserRelocation):
    def work(progress):
        relocate.restore_user_dir(item, progress)
        return tr("Moved back.")
    return work


class MoveDialog(QDialog):
    """One confirmation for the whole move, with the automount choice."""

    def __init__(self, title: str, rows: list[tuple[str, int]], target: moves.Target,
                 note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(540)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        total = sum(size for _, size in rows)
        lay.addWidget(label(title, "h2"))
        lay.addWidget(label(tr("{size} will be moved to {disk} ({mount}).").format(
            size=format_size(total), disk=target.drive.title, mount=target.mountpoint),
            "muted", wrap=True))
        listing = label("\n".join(f"•  {text}  ({format_size(size)})" for text, size in rows),
                        wrap=True)
        listing.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(listing)
        self.enough = target.drive.free >= total + relocate.SPACE_MARGIN
        if not self.enough:
            lay.addWidget(Banner(tr("Not enough free space on the target disk ({free} free).")
                                 .format(free=format_size(target.drive.free)), "warning"))
        lay.addWidget(label(tr("Everything keeps its old location as a link, so the app starts "
                               "from the menu exactly as before. Close the app before moving "
                               "it.") + ("\n\n" + note if note else ""), "small", wrap=True))
        self.auto = QCheckBox(tr("Mount this disk automatically (recommended)"))
        self.auto.setToolTip(tr("Adds an entry to /etc/fstab: the disk is mounted as soon as a "
                                "moved app needs it, also after a reboot. Booting never waits "
                                "for the disk if it is unplugged."))
        self.auto.setChecked(True)
        self.auto.setVisible(target.can_automount)
        lay.addWidget(self.auto)
        if not target.mounts_by_itself and not target.can_automount:
            lay.addWidget(Banner(tr("This disk is not mounted automatically. Moved apps only "
                                    "start while it is mounted."), "warning"))
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button(tr("Cancel"), on_click=self.reject))
        ok = button(tr("Move"), "move", "primary", on_click=self.accept)
        ok.setEnabled(self.enough)
        row.addWidget(ok)
        lay.addLayout(row)

    @property
    def automount(self) -> bool:
        return self.auto.isVisible() and self.auto.isChecked()


# --------------------------------------------------------------------------- #
class AppsTab(QWidget):
    def __init__(self, page: "ExternalPage"):
        super().__init__()
        self.page = page
        self.plan: moves.AppPlan | None = None
        self._statuses: dict[str, tuple[str, str]] = {}
        self._sorted = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        self.search = SearchField(tr("Search apps…"))
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 2)
        self.filter = QComboBox()
        for key, text in FILTERS:
            self.filter.addItem(tr(text), key)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.filter)
        bar.addStretch(1)
        lay.addLayout(bar)

        p = theme.current()
        colors = {moves.EXTERNAL: p.success, moves.PARTIAL: p.warning,
                  moves.DISCONNECTED: p.danger}
        self.model = ObjectTableModel([
            Column(tr("Application"), lambda a: a.name, icon=lambda a: icons.app_icon(a.icon),
                   tooltip=lambda a: a.ident),
            Column(tr("Size"), lambda a: format_size(a.size) if a.size else "—",
                   sort_key=lambda a: a.size, align_right=True),
            Column(tr("Location"), lambda a: tr(STATUS_TEXT[self._status(a)[0]]),
                   color=lambda a: colors.get(self._status(a)[0]),
                   tooltip=lambda a: self._status(a)[1]),
        ])
        self.table, self.proxy = make_table(self.model)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)

        panel = Card()
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(420)
        self.p_name = label(tr("Select an application"), "h2", wrap=True)
        self.p_name.setTextFormat(Qt.TextFormat.PlainText)
        self.p_status = label("", "muted", wrap=True)
        self.p_status.setTextFormat(Qt.TextFormat.PlainText)
        panel.body.addWidget(self.p_name)
        panel.body.addWidget(self.p_status)
        self.parts_model = ObjectTableModel([
            Column(tr("What"), lambda part: part.label, tooltip=lambda part: part.path),
            Column(tr("Size"), lambda part: format_size(part.size), align_right=True,
                   sort_key=lambda part: part.size),
        ], checkable=True)
        self.parts_model.key = lambda part: f"{part.kind}:{part.path}"
        self.parts_model.on_check_changed = self._update_buttons
        self.parts_table, _ = make_table(self.parts_model)
        self.parts_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.parts_table.setSortingEnabled(False)
        panel.body.addWidget(self.parts_table, 1)
        self.p_note = label("", "small", wrap=True)
        panel.body.addWidget(self.p_note)
        row = QHBoxLayout()
        self.restore_btn = button(tr("Move back"), "undo", on_click=self.restore)
        self.move_btn = button(tr("Move to the external disk"), "move", "primary",
                               on_click=self.move)
        row.addWidget(self.restore_btn)
        row.addStretch()
        row.addWidget(self.move_btn)
        panel.body.addLayout(row)

        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(panel)
        split.setStretchFactor(0, 1)
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        self._show_plan(None)

    # ------------------------------------------------------------------ #
    def _status(self, app: AppEntry) -> tuple[str, str]:
        # cached: the table asks on every repaint, the real check touches the disk
        status = self._statuses.get(app.key)
        if status is None:
            status = self._statuses[app.key] = self.page.state.app_status(app)
        return status

    def set_apps(self, apps: list[AppEntry]) -> None:
        state = self.page.state

        def relevant(a: AppEntry) -> bool:
            if a.has_gui or a.source == "flatpak" or a.ident in state.registry:
                return True
            d = knowledge.for_package(a.ident)
            return bool(d and d.data)

        current = self._current()
        self._statuses = {}
        self.model.set_rows([a for a in apps if relevant(a)])
        if not self._sorted and apps:
            self._sorted = True          # biggest first: those are worth moving
            self.table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self._apply_filter()
        if current is not None:
            for r in range(self.proxy.rowCount()):
                if self.proxy.index(r, 0).data(OBJECT_ROLE).key == current.key:
                    self.table.selectRow(r)
                    return
        self._show_plan(None)

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        only_moved = self.filter.currentData() == "moved"

        def pred(a: AppEntry) -> bool:
            if text and text not in a.name.lower() and text not in a.ident.lower():
                return False
            return not only_moved or self._status(a)[0] != moves.SYSTEM

        self.proxy.set_predicate(pred)

    def _current(self) -> AppEntry | None:
        idx = self.table.currentIndex()
        return idx.data(OBJECT_ROLE) if idx.isValid() else None

    def _row_changed(self, current, _prev) -> None:
        app = current.data(OBJECT_ROLE) if current.isValid() else None
        self._show_plan(None, app)
        if app is None:
            return
        state = self.page.state
        run_async(moves.plan_app, app, state, owner=self, key=(id(self), "plan"),
                  on_done=self._show_plan,
                  on_error=lambda e: self.p_note.setText(tr("Error: {e}").format(e=e)))

    def _show_plan(self, plan: moves.AppPlan | None, loading: AppEntry | None = None) -> None:
        self.plan = plan
        app = plan.app if plan else loading
        self.p_name.setText(app.name if app else tr("Select an application"))
        if plan is None:
            self.p_status.setText(tr("Analysing…") if loading else "")
            self.parts_model.set_rows([])
            self.p_note.setText("" if loading else tr(
                "Pick an app to see what it consists of. Its program files and its data "
                "(e.g. AI models, game libraries) move together."))
            self._update_buttons()
            return
        moved = plan.moved
        if moved:
            lines = [tr("On the external disk:")]
            for part in moved:
                state = "" if part.connected else "  " + tr("(disk not connected)")
                lines.append(f"• {part.label} ({format_size(part.size)}){state}")
            self.p_status.setText("\n".join(lines))
        else:
            self.p_status.setText(tr("Everything is on the system disk."))
        self.parts_model.set_rows(plan.movable)
        self.parts_model.set_checked(plan.movable)
        self.p_note.setText(plan.note)
        self._update_buttons()

    def _update_buttons(self) -> None:
        plan = self.plan
        target = self.page.current_target()
        selected = self.parts_model.checked_objects()
        self.move_btn.setEnabled(bool(plan and selected and target))
        self.restore_btn.setVisible(bool(plan and plan.moved))
        if plan and selected:
            self.move_btn.setText(tr("Move to the external disk ({size})").format(
                size=format_size(sum(p.size for p in selected))))
        else:
            self.move_btn.setText(tr("Move to the external disk"))

    # ------------------------------------------------------------------ #
    def move(self) -> None:
        plan, target = self.plan, self.page.current_target()
        parts = self.parts_model.checked_objects()
        if not plan or not parts or not target:
            return
        app = plan.app
        program = [p.path for p in parts if p.kind == moves.PROGRAM]
        flat = any(p.kind == moves.FLATPAK for p in parts)
        data = [p.path for p in parts if p.kind == moves.DATA]
        note = ""
        if flat:
            note = tr("Flatpak apps are reinstalled into a Flatpak installation on the disk; "
                      "this downloads the app once more.")
        dlg = MoveDialog(tr("Move {name}").format(name=app.name),
                         [(p.label, p.size) for p in parts], target, note, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        steps = self.page.move_steps(target, dlg.automount, package=app.ident,
                                     program_dirs=program, flatpak_app=app if flat else None,
                                     data=data)
        ok = TaskDialog(tr("Moving {name}").format(name=app.name), steps, self,
                        success_text=tr("{name} is now on the external disk. It starts from "
                                        "the menu as before.").format(name=app.name)).run()
        self.page.after_change(app_changed=flat or ok)

    def restore(self) -> None:
        plan = self.plan
        if not plan or not plan.moved:
            return
        if not all(p.connected for p in plan.moved):
            self.page.notify(tr("Connect the disk first."), "error")
            return
        app = plan.app
        if not confirm(self, tr("Move back"),
                       tr("{name} ({size}) will be moved back to the system disk.").format(
                           name=app.name, size=format_size(sum(p.size for p in plan.moved))),
                       ok_text=tr("Move back")):
            return
        steps = self.page.restore_steps(plan)
        TaskDialog(tr("Moving {name} back").format(name=app.name), steps, self,
                   success_text=tr("{name} is back on the system disk.").format(
                       name=app.name)).run()
        self.page.after_change(app_changed=True)


# --------------------------------------------------------------------------- #
class FoldersTab(QWidget):
    def __init__(self, page: "ExternalPage"):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(Banner(tr(
            "Move any large folder, e.g. AI models (~/.ollama, ~/.cache/huggingface), game "
            "libraries or virtual machines. A link stays in its place, so programs keep "
            "finding it. No administrator password needed.")))

        moved = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("Moved folders"), "h2"))
        head.addStretch()
        head.addWidget(button(tr("Move back"), "undo", on_click=self.restore))
        moved.body.addLayout(head)
        p = theme.current()
        self.moved_model = ObjectTableModel([
            Column(tr("Folder"), lambda r: tilde(r.path)),
            Column(tr("Disk"), lambda r: r.target_mount),
            Column(tr("Size"), lambda r: format_size(r.size), sort_key=lambda r: r.size,
                   align_right=True),
            Column(tr("Status"), lambda r: tr("Mounted") if r.connected else tr("Disk not mounted"),
                   color=lambda r: p.success if r.connected else p.danger),
        ])
        self.moved_table, _ = make_table(self.moved_model)
        self.moved_table.setMaximumHeight(170)
        self.moved_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        moved.body.addWidget(self.moved_table)
        lay.addWidget(moved)

        sugg = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("Large folders"), "h2"))
        head.addStretch()
        head.addWidget(button(tr("Pick another folder…"), "folder", on_click=self.pick))
        sugg.body.addLayout(head)
        self.sugg_model = ObjectTableModel([
            Column(tr("Folder"), lambda s: tilde(s[0])),
            Column(tr("Application"), lambda s: s[1]),
            Column(tr("Size"), lambda s: format_size(s[2]), sort_key=lambda s: s[2],
                   align_right=True),
        ])
        self.sugg_table, _ = make_table(self.sugg_model)
        self.sugg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        sugg.body.addWidget(self.sugg_table, 1)
        row = QHBoxLayout()
        self.info = label("", "muted")
        row.addWidget(self.info, 1)
        row.addWidget(button(tr("Move selected"), "move", "primary", on_click=self.move_selected))
        sugg.body.addLayout(row)
        lay.addWidget(sugg, 1)

    def load(self) -> None:
        self.info.setText(tr("Calculating folder sizes…"))
        self.moved_model.set_rows(self.page.state.user_moves)

        def work():
            moved_paths = {m.path for m in relocate.load_user_relocations()}
            seen, out = set(), []
            for d in knowledge.definitions():
                for raw in d.data:
                    path = str(untilde(raw))
                    if (not path.startswith(str(HOME) + "/") or path in seen
                            or path in moved_paths or os.path.islink(path)
                            or not os.path.isdir(path)):
                        continue
                    seen.add(path)
                    out.append((path, d.name, dir_size(path)))
            out = [o for o in out if o[2] > MIN_USER_DIR_SIZE]
            return sorted(out, key=lambda o: -o[2])

        run_async(work, on_done=self._loaded, key=(id(self), "load"),
                  on_error=lambda e: self.info.setText(tr("Error: {e}").format(e=e)))

    def _loaded(self, sugg) -> None:
        self.sugg_model.set_rows(sugg)
        self.sugg_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        self.info.setText(
            trn("{n} large application folder found.", "{n} large application folders found.",
                len(sugg)).format(n=len(sugg)) if sugg else
            tr("No known large application folders; add one with 'Pick another folder'."))

    def pick(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Folder to move"), str(HOME),
                                                QFileDialog.Option.DontResolveSymlinks)
        if path:
            self._move(path, dir_size(path) if os.path.isdir(path) else 0)

    def move_selected(self) -> None:
        idx = self.sugg_table.currentIndex()
        item = idx.data(OBJECT_ROLE) if idx.isValid() else None
        if item:
            self._move(item[0], item[2])
        else:
            self.page.notify(tr("Select a folder from the list."), "warning")

    def _move(self, path: str, size: int) -> None:
        target = self.page.current_target()
        if not target:
            self.page.notify(tr("Attach an ext4/btrfs/xfs formatted external disk."), "warning")
            return
        if not path.startswith(str(HOME) + "/"):
            self.page.notify(tr("Only folders inside your home directory can be moved."), "warning")
            return
        dlg = MoveDialog(tr("Move folder"), [(tilde(path), size)], target,
                         tr("Close applications that use this folder until the move "
                            "finishes."), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        steps = self.page.move_steps(target, dlg.automount, data=[path])
        TaskDialog(tr("Moving folder"), steps, self,
                   success_text=tr("Folder moved to the external disk.")).run()
        self.page.after_change()

    def restore(self) -> None:
        idx = self.moved_table.currentIndex()
        item = idx.data(OBJECT_ROLE) if idx.isValid() else None
        if not item:
            self.page.notify(tr("Select a folder from the list."), "warning")
            return
        if not item.connected:
            self.page.notify(tr("Connect the disk first."), "error")
            return
        TaskDialog(tr("Moving folder back"),
                   [Step(tilde(item.path), func=_restore_data(item))], self,
                   success_text=tr("Folder moved back to the home directory.")).run()
        self.page.after_change()


# --------------------------------------------------------------------------- #
class CopyTab(QWidget):
    """TeraCopy-style copy: every file is read back and compared byte by byte."""

    def __init__(self, page: "ExternalPage"):
        super().__init__()
        self.page = page
        self.sources: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(Banner(tr(
            "Copies files and folders anywhere, then reads every copied file back and compares "
            "it with the original byte by byte. Use it for backups and anything you cannot "
            "afford to lose: a damaged copy is reported instead of going unnoticed.")))
        card = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("What to copy"), "h2"))
        head.addStretch()
        head.addWidget(button(tr("Add files…"), "file", on_click=self.add_files))
        head.addWidget(button(tr("Add folder…"), "folder", on_click=self.add_folder))
        head.addWidget(button(tr("Clear"), on_click=self.clear))
        card.body.addLayout(head)
        self.src_model = ObjectTableModel([
            Column(tr("Location"), lambda s: tilde(s[0])),
            Column(tr("Size"), lambda s: format_size(s[1]), sort_key=lambda s: s[1],
                   align_right=True),
        ])
        self.src_table, _ = make_table(self.src_model)
        self.src_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        card.body.addWidget(self.src_table, 1)
        row = QHBoxLayout()
        self.dest_lbl = label(tr("Destination: not chosen"), "muted", wrap=True)
        row.addWidget(self.dest_lbl, 1)
        row.addWidget(button(tr("Choose destination…"), "folder", on_click=self.choose_dest))
        self.copy_btn = button(tr("Copy and verify"), "check", "primary", on_click=self.copy)
        row.addWidget(self.copy_btn)
        card.body.addLayout(row)
        lay.addWidget(card, 1)
        self.dest = ""
        self._update()

    def _add(self, paths: list[str]) -> None:
        for p in paths:
            if p and p not in self.sources:
                self.sources.append(p)
        self.src_model.set_rows([(p, dir_size(p)) for p in self.sources])
        self._update()

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add files…"), str(HOME))
        self._add(paths)

    def add_folder(self) -> None:
        self._add([QFileDialog.getExistingDirectory(self, tr("Add folder…"), str(HOME))])

    def clear(self) -> None:
        self.sources = []
        self.src_model.set_rows([])
        self._update()

    def choose_dest(self) -> None:
        t = self.page.current_target()
        path = QFileDialog.getExistingDirectory(self, tr("Choose destination…"),
                                                t.mountpoint if t else str(HOME))
        if path:
            self.dest = path
            self.dest_lbl.setText(tr("Destination: {path}").format(path=tilde(path)))
        self._update()

    def _update(self) -> None:
        self.copy_btn.setEnabled(bool(self.sources and self.dest))

    def copy(self) -> None:
        from lmp.core import verified_copy
        sources, dest = list(self.sources), self.dest
        clash = [p for p in sources if os.path.abspath(dest).startswith(os.path.abspath(p) + "/")
                 or os.path.abspath(dest) == os.path.abspath(p)]
        if clash:
            self.page.notify(tr("The destination cannot be inside what is being copied."), "error")
            return

        def work(progress):
            return verified_copy.copy_and_verify(sources, dest, progress)

        TaskDialog(tr("Copy and verify"), [Step(tr("Copying and verifying"), func=work)], self,
                   success_text=tr("Copied and verified: the copies are identical to the "
                                   "originals.")).run()


# --------------------------------------------------------------------------- #
class ExternalPage(Page):
    title = tr_noop("External Disk")
    subtitle = tr_noop("Move apps, games and AI models to another disk; they keep starting "
                       "from the menu as before")
    icon_name = "disk"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.state = moves.MoveState()
        self.targets: list[moves.Target] = []
        self.header_actions.addWidget(tool_button("refresh", tr("Refresh disks and lists"),
                                                  self.refresh))

        disk = Card()
        row = QHBoxLayout()
        row.addWidget(label(tr("Target disk"), "h2"))
        self.disk_combo = QComboBox()
        self.disk_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.disk_combo.setMinimumWidth(320)
        self.disk_combo.currentIndexChanged.connect(self._disk_changed)
        row.addWidget(self.disk_combo)
        row.addStretch()
        self.auto_btn = button(tr("Mount automatically"), "power", on_click=self.enable_automount,
                               tooltip=tr("Mount this disk at startup and whenever a moved app "
                                          "needs it (adds an entry to /etc/fstab)."))
        self.auto_off_btn = button(tr("Stop mounting automatically"), on_click=self.disable_automount)
        row.addWidget(self.auto_btn)
        row.addWidget(self.auto_off_btn)
        disk.body.addLayout(row)
        self.disk_status = label("", "small", wrap=True)
        disk.body.addWidget(self.disk_status)
        self.content.addWidget(disk)

        self.tabs = QTabWidget()
        self.apps_tab = AppsTab(self)
        self.folders_tab = FoldersTab(self)
        self.tabs.addTab(self.apps_tab, tr("Applications"))
        self.tabs.addTab(self.folders_tab, tr("Folders"))
        self.copy_tab = CopyTab(self)
        self.tabs.addTab(self.copy_tab, tr("Verified copy"))
        self.tabs.currentChanged.connect(self._tab_changed)
        self._folders_fresh = False
        self.content.addWidget(self.tabs, 1)
        ctx.apps_loaded.connect(self._apps_loaded)

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        self.disk_status.setText(tr("Looking for disks…"))

        def work():
            return moves.targets(), moves.MoveState.load()

        run_async(work, on_done=self._on_loaded, key=(id(self), "load"),
                  on_error=lambda e: self.disk_status.setText(tr("Error: {e}").format(e=e)))

    def _on_loaded(self, result) -> None:      # (Page uses `_loaded` as a flag)
        self.targets, self.state = result
        previous = self.current_target()
        keep = previous.mountpoint if previous else ""
        self.disk_combo.blockSignals(True)
        self.disk_combo.clear()
        for t in self.targets:
            d = t.drive
            self.disk_combo.addItem(tr("{title}  ·  {mount}  ·  {free} free").format(
                title=d.title, mount=d.mountpoint, free=format_size(d.free)), t)
        if not self.targets:
            self.disk_combo.addItem(tr("No suitable external disk"), None)
        index = next((i for i, t in enumerate(self.targets) if t.mountpoint == keep), 0)
        self.disk_combo.setCurrentIndex(index)
        self.disk_combo.blockSignals(False)
        self._disk_changed()
        self.apps_tab.set_apps(self.ctx.apps)
        self._folders_fresh = False
        self._tab_changed(self.tabs.currentIndex())

    def _apps_loaded(self, apps) -> None:
        self.apps_tab.set_apps(apps)

    def _tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.folders_tab and not self._folders_fresh:
            self._folders_fresh = True
            self.folders_tab.load()

    def current_target(self) -> moves.Target | None:
        return self.disk_combo.currentData() if self.disk_combo.count() else None

    def _disk_changed(self) -> None:
        t = self.current_target()
        self.auto_btn.setVisible(bool(t and t.can_automount))
        self.auto_off_btn.setVisible(bool(t and t.automount == automount.OURS))
        p = theme.current()
        if t is None:
            text = tr("Attach a disk formatted as ext4, btrfs or xfs. Disks formatted for "
                      "Windows (NTFS, exFAT) cannot hold Linux programs.")
            color = p.warning
        elif t.automount == automount.OURS:
            text = tr("✓ Mounted automatically: moved apps start from the menu, also after a "
                      "reboot. While the disk is unplugged they cannot start.")
            color = p.success
        elif t.automount == automount.FSTAB_ENTRY:
            text = tr("✓ This disk is mounted at startup by your /etc/fstab.")
            color = p.success
        else:
            text = tr("⚠ This disk is only mounted when you open it in the file manager. After "
                      "a reboot, apps moved to it will not start until it is mounted. Turn on "
                      "“Mount automatically”.")
            color = p.warning
        self.disk_status.setText(f'<span style="color:{color}">{escape(text)}</span>')
        self.apps_tab._update_buttons()

    def enable_automount(self) -> None:
        t = self.current_target()
        if not t:
            return
        TaskDialog(tr("Mount automatically"), [Step(
            tr("Adding the disk to /etc/fstab"), helper_cmd("automount-add", t.mountpoint),
            uses_pkexec=True)], self).run()
        self.refresh()

    def disable_automount(self) -> None:
        t = self.current_target()
        if not t:
            return
        TaskDialog(tr("Stop mounting automatically"), [Step(
            tr("Removing the disk from /etc/fstab"), helper_cmd("automount-remove", t.mountpoint),
            uses_pkexec=True)], self).run()
        self.refresh()

    # ------------------------------------------------------------------ #
    def move_steps(self, target: moves.Target, automount_on: bool, *, package: str = "",
                   program_dirs: list[str] | None = None, flatpak_app: AppEntry | None = None,
                   data: list[str] | None = None) -> list[Step]:
        """All steps to move the selected parts. Root work happens in a single helper call
        (one password prompt), user folders are moved without root."""
        program_dirs, data = program_dirs or [], data or []
        mount = target.mountpoint
        flags = (["--automount"] if automount_on else []) + \
                (["--user-folder"] if data and not target.user_folder_ready() else [])
        steps: list[Step] = []
        prepared = not flags
        if program_dirs:
            steps.append(Step(tr("Moving the program files"),
                              helper_cmd("relocate-move", package, mount, *flags, *program_dirs),
                              uses_pkexec=True))
            prepared = True
        if flatpak_app is not None and flatpak_app.flatpak_app:
            fa = flatpak_app.flatpak_app
            inst = moves.flatpak_installation_for(mount, self.state)
            if inst is None:
                d = target.drive
                steps.append(Step(tr("Creating a Flatpak installation on the disk"), helper_cmd(
                    "flatpak-installation-add", *([] if prepared else flags),
                    d.label or os.path.basename(mount) or "disk", os.path.join(mount, "flatpak"),
                    tr("External: {title}").format(title=d.title)), uses_pkexec=True))
                prepared = True
                ident = moves.new_installation_ident(d)
            else:
                ident = inst.ident
            steps.append(Step(tr("Installing {name} on the disk").format(name=flatpak_app.name),
                              ["flatpak", "install", "-y", "--noninteractive",
                               f"--installation={ident}", fa.origin or "flathub", fa.app_id]))
            steps.append(Step(tr("Removing {name} from the system disk").format(
                name=flatpak_app.name),
                ["flatpak", "uninstall", "-y", "--noninteractive", fa.install_flag, fa.app_id]))
        if not prepared:
            steps.insert(0, Step(tr("Preparing the disk"),
                                 helper_cmd("prepare-disk", mount, *flags), uses_pkexec=True))
        for path in data:
            steps.append(Step(tr("Moving {path}").format(path=tilde(path)),
                              func=_move_data(path, mount)))
        return steps

    def restore_steps(self, plan: moves.AppPlan) -> list[Step]:
        app, steps = plan.app, []
        if any(p.kind == moves.PROGRAM for p in plan.moved):
            steps.append(Step(tr("Moving the program files back"),
                              helper_cmd("relocate-restore", app.ident), uses_pkexec=True))
        if any(p.kind == moves.FLATPAK for p in plan.moved) and app.flatpak_app:
            fa = app.flatpak_app
            steps.append(Step(tr("Installing {name} on the system disk").format(name=app.name),
                              ["flatpak", "install", "-y", "--noninteractive", "--system",
                               fa.origin or "flathub", fa.app_id]))
            steps.append(Step(tr("Removing {name} from the external disk").format(name=app.name),
                              ["flatpak", "uninstall", "-y", "--noninteractive",
                               fa.install_flag, fa.app_id]))
        for part in plan.moved:
            if part.kind == moves.DATA:
                item = self.state.user_move(part.path)
                if item:
                    steps.append(Step(tr("Moving {path} back").format(path=tilde(part.path)),
                                      func=_restore_data(item)))
        return steps

    def after_change(self, app_changed: bool = False) -> None:
        if app_changed:
            self.ctx.reload_apps()
        self.refresh()
