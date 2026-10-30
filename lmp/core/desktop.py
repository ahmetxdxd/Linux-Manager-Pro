"""Reading and line-based (comment-preserving) editing of .desktop files."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from lmp.core.util import HOME

_FIELD_CODES = ("%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m")


@dataclass
class DesktopEntry:
    path: Path
    name: str = ""
    generic_name: str = ""
    comment: str = ""
    exec: str = ""
    try_exec: str = ""
    icon: str = ""
    wm_class: str = ""
    categories: list[str] = field(default_factory=list)
    no_display: bool = False
    hidden: bool = False
    only_show_in: list[str] = field(default_factory=list)
    not_show_in: list[str] = field(default_factory=list)
    gnome_autostart_enabled: bool = True
    entry_type: str = "Application"

    @property
    def desktop_id(self) -> str:
        return self.path.name

    @property
    def binary(self) -> str:
        """Program name from the Exec line (env, flatpak run and full path stripped)."""
        return exec_binary(self.exec)


def exec_binary(exec_line: str) -> str:
    if not exec_line:
        return ""
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = exec_line.split()
    tokens = [t for t in tokens if t not in _FIELD_CODES]
    # env VAR=1 prog  /  flatpak run com.x.Y  /  sh -c "..."
    while tokens and (tokens[0] == "env" or "=" in tokens[0].split("/")[-1]):
        tokens.pop(0)
    if len(tokens) >= 3 and tokens[0].endswith("flatpak") and tokens[1] == "run":
        rest = [t for t in tokens[2:] if not t.startswith("-")]
        return rest[0] if rest else ""
    return os.path.basename(tokens[0]) if tokens else ""


def _localized_key(key: str) -> tuple[str, str | None]:
    if "[" in key and key.endswith("]"):
        base, loc = key[:-1].split("[", 1)
        return base, loc
    return key, None


def parse(path: Path | str, lang: str | None = None) -> DesktopEntry | None:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lang = lang or (os.environ.get("LANG", "").split(".")[0].split("_")[0] or None)
    values: dict[str, str] = {}
    localized: dict[str, str] = {}
    in_main = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_main = line == "[Desktop Entry]"
            continue
        if not in_main or "=" not in line:
            continue
        key, value = line.split("=", 1)
        base, loc = _localized_key(key.strip())
        if loc is None:
            values.setdefault(base, value.strip())
        elif lang and loc.split("_")[0] == lang:
            localized.setdefault(base, value.strip())

    def get(k: str) -> str:
        return localized.get(k) or values.get(k, "")

    def as_list(k: str) -> list[str]:
        return [x for x in values.get(k, "").split(";") if x]

    return DesktopEntry(
        path=path,
        name=get("Name"),
        generic_name=get("GenericName"),
        comment=get("Comment"),
        exec=values.get("Exec", ""),
        try_exec=values.get("TryExec", ""),
        icon=values.get("Icon", ""),
        wm_class=values.get("StartupWMClass", ""),
        categories=as_list("Categories"),
        no_display=values.get("NoDisplay", "").lower() == "true",
        hidden=values.get("Hidden", "").lower() == "true",
        only_show_in=as_list("OnlyShowIn"),
        not_show_in=as_list("NotShowIn"),
        gnome_autostart_enabled=values.get("X-GNOME-Autostart-enabled", "true").lower() != "false",
        entry_type=values.get("Type", "Application"),
    )


def application_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [data_home / "applications"]
    dirs += [Path(d) / "applications" for d in data_dirs.split(":") if d]
    # Add Flatpak exports even if they are missing from XDG_DATA_DIRS
    dirs += [Path("/var/lib/flatpak/exports/share/applications"),
             data_home / "flatpak/exports/share/applications"]
    out = []
    for d in dirs:
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def index_applications() -> dict[str, DesktopEntry]:
    """desktop_id → DesktopEntry. Priority follows XDG order (first match wins)."""
    result: dict[str, DesktopEntry] = {}
    for d in application_dirs():
        for f in d.glob("*.desktop"):
            if f.name in result:
                continue
            entry = parse(f)
            if entry and entry.name:
                result[f.name] = entry
    return result


def set_key(text: str, key: str, value: str | None) -> str:
    """Sets a key in the [Desktop Entry] section (None → removes it). Other lines are preserved."""
    lines = text.splitlines()
    out: list[str] = []
    in_main = False
    done = False
    main_seen = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_main and not done and value is not None:
                out.append(f"{key}={value}")
                done = True
            in_main = stripped == "[Desktop Entry]"
            main_seen = main_seen or in_main
            out.append(line)
            continue
        if in_main and "=" in stripped and stripped.split("=", 1)[0].strip() == key:
            if value is not None and not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not main_seen:
        out = ["[Desktop Entry]"] + out
        in_main = True
    if in_main and not done and value is not None:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"
