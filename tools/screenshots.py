#!/usr/bin/env python3
"""Renders every page to a PNG using the fake environment (for docs/screenshots).

    python3 tools/fake_env/make_fake_env.py /tmp/lmp-fake
    source /tmp/lmp-fake/env.sh
    QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py OUTDIR [lang] [theme]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QTimer  # noqa: E402

from lmp import i18n  # noqa: E402
from lmp.core import sysinfo  # noqa: E402
from lmp.ui import main_window  # noqa: E402
from lmp.ui.app import Application  # noqa: E402

# Present a reproducible machine in the screenshots instead of the real host.
_collect = sysinfo.collect


def _fake_collect(*args, **kwargs):
    ov = _collect(*args, **kwargs)
    ov.os_name, ov.kernel = "Arch Linux", "6.17.7-arch1-1"
    ov.cpu, ov.cores = "AMD Ryzen 7 5800X", 16
    ov.ram_total, ov.ram_available = 32 * 1024**3, 21 * 1024**3
    ov.uptime = 5 * 3600 + 1260
    return ov


sysinfo.collect = _fake_collect
main_window.os_release = lambda: {"PRETTY_NAME": "Arch Linux"}

PAGES = ["dashboard", "uninstall", "startup", "services", "profile", "external", "builder",
         "settings"]
out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
lang = sys.argv[2] if len(sys.argv) > 2 else "en"
theme = sys.argv[3] if len(sys.argv) > 3 else "dark"
out.mkdir(parents=True, exist_ok=True)

app = Application(["lmp"])
app.ctx.settings.theme = theme
app.ctx.settings.language = lang
i18n.set_language(lang)
app.apply_theme()
app.build_window()
window = app.window
window.resize(1360, 860)
queue = list(PAGES)


def leftover_dialog() -> None:
    """The "Scan leftovers" window for one app (what the Uninstaller button opens)."""
    from lmp.core import leftovers
    from lmp.ui.pages import uninstaller
    from lmp.ui.pages.leftover_dialog import LeftoverDialog

    apps = app.ctx.apps
    target = next(a for a in apps if a.ident == "discord")
    info = (uninstaller._footprint_html(target, leftovers.footprint(target)) + "<br>"
            + i18n.tr("The application is still installed. Deleting its user data below only "
                      "resets its settings; to remove the program itself use “Remove”."))
    dlg = LeftoverDialog(leftovers.scan(target, apps), window,
                         title=i18n.tr("Everything related to the selected app"), info=info,
                         preselect=False)
    dlg.show()
    QTimer.singleShot(400, lambda: (dlg.grab().save(str(out / "leftovers.png")), app.qt.quit()))


def next_page() -> None:
    if not queue:
        leftover_dialog()
        return
    key = queue.pop(0)
    window.show_page(key)

    def prepare() -> None:
        page = window.pages.get(key)
        if key == "uninstall":
            page.table.selectRow(5)
            page.model.set_checked([page.proxy.index(r, 0).data(0x0100 + 2) for r in (1, 7)])
        if key == "services":
            page.table.selectRow(2)

    QTimer.singleShot(2000, prepare)
    QTimer.singleShot(2600, lambda: (window.grab().save(str(out / f"{key}.png")), next_page()))


QTimer.singleShot(1500, next_page)
app.qt.exec()
