"""Everything that starts at login, and boot-time analysis.

Three kinds of entries are listed:

* ``app``     – XDG autostart entries (~/.config/autostart, /etc/xdg/autostart). System entries
  are never modified; to disable one, a copy with ``Hidden=true`` is written to
  ~/.config/autostart (the XDG standard, and what the KDE and GNOME settings do as well).
* ``service`` – systemd user services the user enabled (``systemctl --user enable``).
* ``wm``      – start commands of tiling window managers (Hyprland ``exec-once``, Sway/i3
  ``exec``). Disabling comments the line out with a marker, so it can be turned back on.

Apps such as Discord or Steam rewrite their own autostart file every time they run, which
silently undoes a "disable". Entries switched off here are therefore remembered, and a small
systemd user path unit ("guard") puts ``Hidden=true`` back as soon as such an app rewrites
its file.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from lmp.core import desktop
from lmp.core.util import HOME, current_desktops, data_dir, load_json, move_to_trash, run, save_json
from lmp.i18n import tr

APP, SERVICE, WM = "app", "service", "wm"
GUARD_UNIT = "lmp-autostart-guard"
_OFF_MARK = "#lmp-off# "


def user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(HOME / ".config")
    return Path(base) / "autostart"


def system_dirs() -> list[Path]:
    dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    return [Path(d) / "autostart" for d in dirs.split(":") if d]


def systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(HOME / ".config")
    return Path(base) / "systemd" / "user"


@dataclass
class AutostartItem:
    desktop_id: str             # stable id: .desktop name, unit name or "wm:<file>:<command>"
    name: str
    command: str
    comment: str
    icon: str
    enabled: bool
    origin: str                 # "user" (the user's own entry) / "system"
    user_path: Path | None      # file in ~/.config/autostart (if any) / the WM config file
    system_path: Path | None    # file in /etc/xdg/autostart (if any)
    desktop_mismatch: bool = False   # will not run on this desktop (OnlyShowIn, other WM…)
    kind: str = APP
    source: str = ""            # what to show as the origin (e.g. "hyprland.conf")
    readonly: bool = False      # found by `inspect_items()`; cannot be switched here
    running: bool | None = None  # None = not checked / unknown
    runner: str = ""            # systemd unit (or scope) the running program belongs to
    other_start: bool = False   # running through a session service, not its own autostart entry

    @property
    def origin_label(self) -> str:
        if self.kind == SERVICE:
            return tr("System service") if self.origin == "system" else tr("Background service")
        if self.kind == WM:
            return self.source
        return tr("User") if self.origin == "user" else tr("System")


def _shown_here(entry: desktop.DesktopEntry, desktops: list[str]) -> bool:
    if not desktops:
        return True
    if entry.only_show_in and not set(entry.only_show_in) & set(desktops):
        return False
    if entry.not_show_in and set(entry.not_show_in) & set(desktops):
        return False
    return True


# --------------------------------------------------------------------------- #
# XDG autostart entries
# --------------------------------------------------------------------------- #

def _app_items(desktops: list[str]) -> list[AutostartItem]:
    system: dict[str, Path] = {}
    for d in reversed(system_dirs()):
        for f in d.glob("*.desktop"):
            system[f.name] = f
    user = {f.name: f for f in user_dir().glob("*.desktop")} if user_dir().is_dir() else {}

    items = []
    for desktop_id in sorted(set(system) | set(user)):
        user_path = user.get(desktop_id)
        system_path = system.get(desktop_id)
        effective = desktop.parse(user_path or system_path)
        if effective is None:
            continue
        # If the user copy only holds Hidden=true, take name/command from the system entry
        base = desktop.parse(system_path) if system_path else effective
        enabled = not effective.hidden and effective.gnome_autostart_enabled
        items.append(AutostartItem(
            desktop_id=desktop_id,
            name=effective.name or (base.name if base else desktop_id),
            command=effective.exec or (base.exec if base else ""),
            comment=effective.comment or (base.comment if base else ""),
            icon=effective.icon or (base.icon if base else ""),
            enabled=enabled,
            origin="system" if system_path else "user",
            user_path=user_path, system_path=system_path,
            desktop_mismatch=not _shown_here(effective, desktops),
        ))
    return items


def _set_app_enabled(item: AutostartItem, enabled: bool) -> None:
    target = user_dir() / item.desktop_id
    user_dir().mkdir(parents=True, exist_ok=True)
    if item.user_path and item.user_path.exists():
        text = item.user_path.read_text(encoding="utf-8", errors="replace")
    elif item.system_path:
        text = item.system_path.read_text(encoding="utf-8", errors="replace")
    else:
        raise FileNotFoundError(item.desktop_id)
    text = desktop.set_key(text, "Hidden", None if enabled else "true")
    text = desktop.set_key(text, "X-GNOME-Autostart-enabled", None if enabled else "false")
    _remember_blocked(item.desktop_id, not enabled)
    if enabled and item.system_path and item.system_path.exists():
        # If the user copy became identical to the system one, remove the copy
        system_text = item.system_path.read_text(encoding="utf-8", errors="replace")
        if _normalized(text) == _normalized(system_text):
            if target.exists():
                target.unlink()
            return
    target.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# systemd user services
# --------------------------------------------------------------------------- #

def _services_path() -> Path:
    return data_dir() / "autostart-services.json"


def _service_items() -> list[AutostartItem]:
    """Services the user enabled themselves (links in ~/.config/systemd/user/*.wants), plus
    those switched off here (so they can be switched back on)."""
    enabled: dict[str, Path] = {}
    base = systemd_user_dir()
    if base.is_dir():
        for wants in base.glob("*.wants"):
            for link in wants.iterdir():
                if link.name.endswith(".service") and not link.name.startswith(GUARD_UNIT):
                    enabled.setdefault(link.name, link)
    disabled = [u for u in load_json(_services_path(), []) if u not in enabled]
    items = []
    for unit in sorted(set(enabled) | set(disabled)):
        description = ""
        unit_file = enabled.get(unit)
        try:
            text = unit_file.resolve().read_text(errors="replace") if unit_file else ""
        except OSError:
            text = ""
        m = re.search(r"^Description=(.+)$", text, re.M)
        if m:
            description = m.group(1).strip()
        items.append(AutostartItem(
            desktop_id=unit, name=description or unit[:-8], command=unit, comment="",
            icon="", enabled=unit in enabled, origin="user", user_path=None, system_path=None,
            kind=SERVICE, source=unit,
        ))
    return items


def _set_service_enabled(item: AutostartItem, enabled: bool) -> None:
    if not re.match(r"^[A-Za-z0-9:_.@-]+\.service$", item.desktop_id):
        raise ValueError(tr("Invalid action: {action} {name}").format(
            action="enable" if enabled else "disable", name=item.desktop_id))
    res = run(["systemctl", "--user", "enable" if enabled else "disable", item.desktop_id],
              timeout=30)
    if not res.ok:
        raise OSError(res.err.strip() or res.out.strip() or "systemctl")
    remembered = set(load_json(_services_path(), []))
    if enabled:
        remembered.discard(item.desktop_id)
    else:
        remembered.add(item.desktop_id)
    save_json(_services_path(), sorted(remembered))


# --------------------------------------------------------------------------- #
# Window-manager start commands (Hyprland exec-once, Sway/i3 exec)
# --------------------------------------------------------------------------- #

@dataclass
class _WmSource:
    desktop: str                 # value in XDG_CURRENT_DESKTOP
    label: str
    pattern: re.Pattern          # group 1 = the command

    def files(self) -> list[Path]:
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
        if self.desktop == "Hyprland":
            return sorted((cfg / "hypr").glob("*.conf"))
        folder = cfg / self.desktop.lower()
        out = [folder / "config"] if (folder / "config").is_file() else []
        if (folder / "config.d").is_dir():
            out += sorted(p for p in (folder / "config.d").iterdir() if p.is_file())
        return out


WM_SOURCES = [
    _WmSource("Hyprland", "Hyprland", re.compile(r"^\s*exec-once\s*=\s*(.+?)\s*$")),
    _WmSource("sway", "Sway", re.compile(r"^\s*exec\s+(.+?)\s*$")),
    _WmSource("i3", "i3", re.compile(r"^\s*exec\s+(.+?)\s*$")),
]


def wm_command_name(command: str) -> str:
    """'[workspace 2 silent] uwsm app -- firefox --x' → 'firefox'"""
    text = re.sub(r"^\[[^\]]*\]\s*", "", command.strip())
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    skip = {"uwsm", "app", "--", "env", "exec", "nohup", "setsid", "runapp"}
    for t in tokens:
        if t.startswith("-") or t in skip or "=" in t.split("/")[-1]:
            continue
        if t in ("sh", "bash", "zsh"):
            return text
        return os.path.basename(t)
    return text


def _parse_wm_line(source: _WmSource, line: str) -> tuple[str, bool] | None:
    """(command, enabled) for a start line, or None."""
    body = line.lstrip()
    enabled = True
    if body.startswith(_OFF_MARK):
        body = body[len(_OFF_MARK):]
        enabled = False
    elif body.startswith("#"):
        return None
    m = source.pattern.match(body)
    return (m.group(1), enabled) if m else None


def _wm_items(desktops: list[str]) -> list[AutostartItem]:
    items = []
    for source in WM_SOURCES:
        for path in source.files():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                parsed = _parse_wm_line(source, line)
                if not parsed:
                    continue
                command, enabled = parsed
                items.append(AutostartItem(
                    desktop_id=f"wm:{path}:{command}", name=wm_command_name(command),
                    command=command, comment="", icon=wm_command_name(command).split()[0],
                    enabled=enabled, origin="user", user_path=path, system_path=None,
                    desktop_mismatch=bool(desktops) and source.desktop not in desktops,
                    kind=WM, source=f"{source.label} · {path.name}",
                ))
    return items


def _set_wm_enabled(item: AutostartItem, enabled: bool) -> None:
    path = item.user_path
    source = next((s for s in WM_SOURCES if path in s.files()), None)
    if path is None or source is None:
        raise FileNotFoundError(item.desktop_id)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        parsed = _parse_wm_line(source, line.rstrip("\n"))
        if not parsed or parsed[0] != item.command:
            continue
        indent = line[:len(line) - len(line.lstrip())]
        body = line.lstrip()
        if enabled and body.startswith(_OFF_MARK):
            lines[i] = indent + body[len(_OFF_MARK):]
        elif not enabled and not body.startswith(_OFF_MARK):
            lines[i] = indent + _OFF_MARK + body
        else:
            return
        backup = path.with_name(path.name + ".lmp-backup")
        if not backup.exists():
            shutil.copy2(path, backup)
        tmp = path.with_name(path.name + ".lmp-tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
        return
    raise FileNotFoundError(item.desktop_id)


def xdg_entries_run_here() -> bool:
    """Tiling window managers do not start XDG autostart entries by themselves; they only
    run there when the session uses uwsm or the config starts `dex`."""
    desktops = current_desktops()
    tiling = {s.desktop for s in WM_SOURCES}
    if not desktops or not set(desktops) & tiling:
        return True
    if any(k.startswith("UWSM") for k in os.environ):
        return True
    return any(i.enabled and re.search(r"\b(dex|uwsm|systemd-xdg-autostart)", i.command)
               for i in _wm_items(desktops) if not i.desktop_mismatch)


# --------------------------------------------------------------------------- #
# Combined API
# --------------------------------------------------------------------------- #

def _sorted(items: list[AutostartItem]) -> list[AutostartItem]:
    order = {APP: 0, WM: 1, SERVICE: 2}
    return sorted(items, key=lambda i: (order[i.kind], i.kind == APP and i.origin != "user",
                                        i.name.lower()))


def list_items() -> list[AutostartItem]:
    """Everything configured to start, read from files only (no system queries)."""
    desktops = current_desktops()
    return _sorted(_app_items(desktops) + _service_items() + _wm_items(desktops))


def set_enabled(item: AutostartItem, enabled: bool) -> None:
    if item.readonly:
        raise PermissionError(tr("This system service cannot be switched off from here."))
    if item.kind == SERVICE:
        _set_service_enabled(item, enabled)
    elif item.kind == WM:
        _set_wm_enabled(item, enabled)
    else:
        _set_app_enabled(item, enabled)


def _snapshot_path() -> Path:
    return data_dir() / "autostart-disabled.json"


def disable_all(items: list[AutostartItem]) -> list[str]:
    """Turns off every application entry that is currently on and would run on this desktop.

    Window-manager commands and background services are left alone: on a tiling WM they
    usually start the bar, wallpaper or notifications, and switching them all off at once
    would leave an unusable desktop.
    The ids are remembered, so `restore_disabled()` can undo exactly this one-click action
    (entries you had already switched off yourself stay off).
    """
    changed: list[str] = []
    for item in items:
        if item.kind == APP and item.enabled and not item.desktop_mismatch:
            set_enabled(item, False)
            changed.append(item.desktop_id)
    previous = load_json(_snapshot_path(), [])
    save_json(_snapshot_path(), sorted(set(previous) | set(changed)))
    return changed


def disabled_snapshot() -> list[str]:
    """Entries a previous `disable_all()` switched off and that are still off."""
    ids = load_json(_snapshot_path(), [])
    if not ids:
        return []
    still_off = {i.desktop_id for i in list_items() if not i.enabled}
    return [i for i in ids if i in still_off]


def restore_disabled() -> int:
    """Turns back on what `disable_all()` switched off. Returns how many were restored."""
    wanted = set(load_json(_snapshot_path(), []))
    restored = 0
    for item in list_items():
        if item.desktop_id in wanted and not item.enabled:
            set_enabled(item, True)
            restored += 1
    save_json(_snapshot_path(), [])
    return restored


def _normalized(text: str) -> list[str]:
    return sorted(line.strip() for line in text.splitlines() if line.strip())


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "entry"


def add_item(name: str, command: str, comment: str = "", delay: int = 0) -> Path:
    if not name.strip() or not command.strip():
        raise ValueError(tr("Name and command cannot be empty."))
    user_dir().mkdir(parents=True, exist_ok=True)
    path = user_dir() / f"{_slug(name)}.desktop"
    n = 2
    while path.exists():
        path = user_dir() / f"{_slug(name)}-{n}.desktop"
        n += 1
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name.strip()}",
        f"Exec={command.strip()}",
    ]
    if comment.strip():
        lines.append(f"Comment={comment.strip()}")
    if delay > 0:
        lines.append(f"X-GNOME-Autostart-Delay={delay}")
        # KDE ignores the delay key, so wrap the command in sleep
        lines[3] = f"Exec=sh -c {shlex.quote(f'sleep {delay}; exec {command.strip()}')}"
    lines.append("X-LMP-Created=true")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def remove_item(item: AutostartItem) -> None:
    """Only the user's own entries can be deleted (moved to the Trash)."""
    if item.kind != APP or item.origin != "user" or not item.user_path:
        raise PermissionError(tr("System entries cannot be deleted, only disabled."))
    _remember_blocked(item.desktop_id, False)
    move_to_trash(item.user_path)


# --------------------------------------------------------------------------- #
# Guard: keeps disabled entries disabled even when an app rewrites its file
# --------------------------------------------------------------------------- #

def _blocked_path() -> Path:
    return data_dir() / "autostart-blocked.json"


def blocked_ids() -> list[str]:
    return list(load_json(_blocked_path(), []))


def _remember_blocked(desktop_id: str, blocked: bool) -> None:
    ids = set(blocked_ids())
    before = set(ids)
    if blocked:
        ids.add(desktop_id)
    else:
        ids.discard(desktop_id)
    if ids != before:
        save_json(_blocked_path(), sorted(ids))


# The guard runs as its own small script so it works no matter how (or whether) Linux
# Manager Pro is installed. It only adds Hidden=true; it never enables or deletes anything.
GUARD_SCRIPT = r'''#!/usr/bin/python3 -I
"""Linux Manager Pro autostart guard (generated, do not edit).
Keeps the startup entries you switched off in Linux Manager Pro switched off, even when
the application rewrites its own autostart file."""
import json, os, sys

def hide(text):
    out, in_main, seen, done = [], False, False, set()
    want = {"Hidden": "true", "X-GNOME-Autostart-enabled": "false"}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_main:
                out += [k + "=" + v for k, v in want.items() if k not in done]
                done.update(want)
            in_main = s == "[Desktop Entry]"
            seen = seen or in_main
            out.append(line)
            continue
        key = s.split("=", 1)[0].strip() if "=" in s else None
        if in_main and key in want:
            if key not in done:
                out.append(key + "=" + want[key])
                done.add(key)
            continue
        out.append(line)
    if in_main:
        out += [k + "=" + v for k, v in want.items() if k not in done]
    if not seen:
        return text
    return "\n".join(out) + "\n"

def main(blocked_file, folder):
    try:
        with open(blocked_file, encoding="utf-8") as f:
            ids = json.load(f)
    except (OSError, ValueError):
        return 0
    for name in ids:
        if not isinstance(name, str) or "/" in name or not name.endswith(".desktop"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        new = hide(text)
        if new != text:
            tmp = path + ".lmp-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new)
            os.replace(tmp, path)
            print("kept disabled:", name)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
'''


def guard_script_path() -> Path:
    return data_dir() / "autostart-guard.py"


def _python() -> str:
    return "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def _quote_unit_arg(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def enforce_blocked() -> None:
    """Applies the guard once in-process (e.g. when the Startup page opens)."""
    namespace: dict = {}
    exec(compile(GUARD_SCRIPT, "autostart-guard", "exec"), namespace)   # noqa: S102 - our own code
    namespace["main"](str(_blocked_path()), str(user_dir()))


def sync_guard() -> bool:
    """Installs the guard while something is blocked, removes it when nothing is.
    Returns True when the guard is active. Missing systemd is not an error."""
    units = systemd_user_dir()
    path_unit = units / f"{GUARD_UNIT}.path"
    service_unit = units / f"{GUARD_UNIT}.service"
    if not shutil.which("systemctl"):
        return False
    if not blocked_ids():
        if path_unit.exists():
            run(["systemctl", "--user", "disable", "--now", path_unit.name], timeout=20)
            path_unit.unlink(missing_ok=True)
            service_unit.unlink(missing_ok=True)
            run(["systemctl", "--user", "daemon-reload"], timeout=20)
        return False
    script = guard_script_path()
    if not script.exists() or script.read_text(encoding="utf-8") != GUARD_SCRIPT:
        script.write_text(GUARD_SCRIPT, encoding="utf-8")
    user_dir().mkdir(parents=True, exist_ok=True)
    path_text = (
        "[Unit]\nDescription=Linux Manager Pro: keep disabled startup entries off\n\n"
        f"[Path]\nPathModified={user_dir()}\nUnit={service_unit.name}\n\n"
        "[Install]\nWantedBy=default.target\n")
    service_text = (
        "[Unit]\nDescription=Linux Manager Pro: keep disabled startup entries off\n\n"
        "[Service]\nType=oneshot\n"
        f"ExecStart={_python()} -I {_quote_unit_arg(str(script))} "
        f"{_quote_unit_arg(str(_blocked_path()))} {_quote_unit_arg(str(user_dir()))}\n")
    changed = False
    units.mkdir(parents=True, exist_ok=True)
    for f, text in ((path_unit, path_text), (service_unit, service_text)):
        if not f.exists() or f.read_text(encoding="utf-8") != text:
            f.write_text(text, encoding="utf-8")
            changed = True
    if changed:
        run(["systemctl", "--user", "daemon-reload"], timeout=20)
        run(["systemctl", "--user", "enable", "--now", path_unit.name], timeout=20)
    return True


# --------------------------------------------------------------------------- #
# What is really running, and what else starts at login
#
# An entry switched off above only disables its own .desktop file. The same program may be
# started by a systemd user service that the desktop pulls in itself (Plasma 6 starts kaccess,
# powerdevil, the polkit agent… as `plasma-*.service` through plasma-workspace.target). The
# functions below look at the running system (read-only) to show that.
# --------------------------------------------------------------------------- #

# Targets whose Wants=/Requires= list the units the session starts by itself. plasma-core.target
# (the shell and the compositor) is left out on purpose.
SESSION_TARGETS = ("default.target", "graphical-session.target", "plasma-workspace.target")
_UNIT_TYPES = (".service", ".timer", ".path")
_SHELLS = {"sh", "bash", "dash", "zsh", "fish"}
_GENERIC = _SHELLS | {"env", "python", "python3", "perl", "ruby", "node", "java", "sleep",
                      "nohup", "setsid", "systemd-run", "dbus-launch", "dbus-run-session",
                      "gtk-launch", "xdg-open"}
_INTERPRETERS = _SHELLS | {"python", "python3", "perl", "ruby", "node", "java"}


@dataclass
class UnitInfo:
    name: str
    description: str = ""
    load: str = ""
    active: str = ""
    sub: str = ""
    fragment: str = ""

    @property
    def running(self) -> bool:
        """Active and still doing something (a finished one-shot service is not 'running')."""
        return self.active in ("active", "activating", "reloading") and self.sub != "exited"


@dataclass
class ProcInfo:
    comm: str                    # /proc/<pid>/comm (the kernel cuts it at 15 characters)
    names: frozenset[str]        # comm, argv[0], the executable and (for scripts) the script
    unit: str                    # systemd unit/scope the process lives in ("" = unknown)
    cgroup: str


@dataclass
class Snapshot:
    units: dict[str, UnitInfo]
    pulled_by: dict[str, str]    # unit → session target (or "enabled") that starts it at login
    procs: list[ProcInfo]

    def is_session_unit(self, unit: str) -> bool:
        return unit in self.pulled_by and not unit.startswith("app-")

    def find_process(self, binary: str) -> ProcInfo | None:
        if not binary:
            return None
        is_app_id = "." in binary and "/" not in binary          # flatpak application id
        found = []
        for p in self.procs:
            if (binary in p.names or (len(binary) > 15 and binary[:15] == p.comm)
                    or (is_app_id and f"flatpak-{binary}" in p.cgroup)):
                found.append(p)
        # prefer a match that belongs to a session service: that is the one that explains
        # why a program keeps starting
        return next((p for p in found if self.is_session_unit(p.unit)), found[0] if found else None)


def _unit_escape(text: str) -> str:
    """Same as `systemd-escape`."""
    out = []
    for i, ch in enumerate(text):
        if ch == "/":
            out.append("-")
        elif ch.isascii() and (ch.isalnum() or ch in ":_" or (ch == "." and i > 0)):
            out.append(ch)
        else:
            out.append("".join(f"\\x{b:02x}" for b in ch.encode("utf-8")))
    return "".join(out)


def app_unit_name(desktop_id: str) -> str:
    """The unit systemd's xdg-autostart generator makes for an autostart entry."""
    stem = desktop_id[:-len(".desktop")] if desktop_id.endswith(".desktop") else desktop_id
    return f"app-{_unit_escape(stem)}@autostart.service"


def parse_show(text: str) -> dict[str, dict[str, str]]:
    """`systemctl show a b c` output → {unit id: {property: value}}."""
    out: dict[str, dict[str, str]] = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        props = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        if props.get("Id"):
            out[props["Id"]] = props
    return out


def _startable(unit: str) -> bool:
    return (unit.endswith(_UNIT_TYPES) and not unit.startswith(GUARD_UNIT)
            and not unit.startswith("app-"))


def _cgroup_unit(path: str) -> str:
    """Nearest systemd unit/scope in a cgroup path (not the user manager itself)."""
    for part in reversed(path.strip("/").split("/")):
        if part.endswith((".service", ".scope")) and not part.startswith("user@") \
                and part != "init.scope":
            return part
    return ""


def scan_processes(proc_root: Path | str = "/proc") -> list[ProcInfo]:
    """The current user's processes. Never raises: processes that vanish are skipped."""
    root = Path(proc_root)
    uid = os.getuid()
    out = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            comm = (entry / "comm").read_text(errors="replace").strip()
            argv = [a.decode(errors="replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
            cgroup = (entry / "cgroup").read_text(errors="replace")
        except OSError:
            continue
        try:
            exe = os.path.basename(os.readlink(entry / "exe")).removesuffix(" (deleted)")
        except OSError:
            exe = ""
        names = {comm, exe} - {""}
        if argv:
            names.add(os.path.basename(argv[0].split(" ")[0]))
            if (exe in _INTERPRETERS or exe.startswith("python")) and len(argv) > 1 \
                    and not argv[1].startswith("-"):
                names.add(os.path.basename(argv[1]))      # a script run by its interpreter
        path = ""
        for line in cgroup.splitlines():
            if line.startswith("0::") or "name=systemd" in line:
                path = line.split(":", 2)[-1]
                break
        out.append(ProcInfo(comm=comm, names=frozenset(names), unit=_cgroup_unit(path),
                            cgroup=path))
    return out


def take_snapshot(extra_units=(), proc_root: Path | str = "/proc") -> Snapshot:
    """Asks systemd which units the session starts and what state some units are in. A missing
    or unreachable systemd just gives an empty answer."""
    pulled_by: dict[str, str] = {}
    res = run(["systemctl", "--user", "show", "-p", "Id", "-p", "Wants", "-p", "Requires",
               *SESSION_TARGETS], timeout=20)
    if res.ok:
        for target, props in parse_show(res.out).items():
            for unit in f"{props.get('Wants', '')} {props.get('Requires', '')}".split():
                if _startable(unit):
                    pulled_by.setdefault(unit, target)
    res = run(["systemctl", "--user", "list-unit-files", "--state=enabled,enabled-runtime",
               "--type=service,timer,path", "--no-legend", "--plain", "--no-pager"], timeout=20)
    if res.ok:
        for line in res.out.splitlines():
            unit = line.split(None, 1)[0] if line.strip() else ""
            if _startable(unit):
                pulled_by.setdefault(unit, "enabled")

    wanted = sorted(set(pulled_by) | set(extra_units))
    units: dict[str, UnitInfo] = {}
    if wanted:
        res = run(["systemctl", "--user", "show", "-p", "Id", "-p", "Description",
                   "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                   "-p", "FragmentPath", *wanted], timeout=20)
        if res.ok:
            for name, props in parse_show(res.out).items():
                units[name] = UnitInfo(
                    name=name, description=props.get("Description", ""),
                    load=props.get("LoadState", ""), active=props.get("ActiveState", ""),
                    sub=props.get("SubState", ""), fragment=props.get("FragmentPath", ""))
    return Snapshot(units, pulled_by, scan_processes(proc_root))


def _item_binary(command: str) -> str:
    """The program an entry starts ('' when it cannot be told, e.g. a bare `sh -c`)."""
    text = re.sub(r"^\[[^\]]*\]\s*", "", command.strip())
    binary = desktop.exec_binary(text)
    if binary in _SHELLS:
        try:
            tokens = shlex.split(text)
        except ValueError:
            return ""
        if "-c" not in tokens or tokens.index("-c") + 1 >= len(tokens):
            return ""
        # `sleep 3 && arch-update --tray` → the last real command
        for part in reversed(re.split(r"&&|\|\||;|\|", tokens[tokens.index("-c") + 1])):
            inner = desktop.exec_binary(re.sub(r"^(exec|nohup|setsid)\s+", "", part.strip()))
            if inner and inner not in _GENERIC:
                return inner
        return ""
    return "" if binary in _GENERIC else binary


def _wm_binary(command: str) -> str:
    name = wm_command_name(command)
    return "" if " " in name or name in _GENERIC else name


def inspect_items(items: list[AutostartItem], snapshot: Snapshot | None = None
                  ) -> list[AutostartItem]:
    """Fills in what is running (`running`, `runner`, `other_start`) and adds the systemd user
    units the session starts by itself, as read-only entries. Queries the live system, so call
    it from a background thread."""
    if snapshot is None:
        extra = [app_unit_name(i.desktop_id) if i.kind == APP else i.desktop_id
                 for i in items if i.kind in (APP, SERVICE)]
        snapshot = take_snapshot(extra)
    snap = snapshot

    for item in items:
        if item.kind == SERVICE:
            info = snap.units.get(item.desktop_id)
            if info:
                item.running = info.running
                item.runner = item.desktop_id if info.running else ""
            continue
        own = ""
        if item.kind == APP:
            own = app_unit_name(item.desktop_id)
            info = snap.units.get(own)
            if info and info.running:
                item.running, item.runner = True, own
                continue
            binary = _item_binary(item.command)
        else:
            binary = _wm_binary(item.command)
        proc = snap.find_process(binary)
        item.running = proc is not None if binary else None
        if proc:
            item.runner = proc.unit
            item.other_start = snap.is_session_unit(proc.unit) and proc.unit != own

    known = {i.desktop_id for i in items if i.kind == SERVICE}
    extra_items = []
    for unit, pulled in sorted(snap.pulled_by.items()):
        info = snap.units.get(unit)
        if unit in known or not info or info.load != "loaded":
            continue
        fragment = Path(info.fragment) if info.fragment else None
        mine = fragment is not None and systemd_user_dir() in fragment.parents
        extra_items.append(AutostartItem(
            desktop_id=unit, name=info.description or unit.rsplit(".", 1)[0],
            command=unit, comment=f"{unit} · {pulled}", icon="", enabled=True,
            origin="user" if mine else "system", user_path=None, system_path=fragment,
            kind=SERVICE, source=pulled, readonly=True, running=info.running,
            runner=unit if info.running else ""))
    return _sorted(items + extra_items)


# --------------------------------------------------------------------------- #
# Boot time
# --------------------------------------------------------------------------- #

@dataclass
class BootReport:
    summary: str
    total_seconds: float | None
    blame: list[tuple[str, float]]


def _to_seconds(text: str) -> float:
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)\s*(min|ms|us|h|s)", text):
        v = float(value)
        total += {"h": 3600, "min": 60, "s": 1, "ms": 0.001, "us": 0.000001}[unit] * v
    return total


def parse_blame(text: str) -> list[tuple[str, float]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        duration, unit = parts
        out.append((unit, _to_seconds(duration)))
    return out


def boot_report(limit: int = 15) -> BootReport | None:
    summary = run(["systemd-analyze"], timeout=20)
    if not summary.ok:
        return None
    first = summary.out.strip().splitlines()[0] if summary.out.strip() else ""
    total = None
    m = re.search(r"=\s*(.+)$", first)
    if m:
        total = _to_seconds(m.group(1))
    blame = run(["systemd-analyze", "blame", "--no-pager"], timeout=20)
    return BootReport(first, total, parse_blame(blame.out)[:limit] if blame.ok else [])
