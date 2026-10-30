from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from lmp.core import sysinfo
from lmp.core.privileged import helper_cmd
from lmp.core.util import format_size
from lmp.i18n import tr, trn, tr_noop
from lmp.ui.background import run_async
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import Card, Page, StatTile, UsageBar, button, confirm, label


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return tr("{d}d {h}h").format(d=d, h=h)
    if h:
        return tr("{h}h {m}m").format(h=h, m=m)
    return tr("{m}m").format(m=m)


class DashboardPage(Page):
    title = tr_noop("Overview")
    subtitle = tr_noop("Your system at a glance, plus quick maintenance tasks")
    icon_name = "dashboard"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.header_actions.addWidget(button(tr("Refresh"), "refresh", on_click=self.refresh))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(14)
        self.content.addWidget(scroll)

        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.t_pkgs = StatTile(tr("Installed packages"), "package")
        self.t_flatpak = StatTile(tr("Flatpak apps"), "backup")
        self.t_orphans = StatTile(tr("Orphaned packages"), "broom")
        self.t_cache = StatTile(tr("Package cache"), "disk")
        self.orphan_btn = button(tr("Clean"), "broom", on_click=self.clean_orphans)
        self.orphan_btn.setEnabled(False)
        self.t_orphans.action_row.addWidget(self.orphan_btn)
        self.cache_btn = button(tr("Clean"), "broom", on_click=self.clean_cache)
        self.cache_btn.setEnabled(False)
        self.t_cache.action_row.addWidget(self.cache_btn)
        for i, t in enumerate((self.t_pkgs, self.t_flatpak, self.t_orphans, self.t_cache)):
            tiles.addWidget(t, 0, i)
        lay.addLayout(tiles)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.sys_card = Card()
        self.sys_card.body.addWidget(label(tr("System"), "h2"))
        self.sys_grid = QGridLayout()
        self.sys_grid.setHorizontalSpacing(18)
        self.sys_grid.setVerticalSpacing(6)
        self.sys_card.body.addLayout(self.sys_grid)
        self.sys_card.body.addStretch()
        row.addWidget(self.sys_card, 1)

        self.disk_card = Card()
        self.disk_card.body.addWidget(label(tr("Disks"), "h2"))
        self.disk_box = QVBoxLayout()
        self.disk_box.setSpacing(10)
        self.disk_card.body.addLayout(self.disk_box)
        self.disk_card.body.addStretch()
        row.addWidget(self.disk_card, 1)
        lay.addLayout(row)

        quick = Card()
        quick.body.addWidget(label(tr("Quick actions"), "h2"))
        qrow = QHBoxLayout()
        for text, icon_name, page in ((tr("Uninstall an app"), "uninstall", "uninstall"),
                                      (tr("Find orphaned leftovers"), "broom", "uninstall:orphans"),
                                      (tr("Speed up boot"), "startup", "startup"),
                                      (tr("Back up a profile"), "backup", "profile"),
                                      (tr("Build locally"), "build", "builder")):
            qrow.addWidget(button(text, icon_name,
                                  on_click=lambda _=False, p=page: self.navigate.emit(p)))
        qrow.addStretch()
        quick.body.addLayout(qrow)
        lay.addWidget(quick)
        lay.addStretch()
        self.overview: sysinfo.Overview | None = None
        self._loaded_once = False
        # package counts come from the shared application list (no second pacman query)
        ctx.apps_loaded.connect(self._show_counts)
        ctx.apps_load_failed.connect(self._counts_failed)

    def refresh(self) -> None:
        for t in (self.t_pkgs, self.t_flatpak, self.t_orphans, self.t_cache):
            t.set("…", tr("Loading"))
        if self._loaded_once:
            self.ctx.reload_apps()
        elif self.ctx.apps:
            self._show_counts(self.ctx.apps)
        self._loaded_once = True
        run_async(sysinfo.collect, False, on_done=self._show, key=(id(self), "overview"),
                  on_error=lambda e: self.notify(tr("Could not read system info: {e}").format(e=e), "error"))

    def _show_counts(self, apps) -> None:
        packages = [a for a in apps if a.source != "flatpak"]
        explicit = sum(1 for a in packages if a.explicit)
        foreign = sum(1 for a in packages if a.source == "aur")
        self.t_pkgs.set(str(len(packages)),
                        tr("{explicit} explicitly installed · {foreign} AUR/local").format(
                            explicit=explicit, foreign=foreign))
        self.t_flatpak.set(str(len(apps) - len(packages)), tr("Apps running inside a sandbox"))

    def _counts_failed(self, _message: str) -> None:
        for t in (self.t_pkgs, self.t_flatpak):
            t.set("—", tr("Could not read the application list."))

    def _show(self, ov: sysinfo.Overview) -> None:
        self.overview = ov
        self.t_orphans.set(str(len(ov.orphans)),
                           tr("Dependencies no package needs anymore") if ov.orphans
                           else tr("Clean, no orphaned packages"))
        self.orphan_btn.setEnabled(bool(ov.orphans))
        self.t_cache.set(format_size(ov.cache_size), "/var/cache/pacman/pkg")
        self.cache_btn.setEnabled(ov.cache_size > 0)

        while self.sys_grid.count():
            w = self.sys_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        ram_used = ov.ram_total - ov.ram_available
        rows = [
            (tr("Distribution"), ov.os_name), (tr("Kernel"), ov.kernel),
            (tr("CPU"), tr("{cpu} ({n} threads)").format(cpu=ov.cpu, n=ov.cores)),
            (tr("Memory"), f"{format_size(ram_used)} / {format_size(ov.ram_total)}"),
            (tr("Desktop"), ov.desktop or "—"), (tr("Uptime"), _duration(ov.uptime)),
            (tr("Last boot time"),
             tr("{s:.1f} s").format(s=ov.boot_seconds) if ov.boot_seconds else "—"),
        ]
        for i, (k, v) in enumerate(rows):
            self.sys_grid.addWidget(label(k, "muted"), i, 0, Qt.AlignmentFlag.AlignTop)
            val = label(v, wrap=True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.sys_grid.addWidget(val, i, 1)
        self.sys_grid.setColumnStretch(1, 1)

        while self.disk_box.count():
            item = self.disk_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for d in ov.drives[:6]:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            top = QHBoxLayout()
            top.addWidget(label(d.title))
            top.addStretch()
            top.addWidget(label(tr("{percent:.0f}%").format(percent=d.percent), "muted"))
            v.addLayout(top)
            v.addWidget(UsageBar(d.percent))
            v.addWidget(label(f"{d.mountpoint} · {d.summary}", "small"))
            self.disk_box.addWidget(w)
        if not ov.drives:
            self.disk_box.addWidget(label(tr("Could not read disk info."), "muted"))

    def clean_orphans(self) -> None:
        if not self.overview or not self.overview.orphans:
            return
        names = self.overview.orphans
        preview = ", ".join(names[:15]) + (" …" if len(names) > 15 else "")
        if not confirm(self, tr("Remove orphaned packages"),
                       trn("{n} orphaned package will be removed.",
                           "{n} orphaned packages will be removed.", len(names)).format(n=len(names)),
                       informative=preview, ok_text=tr("Remove"), danger=True):
            return
        dlg = TaskDialog(tr("Removing orphaned packages"),
                         [Step("pacman -Rns", helper_cmd("remove-orphans"), uses_pkexec=True)],
                         self, success_text=tr("Orphaned packages removed."))
        dlg.run()
        self.refresh()      # also re-reads the application list

    def clean_cache(self) -> None:
        keep = self.ctx.settings.cache_keep
        if not confirm(self, tr("Clean the package cache"),
                       tr("Downloaded package files other than the latest {keep} versions of "
                          "each package, and cached files of uninstalled packages, will be "
                          "deleted.").format(keep=keep),
                       ok_text=tr("Clean")):
            return
        dlg = TaskDialog(tr("Cleaning the cache"),
                         [Step("paccache", helper_cmd("clean-cache", f"--keep={keep}",
                                                      "--uninstalled"), uses_pkexec=True)],
                         self, success_text=tr("Cache cleaned."))
        dlg.run()
        self.refresh()
