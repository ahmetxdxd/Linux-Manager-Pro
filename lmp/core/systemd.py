"""systemd service management.

System-service actions run `systemctl` directly; if privileges are needed, systemd itself
opens the desktop authentication dialog through polkit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lmp.core.util import run
from lmp.i18n import tr, tr_noop

ACTIONS = ("start", "stop", "restart", "enable", "disable", "mask", "unmask")
ACTION_LABELS = {
    "start": tr_noop("Start"), "stop": tr_noop("Stop"), "restart": tr_noop("Restart"),
    "enable": tr_noop("Enable at boot"), "disable": tr_noop("Disable at boot"),
    "mask": tr_noop("Mask"), "unmask": tr_noop("Unmask"),
}
UNIT_RE = re.compile(r"^[A-Za-z0-9:_.@\\-]+$")

ACTIVE_LABELS = {
    "active": tr_noop("Running"), "inactive": tr_noop("Stopped"), "failed": tr_noop("Failed"),
    "activating": tr_noop("Starting"), "deactivating": tr_noop("Stopping"), "reloading": tr_noop("Reloading"),
}
FILE_STATE_LABELS = {
    "enabled": tr_noop("Enabled"), "enabled-runtime": tr_noop("Enabled (runtime)"),
    "disabled": tr_noop("Disabled"), "static": tr_noop("Static"), "masked": tr_noop("Masked"),
    "masked-runtime": tr_noop("Masked (runtime)"), "indirect": tr_noop("Indirect"),
    "generated": tr_noop("Generated"), "transient": tr_noop("Transient"), "alias": tr_noop("Alias"),
    "linked": tr_noop("Linked"), "linked-runtime": tr_noop("Linked (runtime)"),
    "bad": tr_noop("Broken"), "": "—",
}


@dataclass
class Unit:
    name: str
    description: str = ""
    load: str = ""
    active: str = "inactive"
    sub: str = ""
    file_state: str = ""
    preset: str = ""
    user: bool = False

    @property
    def active_label(self) -> str:
        return tr(ACTIVE_LABELS.get(self.active, self.active))

    @property
    def file_state_label(self) -> str:
        return tr(FILE_STATE_LABELS.get(self.file_state, self.file_state))

    @property
    def can_toggle_boot(self) -> bool:
        return self.file_state in ("enabled", "disabled", "enabled-runtime", "linked")


def _base(user: bool) -> list[str]:
    return ["systemctl", "--user"] if user else ["systemctl"]


def parse_list_units(text: str) -> dict[str, Unit]:
    units = {}
    for line in text.splitlines():
        line = line.strip().lstrip("●").strip()
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        units[parts[0]] = Unit(name=parts[0], load=parts[1], active=parts[2], sub=parts[3],
                               description=parts[4] if len(parts) > 4 else "")
    return units


def parse_unit_files(text: str) -> dict[str, tuple[str, str]]:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(".service"):
            out[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")
    return out


def list_units(user: bool = False) -> list[Unit]:
    flags = ["--type=service", "--no-legend", "--no-pager", "--plain"]
    loaded = run(_base(user) + ["list-units", "--all"] + flags, timeout=30)
    files = run(_base(user) + ["list-unit-files"] + flags, timeout=30)
    units = parse_list_units(loaded.out) if loaded.ok else {}
    file_states = parse_unit_files(files.out) if files.ok else {}
    for name, (state, preset) in file_states.items():
        if name.endswith("@.service"):
            continue  # template units cannot be started on their own
        unit = units.setdefault(name, Unit(name=name))
        unit.file_state, unit.preset = state, preset
    for unit in units.values():
        unit.user = user
        if unit.load == "not-found" and not unit.file_state:
            unit.file_state = ""
    return sorted((u for u in units.values() if u.load != "not-found" or u.active != "inactive"),
                  key=lambda u: u.name.lower())


def action_cmd(unit: Unit | str, action: str, user: bool = False) -> list[str]:
    name = unit.name if isinstance(unit, Unit) else unit
    if isinstance(unit, Unit):
        user = unit.user
    if action not in ACTIONS or not UNIT_RE.match(name):
        raise ValueError(tr("Invalid action: {action} {name}").format(action=action, name=name))
    # enable/disable only change boot behaviour; the live state has its own buttons.
    return _base(user) + [action, name]


def journal(unit: Unit, lines: int = 300) -> str:
    cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(lines)]
    cmd += ["--user-unit", unit.name] if unit.user else ["-u", unit.name]
    res = run(cmd, timeout=30, locale_c=False)
    text = res.out.strip()
    if not text:
        return res.err.strip() or tr("No journal entries for this service (or you lack permission to read them).")
    return text


def unit_file(unit: Unit) -> str:
    res = run(_base(unit.user) + ["cat", unit.name], timeout=20)
    return res.out.strip() or res.err.strip()


def status_text(unit: Unit) -> str:
    res = run(_base(unit.user) + ["status", "--no-pager", "-n", "0", unit.name], timeout=20,
              locale_c=False)
    return res.out.strip() or res.err.strip()
