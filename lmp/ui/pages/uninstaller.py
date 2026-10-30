from __future__ import annotations

from html import escape

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView,
                             QLabel, QListWidget, QSplitter, QStackedWidget, QVBoxLayout)

from lmp.core import leftovers
from lmp.core.apps import AppEntry
from lmp.core.privileged import helper_cmd
from lmp.core.util import format_date, format_size, tilde
from lmp.i18n import tr, trn, tr_noop
from lmp.ui import icons, theme
from lmp.ui.background import run_async
from lmp.ui.models import OBJECT_ROLE, Column, ObjectTableModel, make_table
from lmp.ui.pages.leftover_dialog import LeftoverDialog
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import (Banner, Card, EmptyState, Page, SearchField, button, label,
                            tool_button)

VIEWS = [("apps", tr_noop("Apps")), ("explicit", tr_noop("Explicitly installed packages")),
         ("all", tr_noop("All packages (including dependencies)"))]
SOURCES = [("", tr_noop("All sources")), ("repo", tr_noop("Official repo")),
           ("aur", tr_noop("AUR / local")), ("flatpak", "Flatpak")]


class ConfirmUninstallDialog(QDialog):
    def __init__(self, apps: list[AppEntry], scan_default: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Confirm removal"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        total = sum(a.size for a in apps)
        lay.addWidget(label(trn("{n} app will be removed", "{n} apps will be removed",
                            len(apps)).format(n=len(apps)), "h2"))
        lay.addWidget(label(tr("{size} will be freed (not counting user data).").format(
            size=format_size(total)), "muted"))
        lst = QListWidget()
        for a in apps:
            lst.addItem(f"{a.name}  ·  {a.ident}  ·  {a.source_label}")
        lst.setMaximumHeight(170)
        lay.addWidget(lst)
        needed = [a for a in apps if a.required_by and a.source != "flatpak"]
        selected_names = {a.ident for a in apps}
        blockers = {a.ident: [r for r in a.required_by if r not in selected_names] for a in needed}
        blockers = {k: v for k, v in blockers.items() if v}
        if blockers:
            text = tr("<b>Warning:</b> other packages depend on some of these; removal may fail:<br>")
            text += "<br>".join(f"{k} ← {', '.join(v[:4])}{' …' if len(v) > 4 else ''}"
                                for k, v in list(blockers.items())[:5])
            lay.addWidget(Banner(text, "warning"))
        lay.addWidget(label(tr("pacman packages are removed together with their dependencies (-Rns). "
                               "Your user data is not deleted in this step."), "small", wrap=True))
        self.scan = QCheckBox(tr("Find leftovers after removal"))
        self.scan.setChecked(scan_default)
        lay.addWidget(self.scan)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button(tr("Cancel"), on_click=self.reject))
        row.addWidget(button(tr("Remove"), "uninstall", "danger", on_click=self.accept))
        lay.addLayout(row)


def _footprint_html(app: AppEntry, fp: leftovers.Footprint) -> str:
    """One paragraph describing what the package itself occupies."""
    parts = [f"<b>{escape(app.name)}</b>"]
    if app.source == "flatpak":
        if fp.location:
            parts.append(tr("Flatpak files: {size} in {path}").format(
                size=format_size(fp.size), path=escape(tilde(fp.location))))
    elif fp.files:
        parts.append(tr("{n} package files · {size}").format(n=fp.files, size=format_size(fp.size)))
        if fp.folders:
            parts.append(tr("Largest: ") + ", ".join(f"{escape(path)} ({format_size(size)})"
                                                    for path, size in fp.folders[:4]))
        if fp.services:
            parts.append(tr("Services: ") + ", ".join(fp.services[:5]))
        if fp.dependencies:
            parts.append(tr("Dependencies removed with it: ") + ", ".join(fp.dependencies[:8])
                         + (" …" if len(fp.dependencies) > 8 else ""))
    return " · ".join(parts)


class DetailsPanel(Card):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(270)
        self.setMaximumWidth(360)
        self.icon = QLabel()
        self.icon.setFixedSize(48, 48)
        self.name = label("", "h2", wrap=True)
        self.ident = label("", "small", wrap=True)
        self.ident.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc = label("", "muted", wrap=True)
        self.meta = label("", wrap=True)
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.meta.setOpenExternalLinks(True)
        self.deps = label("", "small", wrap=True)
        for w in (self.name, self.ident, self.desc, self.deps):
            w.setTextFormat(Qt.TextFormat.PlainText)     # package metadata is not markup
        for w in (self.icon, self.name, self.ident, self.desc, self.meta, self.deps):
            self.body.addWidget(w)
        self.body.addStretch()
        self.show_app(None)

    def show_app(self, app: AppEntry | None) -> None:
        if app is None:
            self.icon.clear()
            self.name.setText(tr("Select an application"))
            for w in (self.ident, self.desc, self.meta, self.deps):
                w.setText("")
            return
        self.icon.setPixmap(icons.app_icon(app.icon, size=48).pixmap(48, 48))
        self.name.setText(app.name)
        self.ident.setText(app.ident)
        self.desc.setText(app.description or "")
        p = theme.current()
        rows = [(tr("Source"), app.source_label), (tr("Version"), app.version or "—"),
                (tr("Size"), format_size(app.size) if app.size else "—"),
                (tr("Installed"), format_date(app.install_date))]
        if app.installation and app.source == "flatpak":
            rows.append((tr("Installation"), app.installation))
        if not app.explicit:
            rows.append((tr("Reason"), tr("Installed as a dependency")))
        html = "".join(f'<tr><td style="color:{p.muted};padding-right:12px">{escape(k)}</td>'
                       f"<td>{escape(str(v))}</td></tr>" for k, v in rows)
        if app.url:
            url = escape(app.url, quote=True)
            html += (f'<tr><td style="color:{p.muted}">{escape(tr("Web"))}</td><td><a style="color:{p.accent}" '
                     f'href="{url}">{escape(app.url[:40])}</a></td></tr>')
        self.meta.setText(f"<table>{html}</table>")
        if app.required_by:
            self.deps.setText(tr("Packages that depend on it: ") + ", ".join(app.required_by[:12]) +
                              (" …" if len(app.required_by) > 12 else ""))
        else:
            self.deps.setText("")


class UninstallerPage(Page):
    title = tr_noop("Uninstaller")
    subtitle = tr_noop("Remove applications completely, together with their leftovers")
    icon_name = "uninstall"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.header_actions.addWidget(button(tr("Scan orphaned leftovers"), "broom",
                                             on_click=self.scan_orphans,
                                             tooltip=tr("Find folders left behind by "
                                                        "applications you removed earlier")))
        self.header_actions.addWidget(tool_button("refresh", tr("Reload list"),
                                                  self.ctx.reload_apps))

        bar = QHBoxLayout()
        self.search = SearchField(tr("Search name, package name or description…"))
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 2)
        self.view_combo = QComboBox()
        for key, text in VIEWS:
            self.view_combo.addItem(tr(text), key)
        self.view_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.view_combo)
        self.source_combo = QComboBox()
        for key, text in SOURCES:
            self.source_combo.addItem(tr(text), key)
        self.source_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.source_combo)
        bar.addStretch(1)
        self.count_lbl = label("", "muted")
        bar.addWidget(self.count_lbl)
        self.content.addLayout(bar)

        p = theme.current()
        source_colors = {"repo": p.accent, "aur": p.warning, "flatpak": p.success}
        columns = [
            Column(tr("Name"), lambda a: a.name, icon=lambda a: icons.app_icon(a.icon),
                   tooltip=lambda a: a.ident),
            Column(tr("Source"), lambda a: a.source_label, color=lambda a: source_colors.get(a.source)),
            Column(tr("Version"), lambda a: a.version),
            Column(tr("Size"), lambda a: format_size(a.size) if a.size else "—",
                   sort_key=lambda a: a.size, align_right=True),
            Column(tr("Installed"), lambda a: format_date(a.install_date),
                   sort_key=lambda a: a.install_date.timestamp() if a.install_date else 0),
        ]
        self.model = ObjectTableModel(columns, checkable=True)
        self.model.key = lambda a: a.key
        self.model.on_check_changed = self._update_actionbar
        self.table, self.proxy = make_table(self.model)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.selectionModel().currentRowChanged.connect(self._row_changed)
        self.table.doubleClicked.connect(self._toggle_row)

        self.details = DetailsPanel()
        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(self.details)
        split.setStretchFactor(0, 1)
        split.setChildrenCollapsible(False)

        self.stack = QStackedWidget()
        self.loading = EmptyState("clock", tr("Reading applications…"),
                                  tr("Scanning the package database and the Flatpak list."))
        self.empty = EmptyState("search", tr("No results"), tr("Change the search or filters."))
        self.stack.addWidget(self.loading)
        self.stack.addWidget(split)
        self.stack.addWidget(self.empty)
        self.content.addWidget(self.stack, 1)

        actionbar = QFrame()
        actionbar.setProperty("role", "card")
        ab = QHBoxLayout(actionbar)
        ab.setContentsMargins(14, 10, 14, 10)
        self.sel_lbl = label(tr("Tick what you want to remove."), "muted")
        ab.addWidget(self.sel_lbl)
        ab.addStretch()
        self.scan_tick = QCheckBox(tr("Find leftovers after removal"))
        self.scan_tick.setToolTip(tr("After the app is removed, search the disk for its "
                                     "configuration, cache and data and list what was found."))
        self.scan_tick.setChecked(ctx.settings.scan_leftovers_after_uninstall)
        self.scan_tick.toggled.connect(self._scan_tick_changed)
        ab.addWidget(self.scan_tick)
        self.clear_btn = button(tr("Clear selection"), on_click=lambda: self.model.set_checked(
            self.model.rows, False))
        ab.addWidget(self.clear_btn)
        self.inspect_btn = button(tr("Scan leftovers"), "broom", on_click=self.inspect,
                                  tooltip=tr("List everything related to the selected app "
                                             "without removing it"))
        ab.addWidget(self.inspect_btn)
        self.remove_btn = button(tr("Remove"), "uninstall", "danger", on_click=self.uninstall)
        ab.addWidget(self.remove_btn)
        self.content.addWidget(actionbar)

        ctx.apps_loading.connect(self._on_loading)
        ctx.apps_loaded.connect(self._on_apps)
        ctx.apps_load_failed.connect(self._on_apps_failed)
        self._update_actionbar()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        if self.ctx.apps:
            self._on_apps(self.ctx.apps)
        else:
            self.ctx.reload_apps()

    def _on_loading(self) -> None:
        if not self.model.rows:
            self.stack.setCurrentIndex(0)

    def _on_apps_failed(self, message: str) -> None:
        self.notify(tr("Could not read the application list: {e}").format(e=message), "error")
        if not self.model.rows:
            self.stack.setCurrentIndex(2)

    def _on_apps(self, apps: list[AppEntry]) -> None:
        self.model.set_rows(apps, keep_checked=True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        view = self.view_combo.currentData()
        source = self.source_combo.currentData()

        def pred(a: AppEntry) -> bool:
            if source and a.source != source:
                return False
            if view == "apps" and not (a.has_gui or a.source == "flatpak"):
                return False
            if view == "explicit" and not a.explicit:
                return False
            if text and text not in a.name.lower() and text not in a.ident.lower() \
                    and text not in a.description.lower():
                return False
            return True

        self.proxy.set_predicate(pred)
        n = self.proxy.rowCount()
        self.count_lbl.setText(tr("{n} items").format(n=n))
        if not self.model.rows:
            self.stack.setCurrentIndex(0)
        else:
            self.stack.setCurrentIndex(1 if n else 2)

    def _row_changed(self, current, _prev) -> None:
        self.details.show_app(current.data(OBJECT_ROLE) if current.isValid() else None)

    def _toggle_row(self, index) -> None:
        app = index.data(OBJECT_ROLE)
        if app is not None:
            self.model.set_checked([app], app.key not in self.model.checked)

    def _scan_tick_changed(self, on: bool) -> None:
        self.ctx.settings.scan_leftovers_after_uninstall = on
        self.ctx.settings.save()

    def _update_actionbar(self) -> None:
        sel = self.model.checked_objects()
        if sel:
            self.sel_lbl.setText(tr("{n} apps selected · {size}").format(
                n=len(sel), size=format_size(sum(a.size for a in sel))))
        else:
            self.sel_lbl.setText(tr("Tick what you want to remove (or select a single row)."))
        self.clear_btn.setEnabled(bool(sel))

    # ------------------------------------------------------------------ #
    def _targets(self) -> list[AppEntry]:
        sel = self.model.checked_objects()
        if sel:
            return sel
        idx = self.table.currentIndex()
        app = idx.data(OBJECT_ROLE) if idx.isValid() else None
        return [app] if app else []

    def uninstall(self) -> None:
        targets = self._targets()
        if not targets:
            self.notify(tr("Tick the apps to remove first."), "warning")
            return
        dlg = ConfirmUninstallDialog(targets, self.scan_tick.isChecked(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        scan = dlg.scan.isChecked()
        self.scan_tick.setChecked(scan)
        steps: list[Step] = []
        pkgs = [a.ident for a in targets if a.source in ("repo", "aur")]
        if pkgs:
            steps.append(Step(tr("Removing {n} packages (pacman)").format(n=len(pkgs)),
                              helper_cmd("pacman-remove", *pkgs), uses_pkexec=True,
                              continue_on_error=True))
        groups: dict[str, list[AppEntry]] = {}
        for a in targets:
            if a.source == "flatpak" and a.flatpak_app:
                groups.setdefault(a.flatpak_app.install_flag, []).append(a)
        for flag, apps in groups.items():
            steps.append(Step(tr("Removing {n} Flatpak apps").format(n=len(apps)),
                              ["flatpak", "uninstall", "-y", "--noninteractive", flag,
                               *[a.ident for a in apps]], continue_on_error=True))
        before = list(self.ctx.apps)
        TaskDialog(tr("Removing applications"), steps, self,
                   success_text=tr("Removal finished.")).run()
        self.model.set_checked(targets, False)
        self._after_uninstall(targets, before, scan)

    def _after_uninstall(self, targets, before, scan: bool) -> None:
        def once(apps):
            if apps is None:      # the list could not be re-read; the error was already shown
                return
            keys = {a.key for a in apps}
            removed = [t for t in targets if t.key not in keys]
            failed = [t for t in targets if t.key in keys]
            if removed:
                self.notify(trn("{n} app removed.", "{n} apps removed.", len(removed)).format(
                    n=len(removed)), "ok")
            if failed:
                self.notify(trn("{n} app could not be removed: {names}",
                                "{n} apps could not be removed: {names}", len(failed)).format(
                    n=len(failed), names=", ".join(t.name for t in failed[:3])), "error")
            if scan and removed:
                self._scan_leftovers(removed, before, keys)

        # the callback only receives a list read after the removal finished
        self.ctx.reload_apps(on_loaded=once, owner=self)

    def _scan_leftovers(self, removed, before, still_installed) -> None:
        include_system = self.ctx.settings.include_system_leftovers

        def work(progress):
            found, cache = [], {}
            for app in removed:
                found += leftovers.scan(app, before, still_installed=still_installed,
                                        include_system=include_system, progress=progress,
                                        keys_cache=cache)
            return found

        self.sel_lbl.setText(tr("Scanning for leftover files…"))
        run_async(work, on_done=self._show_leftovers,
                  on_error=lambda e: self.notify(tr("Leftover scan failed: {e}").format(e=e), "error"),
                  on_progress=self._show_progress)

    def inspect(self) -> None:
        """Lists everything related to the selected app(s) without removing anything."""
        targets = self._targets()
        if not targets:
            self.notify(tr("Select an application first."), "warning")
            return
        include_system = self.ctx.settings.include_system_leftovers
        all_apps = list(self.ctx.apps)

        def work(progress):
            found, prints, cache = [], [], {}
            for app in targets:
                prints.append((app, leftovers.footprint(app)))
                found += leftovers.scan(app, all_apps, include_system=include_system,
                                        progress=progress, keys_cache=cache)
            return found, prints

        self.inspect_btn.setEnabled(False)
        self.sel_lbl.setText(tr("Scanning for leftover files…"))
        run_async(work, on_done=self._show_inspection,
                  on_error=lambda e: (self.inspect_btn.setEnabled(True),
                                      self.notify(tr("Leftover scan failed: {e}").format(e=e), "error")),
                  on_progress=self._show_progress)

    def _show_progress(self, _percent, message: str) -> None:
        if message:
            self.sel_lbl.setText(message)

    def _show_inspection(self, result) -> None:
        found, prints = result
        self.inspect_btn.setEnabled(True)
        self._update_actionbar()
        info = "<br>".join(_footprint_html(app, fp) for app, fp in prints)
        info += "<br>" + tr("The application is still installed. Deleting its user data below only "
                            "resets its settings; to remove the program itself use “Remove”.")
        if not found:
            info += "<br>" + tr("No separate user data, cache or configuration was found.")
        dlg = LeftoverDialog(found, self, title=tr("Everything related to the selected app"),
                             delete_mode=self.ctx.settings.delete_mode, info=info, preselect=False)
        dlg.exec()
        if dlg.removed:
            self.notify(trn("{n} leftover item cleaned.", "{n} leftover items cleaned.",
                            dlg.removed).format(n=dlg.removed), "ok")
        if dlg.errors:
            self.notify(tr("Some items could not be deleted: ") + dlg.errors[0], "error")

    def _show_leftovers(self, found) -> None:
        self._update_actionbar()
        if not found:
            self.notify(tr("No leftover files found, all clean!"), "ok")
            return
        dlg = LeftoverDialog(found, self, delete_mode=self.ctx.settings.delete_mode)
        dlg.exec()
        if dlg.removed:
            self.notify(trn("{n} leftover item cleaned.", "{n} leftover items cleaned.",
                               dlg.removed).format(n=dlg.removed), "ok")
        if dlg.errors:
            self.notify(tr("Some items could not be deleted: ") + dlg.errors[0], "error")

    def scan_orphans(self) -> None:
        if not self.ctx.apps:
            self.notify(tr("Wait for the application list to load first."), "warning")
            return
        self.sel_lbl.setText(tr("Scanning for orphaned leftovers…"))
        apps = list(self.ctx.apps)
        run_async(leftovers.scan_orphans, apps, key=(id(self), "orphans"),
                  on_done=self._show_orphans,
                  on_error=lambda e: self.notify(tr("Scan failed: {e}").format(e=e), "error"),
                  on_progress=self._show_progress)

    def _show_orphans(self, found) -> None:
        self._update_actionbar()
        if not found:
            self.notify(tr("No orphaned leftovers found."), "ok")
            return
        dlg = LeftoverDialog(found, self, title=tr("Orphaned leftovers"), orphan_mode=True,
                             delete_mode=self.ctx.settings.delete_mode)
        dlg.exec()
        if dlg.removed:
            self.notify(trn("{n} item cleaned.", "{n} items cleaned.", dlg.removed).format(
                n=dlg.removed), "ok")
