from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView,
                             QLineEdit, QSpinBox)

from lmp.core import builder
from lmp.core.util import have, os_release
from lmp.i18n import tr, trn, tr_noop
from lmp.ui import theme
from lmp.ui.background import run_async
from lmp.ui.models import OBJECT_ROLE, Column, ObjectTableModel, make_table
from lmp.ui.runner import Step, TaskDialog
from lmp.ui.widgets import Banner, Card, Page, button, confirm, label, tool_button

PLACEHOLDERS = {
    "repo": tr_noop("Package name, e.g. zstd, ffmpeg, mesa"),
    "aur": tr_noop("AUR package name, e.g. paru, visual-studio-code-bin"),
    "git": tr_noop("Git address, e.g. https://github.com/user/project"),
}


class BuilderPage(Page):
    title = tr_noop("Builder")
    subtitle = tr_noop("Gentoo-style: compile packages from source, optimised for this CPU")
    icon_name = "build"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        info = os_release()
        if "cachyos" in (info.get("ID", "") + info.get("ID_LIKE", "")).lower():
            self.content.addWidget(Banner(
                tr("CachyOS already ships packages optimised for x86-64-v3/v4. A local build "
                   "(-march=native) fits your CPU exactly, but the gain is usually small; it "
                   "helps most for heavy programs you use a lot (ffmpeg, game engines, "
                   "compression tools).")))

        top = QHBoxLayout()
        top.setSpacing(12)
        new = Card()
        new.body.addWidget(label(tr("New build"), "h2"))
        row = QHBoxLayout()
        self.source = QComboBox()
        for key, text in builder.SOURCES.items():
            self.source.addItem(tr(text), key)
        self.source.currentIndexChanged.connect(self._source_changed)
        row.addWidget(self.source)
        self.target = QLineEdit()
        self.target.returnPressed.connect(self.start_build)
        row.addWidget(self.target, 1)
        new.body.addLayout(row)
        self.build_btn = button(tr("Build and install"), "build", "primary", on_click=self.start_build)
        row2 = QHBoxLayout()
        self.build_info = label(tr("The build runs as your normal user; you are asked for your "
                                   "password for dependencies and installation."),
                               "small", wrap=True)
        row2.addWidget(self.build_info, 1)
        row2.addWidget(self.build_btn)
        new.body.addLayout(row2)
        new.body.addStretch()
        top.addWidget(new, 3)

        prof = Card()
        prof.body.addWidget(label(tr("Build profile"), "h2"))
        self.cpu_lbl = label(tr("Detecting CPU…"), "small", wrap=True)
        prof.body.addWidget(self.cpu_lbl)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        o = ctx.settings.build
        self.cb_native = QCheckBox(tr("-march=native (tuned for this CPU)"))
        self.cb_native.setChecked(o.march_native)
        self.opt = QComboBox()
        self.opt.addItem(tr("-O2 (safe, recommended)"), "O2")
        self.opt.addItem(tr("-O3 (aggressive)"), "O3")
        self.opt.setCurrentIndex(1 if o.opt_level == "O3" else 0)
        self.cb_lto = QCheckBox(tr("LTO (link-time optimisation)"))
        self.cb_lto.setChecked(o.lto)
        self.cb_ccache = QCheckBox(tr("ccache (speeds up rebuilds)"))
        self.cb_ccache.setChecked(o.ccache and have("ccache"))
        self.cb_ccache.setEnabled(have("ccache"))
        if not have("ccache"):
            self.cb_ccache.setToolTip(tr("ccache is not installed (sudo pacman -S ccache)"))
        self.cb_check = QCheckBox(tr("Skip tests (faster)"))
        self.cb_check.setChecked(o.skip_check)
        self.cb_pgp = QCheckBox(tr("Skip PGP verification"))
        self.cb_pgp.setChecked(o.skip_pgp)
        self.cb_debug = QCheckBox(tr("Don't build a debug package"))
        self.cb_debug.setChecked(o.no_debug)
        self.cb_rmdeps = QCheckBox(tr("Remove build dependencies afterwards"))
        self.cb_rmdeps.setChecked(o.remove_makedeps)
        self.jobs = QSpinBox()
        self.jobs.setRange(0, 256)
        self.jobs.setSpecialValueText(tr("Automatic ({n})").format(n=os.cpu_count()))
        self.jobs.setValue(o.jobs)
        widgets = [self.cb_native, self.cb_lto, self.cb_ccache, self.cb_check, self.cb_pgp,
                   self.cb_debug, self.cb_rmdeps]
        for i, w in enumerate(widgets):
            grid.addWidget(w, i // 2, i % 2)
        opt_row = QHBoxLayout()
        opt_row.addWidget(label(tr("Optimisation"), "muted"))
        opt_row.addWidget(self.opt)
        opt_row.addWidget(label(tr("Threads"), "muted"))
        opt_row.addWidget(self.jobs)
        opt_row.addStretch()
        prof.body.addLayout(grid)
        prof.body.addLayout(opt_row)
        self.flags_lbl = label("", "small", wrap=True)
        self.flags_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.flags_lbl.setStyleSheet("font-family: monospace;")
        prof.body.addWidget(self.flags_lbl)
        top.addWidget(prof, 4)
        self.content.addLayout(top)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._options_changed)
        for w in widgets:
            w.toggled.connect(self._debounce.start)
        self.opt.currentIndexChanged.connect(self._debounce.start)
        self.jobs.valueChanged.connect(self._debounce.start)

        rec = Card()
        head = QHBoxLayout()
        head.addWidget(label(tr("Locally built packages"), "h2"))
        head.addStretch()
        head.addWidget(button(tr("Rebuild selected"), "restart", on_click=self.rebuild_selected))
        self.rebuild_all_btn = button(tr("Rebuild everything that needs it"), "build",
                                      on_click=self.rebuild_needed)
        head.addWidget(self.rebuild_all_btn)
        head.addWidget(button(tr("Remove from list"), on_click=self.forget))
        head.addWidget(tool_button("refresh", tr("Check statuses"), self.load_records))
        rec.body.addLayout(head)
        p = theme.current()
        state_colors = {"ok": p.success, "replaced": p.warning, "outdated": p.warning,
                        "removed": p.muted}
        self.rec_model = ObjectTableModel([
            Column(tr("Package"), lambda s: s.record.name),
            Column(tr("Source"), lambda s: tr(builder.SOURCES.get(s.record.source,
                                                                  s.record.source))),
            Column(tr("Built"), lambda s: s.record.built_version),
            Column(tr("Installed version"), lambda s: s.installed or "—"),
            Column(tr("Status"), lambda s: s.label, color=lambda s: state_colors.get(s.state)),
            Column(tr("Flags"), lambda s: s.record.options),
            Column(tr("Date"), lambda s: s.record.date),
        ])
        self.rec_table, _ = make_table(self.rec_model)
        hh = self.rec_table.horizontalHeader()
        for c in range(7):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.rec_empty = label(tr("No locally built packages yet. Type a package name above and press "
                                  "'Build and install'; built packages are tracked here and "
                                  "flagged for a rebuild when the repository gets a new "
                                  "version."), "muted", wrap=True)
        rec.body.addWidget(self.rec_empty)
        rec.body.addWidget(self.rec_table, 1)
        self.content.addWidget(rec, 1)
        self._source_changed()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        run_async(builder.native_cpu, on_done=self._show_cpu)
        self._options_changed()
        self.load_records()

    def _show_cpu(self, result) -> None:
        model, march = result
        text = model or tr("Could not read the CPU model")
        if march:
            text += tr("  ·  gcc -march=native → <b>{march}</b>").format(march=march)
        elif not have("gcc"):
            text += tr("  ·  gcc is not installed (base-devel required)")
        self.cpu_lbl.setText(text)

    def _source_changed(self) -> None:
        self.target.setPlaceholderText(PLACEHOLDERS[self.source.currentData()])

    def options(self) -> builder.BuildOptions:
        return builder.BuildOptions(
            march_native=self.cb_native.isChecked(), opt_level=self.opt.currentData(),
            lto=self.cb_lto.isChecked(), ccache=self.cb_ccache.isChecked(),
            skip_check=self.cb_check.isChecked(), skip_pgp=self.cb_pgp.isChecked(),
            no_debug=self.cb_debug.isChecked(), remove_makedeps=self.cb_rmdeps.isChecked(),
            jobs=self.jobs.value())

    def _options_changed(self) -> None:
        opts = self.options()
        self.ctx.settings.build = opts
        self.ctx.save_settings()
        if os.path.exists("/etc/makepkg.conf"):
            run_async(builder.effective_flags, opts, owner=self, key=(id(self), "flags"),
                      on_done=lambda text: self.flags_lbl.setText(text))
        else:
            self.flags_lbl.setText(tr("Preview: /etc/makepkg.conf not found (is pacman installed?)"))

    # ------------------------------------------------------------------ #
    def load_records(self) -> None:
        run_async(builder.record_statuses, on_done=self._show_records,
                  on_error=lambda e: self.notify(tr("Could not read statuses: {e}").format(e=e), "error"))

    def _show_records(self, statuses) -> None:
        self.rec_model.set_rows(statuses)
        self.rec_empty.setVisible(not statuses)
        need = [s for s in statuses if s.state in ("replaced", "outdated")]
        self.rebuild_all_btn.setText(tr("Rebuild everything that needs it ({n})").format(n=len(need)))
        self.rebuild_all_btn.setEnabled(bool(need))

    def start_build(self) -> None:
        target = self.target.text().strip()
        if not target:
            self.notify(tr("Enter the package or address to build."), "warning")
            return
        self._build_queue([(self.source.currentData(), target)])

    def _build_queue(self, items: list[tuple[str, str]]) -> None:
        if os.geteuid() == 0:
            self.notify(tr("makepkg does not run as root; open the app as your normal user."),
                        "error")
            return
        if not have("makepkg"):
            self.notify(tr("makepkg not found. Install the 'base-devel' group."), "error")
            return
        self.build_btn.setEnabled(False)
        self.build_info.setText(tr("Fetching package info…"))
        opts = self.options()

        def work():
            return [builder.resolve(s, t) for s, t in items]

        def resolved(jobs):
            self.build_btn.setEnabled(True)
            self.build_info.setText("")
            notes = [n for j in jobs for n in j.notes]
            if notes and not confirm(self, tr("Before building"), "\n\n".join(notes),
                                     ok_text=tr("Build")):
                return
            conf = builder.write_conf(opts)
            steps = [Step(tr("Building {name} ({opts})").format(name=j.name, opts=opts.summary()),
                          ["bash", "-c", builder.build_script(j, opts, conf)],
                          continue_on_error=len(jobs) > 1) for j in jobs]
            dlg = TaskDialog(tr("Local build"), steps, self,
                             success_text=tr("Build and installation finished."))
            dlg.run()
            saved = 0
            for job, ok in zip(jobs, dlg.results):
                if ok and builder.save_record(job, opts):
                    saved += 1
            if saved:
                self.notify(trn("{n} package was built locally and installed.",
                                   "{n} packages were built locally and installed.",
                                   saved).format(n=saved), "ok")
                self.target.clear()
                self.ctx.reload_apps()
            self.load_records()

        def failed(msg):
            self.build_btn.setEnabled(True)
            self.build_info.setText(msg)
            self.notify(msg, "error")

        run_async(work, on_done=resolved, on_error=failed, owner=self)

    def _selected_status(self):
        idx = self.rec_table.currentIndex()
        return idx.data(OBJECT_ROLE) if idx.isValid() else None

    def rebuild_selected(self) -> None:
        s = self._selected_status()
        if s:
            self._build_queue([(s.record.source, s.record.target)])

    def rebuild_needed(self) -> None:
        items = [(s.record.source, s.record.target) for s in self.rec_model.rows
                 if s.state in ("replaced", "outdated")]
        if items and confirm(
                self, tr("Batch build"),
                trn("{n} package will be built in sequence. This can take a long time.",
                    "{n} packages will be built in sequence. This can take a long time.",
                    len(items)).format(n=len(items)), ok_text=tr("Start")):
            self._build_queue(items)

    def forget(self) -> None:
        s = self._selected_status()
        if s:
            builder.forget_record(s.record.name)
            self.load_records()
