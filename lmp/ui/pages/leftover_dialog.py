from __future__ import annotations

import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QVBoxLayout

from lmp.core import leftovers
from lmp.core.privileged import helper_cmd
from lmp.core.util import format_date, format_size, move_to_trash, remove_path, tilde
from lmp.i18n import tr
from lmp.ui import theme
from lmp.ui.background import run_async
from lmp.ui.models import Column, ObjectTableModel, make_table
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import Banner, button, confirm, label


class LeftoverDialog(QDialog):
    """Shows the leftovers found; moves the ones the user selects to the Trash or deletes them."""

    def __init__(self, items: list[leftovers.Leftover], parent=None, *, title: str = "",
                 orphan_mode: bool = False, delete_mode: str = "trash", info: str = "",
                 preselect: bool = True):
        super().__init__(parent)
        title = title or tr("Leftover files")
        self.setWindowTitle(title)
        self.resize(940, 580)
        self.items = items
        self.delete_mode = delete_mode
        self.removed = 0
        self.errors: list[str] = []
        self._busy = False
        p = theme.current()
        colors = {leftovers.EXACT: p.success, leftovers.PROBABLE: p.warning,
                  leftovers.LOW: p.muted}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label(title, "h2"))
        if orphan_mode:
            text = tr("These folders match no installed application; they most likely belong to "
                     "applications you removed earlier. The list is <b>a best guess</b>: folders "
                     "of programs you installed by hand (AppImages, scripts, etc.) may show up "
                     "too. Nothing is selected by default; only select what you recognise.")
            lay.addWidget(Banner(text, "warning"))
        else:
            text = (tr("<b>Exact</b> matches are selected by default. Review the <b>Probable</b> "
                       "ones before selecting them. Folders also used by another installed "
                       "application are not listed.") if preselect else
                    tr("Nothing is selected yet; tick what you want to delete. Folders also used "
                       "by another installed application are not listed."))
            if any(i.system for i in items):
                text += tr(" System locations ask for your administrator password and are deleted "
                           "<b>permanently</b>.")
            lay.addWidget(Banner(text))

        if info:
            lay.addWidget(Banner(info))

        columns = [
            Column(tr("Location"), lambda i: tilde(i.path), tooltip=lambda i: i.reason or str(i.path)),
            Column(tr("Type"), lambda i: i.kind),
            Column(tr("Confidence"), lambda i: i.confidence_label,
                   sort_key=lambda i: {"exact": 0, "probable": 1, "low": 2}[i.confidence],
                   color=lambda i: colors[i.confidence]),
            Column(tr("Size"), lambda i: format_size(i.size), sort_key=lambda i: i.size,
                   align_right=True),
        ]
        columns.append(Column(tr("Last modified"), lambda i: format_date(i.modified),
                              sort_key=lambda i: i.modified.timestamp() if i.modified else 0)
                       if orphan_mode else Column(tr("Application"), lambda i: i.app))
        self.model = ObjectTableModel(columns, checkable=True)
        self.model.key = lambda i: str(i.path)
        self.view, self.proxy = make_table(self.model)
        self.model.set_rows(items)
        self.model.set_checked([i for i in items if i.default_checked] if preselect else [])
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(columns)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.view.sortByColumn(2, Qt.SortOrder.AscendingOrder)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addWidget(button(tr("Select all"), on_click=lambda: self.model.set_checked(self.items)))
        row.addWidget(button(tr("Deselect all"),
                             on_click=lambda: self.model.set_checked(self.items, False)))
        row.addWidget(button(tr("Open location"), "folder", on_click=self._open_location))
        row.addStretch()
        self.summary = label("", "muted")
        row.addWidget(self.summary)
        lay.addLayout(row)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.close_btn = button(tr("Close"), on_click=self.reject)
        buttons.addWidget(self.close_btn)
        verb = tr("Move to Trash") if delete_mode == "trash" else tr("Delete permanently")
        self.delete_btn = button(verb, "uninstall", "danger", on_click=self._delete)
        buttons.addWidget(self.delete_btn)
        lay.addLayout(buttons)
        self.model.on_check_changed = self._update_summary
        self._update_summary()

    def _update_summary(self) -> None:
        sel = self.model.checked_objects()
        total = sum(i.size for i in sel)
        self.summary.setText(tr("{n} / {total} selected · {size}").format(
            n=len(sel), total=len(self.items), size=format_size(total)))
        self.delete_btn.setEnabled(bool(sel) and not self._busy)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.close_btn.setEnabled(not busy)
        self.view.setEnabled(not busy)
        self._update_summary()

    def reject(self) -> None:
        # closing in the middle of a deletion would lose its result and pop up the password
        # dialog later out of nowhere
        if not self._busy:
            super().reject()

    def _open_location(self) -> None:
        idx = self.view.currentIndex()
        if not idx.isValid():
            return
        item = self.proxy.index(idx.row(), 0).data(Qt.ItemDataRole.UserRole + 2)
        target = item.path if item.path.is_dir() else item.path.parent
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def _delete(self) -> None:
        selected = self.model.checked_objects()
        user_items = [i for i in selected if not i.system]
        system_items = [i for i in selected if i.system]
        size = format_size(sum(i.size for i in selected))
        where = (tr("will be moved to the Trash") if self.delete_mode == "trash"
                 else tr("will be deleted permanently"))
        msg = tr("{n} user item(s) ({size}) {where}.").format(n=len(user_items), size=size,
                                                              where=where)
        if system_items:
            msg += "\n" + tr("{n} system item(s) will be deleted permanently (password "
                           "required).").format(n=len(system_items))
        if not confirm(self, tr("Clean up leftovers"), msg, ok_text=tr("Clean"),
                       danger=self.delete_mode != "trash" or bool(system_items)):
            return
        self._set_busy(True)
        mode = self.delete_mode

        def work(progress):
            errors = []
            for n, item in enumerate(user_items, 1):
                progress(int(n * 100 / max(1, len(user_items))), tilde(item.path))
                try:
                    if mode == "trash":
                        move_to_trash(item.path)
                    else:
                        remove_path(item.path)
                except OSError as exc:
                    errors.append(f"{tilde(item.path)}: {exc}")
            return errors

        def after_user(errors):
            self.errors = list(errors)
            removed = len(user_items) - len(errors)
            if system_items:
                ok = TaskDialog(tr("Deleting system leftovers"),
                                [Step(tr("Deleting system locations"),
                                      helper_cmd("delete-paths", *[str(i.path) for i in system_items]),
                                      uses_pkexec=True)],
                                self, success_text=tr("System leftovers deleted.")).run()
                if ok:
                    removed += len(system_items)
                else:
                    # the helper reports per path, but a failed or cancelled run means we
                    # cannot vouch for any of them; count only what still is gone
                    gone = [i for i in system_items if not i.path.exists()
                            and not i.path.is_symlink()]
                    removed += len(gone)
                    if len(gone) < len(system_items):
                        self.errors.append(tr("System locations were not deleted (authorization "
                                              "cancelled or failed)."))
            self.removed = removed
            self._busy = False
            self.accept()

        def failed(message):
            self._set_busy(False)
            self.errors = [message]
            self.summary.setText(tr("Deletion failed: {e}").format(e=message))

        if user_items:
            run_async(work, on_done=after_user, on_error=failed, owner=self,
                      on_progress=lambda pct, text: self.summary.setText(
                          tr("{percent}% · {item}").format(percent=pct, item=text)))
        else:
            after_user([])
