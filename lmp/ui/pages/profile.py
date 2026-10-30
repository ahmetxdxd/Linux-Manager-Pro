from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QSplitter,
                             QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from lmp.core import pacman, profile
from lmp.core.privileged import helper_cmd
from lmp.core.util import HOME, format_size, tilde
from lmp.i18n import tr, tr_noop
from lmp.ui import theme
from lmp.ui.background import run_async
from lmp.ui.models import Column, ObjectTableModel, make_table
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import Banner, Card, EmptyState, Page, SearchField, button, label

SOURCE_TEXT = {"repo": tr_noop("Official repo"), "aur": "AUR", "flatpak": "Flatpak"}

LARGE_CONFIG = 500 * 1024**2       # shown in the warning colour
KNOWN_SELECT_LIMIT = 2 * 1024**3   # "Select known" skips anything bigger than this


@dataclass
class InstallItem:
    name: str
    source: str
    origin: str = "flathub"


class ExportTab(QWidget):
    def __init__(self, page: "ProfilePage"):
        super().__init__()
        self.page = page
        self.content: profile.ProfileContent | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        pk = Card()
        pk.body.addWidget(label(tr("Application list"), "h2"))
        pk.body.addWidget(label(tr("Only packages you installed explicitly are saved; dependencies "
                                   "come along automatically on the target system."),
                         "small", wrap=True))
        row = QHBoxLayout()
        self.cb_repo = QCheckBox(tr("Official repo packages"))
        self.cb_aur = QCheckBox(tr("AUR / local packages"))
        self.cb_flatpak = QCheckBox(tr("Flatpak apps"))
        for cb in (self.cb_repo, self.cb_aur, self.cb_flatpak):
            cb.setChecked(True)
            row.addWidget(cb)
        row.addStretch()
        pk.body.addLayout(row)
        lay.addWidget(pk)

        cfg = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("Configurations"), "h2"))
        head.addStretch()
        self.search = SearchField(tr("Search folders…"))
        self.search.textChanged.connect(self._filter)
        head.addWidget(self.search)
        head.addWidget(button(tr("Select known"), on_click=self._select_known))
        head.addWidget(button(tr("None"), on_click=lambda: self.model.set_checked(self.model.rows, False)))
        head.addWidget(button(tr("Add folder"), "plus", on_click=self._add_folder))
        cfg.body.addLayout(head)
        p = theme.current()
        columns = [
            Column(tr("Name"), lambda c: c.label),
            Column(tr("Location"), lambda c: tilde(c.path)),
            Column(tr("Size"), lambda c: format_size(c.size), sort_key=lambda c: c.size,
                   align_right=True,
                   color=lambda c: p.warning if c.size > LARGE_CONFIG else None),
            Column(tr("Note"), lambda c: tr("Contains private keys") if c.sensitive else
                   (tr("Known application") if c.known else ""),
                   color=lambda c: p.danger if c.sensitive else None),
        ]
        self.model = ObjectTableModel(columns, checkable=True)
        self.model.key = lambda c: str(c.path)
        self.model.on_check_changed = self._update_total
        self.table, self.proxy = make_table(self.model)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        cfg.body.addWidget(self.table, 1)
        lay.addWidget(cfg, 1)

        bottom = QHBoxLayout()
        self.total = label(tr("Loading…"), "muted")
        bottom.addWidget(self.total)
        bottom.addStretch()
        bottom.addWidget(button(tr("Save list only (.txt)"), "file", on_click=self.save_list))
        self.export_btn = button(tr("Create profile"), "export", "primary", on_click=self.export)
        bottom.addWidget(self.export_btn)
        lay.addLayout(bottom)

    def load(self) -> None:
        def work():
            return profile.current_packages(), profile.config_candidates()

        run_async(work, on_done=self._loaded, key=(id(self), "load"),
                  on_error=lambda e: self.page.notify(tr("Could not read: {e}").format(e=e), "error"))

    def _loaded(self, result) -> None:
        self.content, candidates = result
        self.cb_repo.setText(tr("Official repo packages ({n})").format(n=len(self.content.repo)))
        self.cb_aur.setText(tr("AUR / local packages ({n})").format(n=len(self.content.aur)))
        self.cb_flatpak.setText(tr("Flatpak apps ({n})").format(n=len(self.content.flatpak)))
        self.model.set_rows(candidates)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._select_known()

    def _select_known(self) -> None:
        self.model.set_checked(self.model.rows, False)
        self.model.set_checked([c for c in self.model.rows if c.known and not c.sensitive
                                and c.size < KNOWN_SELECT_LIMIT])

    def _filter(self) -> None:
        text = self.search.text().lower()
        self.proxy.set_predicate(lambda c: not text or text in c.label.lower()
                                 or text in str(c.path).lower())

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Folder to back up"), str(HOME))
        if not path:
            return
        if not path.startswith(str(HOME) + "/"):
            self.page.notify(tr("Only folders inside your home directory can be added."), "warning")
            return
        if any(str(c.path) == path for c in self.model.rows):
            self.model.set_checked([c for c in self.model.rows if str(c.path) == path])
            return
        from lmp.core.util import dir_size
        self.total.setText(tr("Calculating folder size…"))

        def added(size):
            cand = profile.ConfigCandidate(Path(path), Path(path).name, size)
            self.model.set_rows(self.model.rows + [cand], keep_checked=True)
            self.model.set_checked([cand])

        # sizing a big folder on the UI thread would freeze the window
        run_async(dir_size, path, on_done=added, owner=self,
                  on_error=lambda e: (self._update_total(),
                                      self.page.notify(tr("Could not read: {e}").format(e=e), "error")))

    def _update_total(self) -> None:
        sel = self.model.checked_objects()
        self.total.setText(tr("{n} configurations selected · {size} (caches are excluded)").format(
            n=len(sel), size=format_size(sum(c.size for c in sel))))

    def _selected_content(self) -> profile.ProfileContent:
        c = self.content or profile.ProfileContent()
        return profile.ProfileContent(repo=c.repo if self.cb_repo.isChecked() else [],
                                      aur=c.aur if self.cb_aur.isChecked() else [],
                                      flatpak=c.flatpak if self.cb_flatpak.isChecked() else [])

    def save_list(self) -> None:
        if not self.content:
            return
        default = str(HOME / tr("app-list-{date}.txt").format(date=f"{datetime.now():%Y%m%d}"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save list"), default, tr("Text (*.txt)"))
        if not path:
            return
        profile.export_text_list(Path(path), self._selected_content())
        self.page.notify(tr("List saved: {path}").format(path=tilde(path)), "ok")

    def export(self) -> None:
        if not self.content:
            return
        sel = self.model.checked_objects()
        if any(c.sensitive for c in sel):
            from lmp.ui.widgets import confirm
            if not confirm(self, tr("Private keys"),
                           tr("The selection contains SSH/GPG private keys. The profile file "
                              "is not encrypted; keep it somewhere safe."),
                           ok_text=tr("I understand, continue")):
                return
        default = str(HOME / tr("linux-profile-{date}.lmp.tar.gz").format(
            date=f"{datetime.now():%Y%m%d}"))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save profile"), default,
                                              tr("Linux Manager Pro profile (*.tar.gz)"))
        if not path:
            return
        content = self._selected_content()
        paths = [c.path for c in sel]
        result: dict = {}

        def work(progress):
            result["path"] = profile.export_profile(Path(path), content, paths, progress)
            return tr("Saved: {path}").format(path=result["path"])

        ok = TaskDialog(tr("Creating profile"), [Step(tr("Archiving files"), func=work)], self,
                        success_text=tr("Profile created.")).run()
        if ok and "path" in result:
            size = format_size(result["path"].stat().st_size)
            self.page.notify(tr("Profile saved ({size}): {path}").format(
                size=size, path=tilde(result["path"])), "ok")


class ImportTab(QWidget):
    def __init__(self, page: "ProfilePage"):
        super().__init__()
        self.page = page
        self.file: Path | None = None
        self.content: profile.ProfileContent | None = None
        self.plan: profile.RestorePlan | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(button(tr("Choose a profile or list file"), "import", "primary",
                             on_click=self.choose))
        self.source_lbl = label(tr("No file chosen yet."), "muted", wrap=True)
        top.addWidget(self.source_lbl, 1)
        lay.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(EmptyState(
            "import", tr("Choose a profile"),
            tr("Open the .lmp.tar.gz profile or .txt application list you created on another "
               "computer (or on this one before a reinstall). Missing applications are "
               "installed and the settings you choose are restored.")))
        body = QWidget()
        b = QVBoxLayout(body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(10)
        self.summary = Banner("")
        b.addWidget(self.summary)

        split = QSplitter()
        pk_card = Card()
        pk_card.body.addWidget(label(tr("Applications to install"), "h2"))
        p = theme.current()
        colors = {"repo": p.accent, "aur": p.warning, "flatpak": p.success}
        self.pkg_model = ObjectTableModel([
            Column(tr("Name"), lambda i: i.name),
            Column(tr("Source"), lambda i: tr(SOURCE_TEXT[i.source]),
                   color=lambda i: colors[i.source]),
        ], checkable=True)
        self.pkg_model.key = lambda i: f"{i.source}:{i.name}"
        self.pkg_model.on_check_changed = self._update_button
        self.pkg_table, _ = make_table(self.pkg_model)
        self.pkg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        pk_card.body.addWidget(self.pkg_table, 1)
        self.unavailable = label("", "small", wrap=True)
        pk_card.body.addWidget(self.unavailable)
        split.addWidget(pk_card)

        cfg_card = Card()
        cfg_card.body.addWidget(label(tr("Settings to restore"), "h2"))
        cfg_card.body.addWidget(label(tr("Your existing files are backed up first "
                                         "(~/.local/share/linux-manager-pro/backups)."),
                             "small", wrap=True))
        self.cfg_model = ObjectTableModel([Column(tr("Location"), lambda s: s)], checkable=True)
        self.cfg_model.key = lambda s: s
        self.cfg_model.on_check_changed = self._update_button
        self.cfg_table, _ = make_table(self.cfg_model)
        self.cfg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        cfg_card.body.addWidget(self.cfg_table, 1)
        split.addWidget(cfg_card)
        b.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.sysupgrade = QCheckBox(tr("Update the system before installing (recommended)"))
        self.sysupgrade.setChecked(True)
        self.sysupgrade.setToolTip(tr("On Arch, packages are installed together with 'pacman -Syu' "
                                      "to avoid partial upgrades."))
        bottom.addWidget(self.sysupgrade)
        bottom.addStretch()
        self.apply_btn = button(tr("Install selected and restore"), "check", "primary",
                                on_click=self.apply)
        bottom.addWidget(self.apply_btn)
        b.addLayout(bottom)
        self.stack.addWidget(body)
        lay.addWidget(self.stack, 1)

    def choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Choose a profile"), str(HOME),
            tr("Profile or list (*.tar.gz *.tgz *.txt);;All files (*)"))
        if not path:
            return
        file = Path(path)
        self.source_lbl.setText(tr("Reading: {path}").format(path=tilde(path)))

        def work():
            if file.suffix == ".txt":
                content = profile.read_text_list(file)
            else:
                content = profile.read_profile(file)
            return content, profile.plan_restore(content)

        def planned(result):
            self.file = file        # only once it has been read: a newer pick wins (key)
            self._planned(result)

        run_async(work, on_done=planned, owner=self, key=(id(self), "choose"),
                  on_error=lambda e: (self.source_lbl.setText(tr("Could not read: {e}").format(e=e)),
                                      self.page.notify(tr("Could not read the file: {e}").format(
                                          e=e), "error")))

    def _planned(self, result) -> None:
        self.content, self.plan = result
        c, plan = self.content, self.plan
        origin = ""
        if c.source_host:
            origin = tr("Source: <b>{host}</b> · {os} · {date}<br>").format(
                host=escape(c.source_host), os=escape(c.source_os), date=escape(c.created_at[:16]))
        missing = len(plan.repo_missing) + len(plan.aur_missing) + len(plan.flatpak_missing)
        self.summary.text.setText(
            tr("{origin}✓ {have} apps already installed · → {missing} apps can be installed · "
               "{configs} settings folders").format(origin=origin, have=plan.already_installed,
                                                    missing=missing, configs=len(plan.configs)))
        self.source_lbl.setText(tilde(self.file))
        items = ([InstallItem(n, "repo") for n in plan.repo_missing] +
                 [InstallItem(n, "aur") for n in plan.aur_missing] +
                 [InstallItem(f["id"], "flatpak", f.get("origin") or "flathub")
                  for f in plan.flatpak_missing])
        self.pkg_model.set_rows(items)
        self.pkg_model.set_checked(items)
        if plan.repo_unavailable:
            self.unavailable.setText(tr("Not found in the repositories (will be skipped): ") +
                                     ", ".join(plan.repo_unavailable[:30]) +
                                     (" …" if len(plan.repo_unavailable) > 30 else ""))
        else:
            self.unavailable.setText("")
        self.cfg_model.set_rows(plan.configs)
        self.cfg_model.set_checked(plan.configs)
        self.stack.setCurrentIndex(1)
        self._update_button()

    def _update_button(self) -> None:
        n = len(self.pkg_model.checked_objects()) + len(self.cfg_model.checked_objects())
        self.apply_btn.setEnabled(n > 0)

    def apply(self) -> None:
        items = self.pkg_model.checked_objects()
        configs = self.cfg_model.checked_objects()
        repo = [i.name for i in items if i.source == "repo"]
        aur = [i.name for i in items if i.source == "aur"]
        flat = [i for i in items if i.source == "flatpak"]
        steps: list[Step] = []
        if repo:
            args = ["pacman-install"] + (["--sysupgrade"] if self.sysupgrade.isChecked() else [])
            steps.append(Step(tr("Installing {n} packages (pacman)").format(n=len(repo)),
                              helper_cmd(*args, *repo), uses_pkexec=True, continue_on_error=True))
        if aur:
            helper = pacman.aur_helper(self.page.ctx.settings.aur_helper)
            if helper:
                steps.append(Step(tr("Installing {n} AUR packages ({helper})").format(n=len(aur), helper=helper),
                                  profile.aur_install_cmd(helper, aur), continue_on_error=True))
            else:
                self.page.notify(tr("No AUR helper (yay/paru) found; AUR packages were skipped."),
                                 "warning")
        by_origin: dict[str, list[str]] = {}
        for f in flat:
            by_origin.setdefault(f.origin, []).append(f.name)
        for origin, ids in by_origin.items():
            steps.append(Step(tr("Installing {n} Flatpak apps").format(n=len(ids)),
                              ["flatpak", "install", "-y", "--noninteractive", origin, *ids],
                              continue_on_error=True))
        if configs and self.file and self.file.suffix != ".txt":
            file = self.file

            def restore(progress):
                backup = profile.restore_configs(file, configs, progress)
                return tr("Your previous files were backed up: {path}").format(path=tilde(backup))

            steps.append(Step(tr("Restoring settings"), func=restore))
        if not steps:
            return
        TaskDialog(tr("Applying profile"), steps, self,
                   success_text=tr("Profile applied.")).run()
        self.page.ctx.reload_apps()
        content = self.content
        run_async(profile.plan_restore, content, owner=self, key=(id(self), "choose"),
                  on_done=lambda plan: self._replanned(content, plan))

    def _replanned(self, content, plan) -> None:
        self._planned((content, plan))
        # settings that were just restored must not be ticked again for a second run
        self.cfg_model.set_checked(self.cfg_model.rows, False)
        self._update_button()


class ProfilePage(Page):
    title = tr_noop("Backup & Transfer")
    subtitle = tr_noop("Keep your application list and settings in a single file; bring "
                       "everything back automatically after a fresh install")
    icon_name = "backup"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        tabs = QTabWidget()
        self.export_tab = ExportTab(self)
        self.import_tab = ImportTab(self)
        tabs.addTab(self.export_tab, tr("Back up"))
        tabs.addTab(self.import_tab, tr("Restore"))
        self.content.addWidget(tabs, 1)

    def refresh(self) -> None:
        self.export_tab.load()
