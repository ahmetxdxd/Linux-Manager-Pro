"""Shared helpers: running commands, sizes, paths, Trash."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from lmp import APP_ID
from lmp.i18n import tr

HOME = Path.home()


# --------------------------------------------------------------------------- #
# Running commands
# --------------------------------------------------------------------------- #

def c_env(extra: dict | None = None) -> dict:
    """Environment that yields parseable (English, fixed-format) output."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    if extra:
        env.update(extra)
    return env


@dataclass
class CmdResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def run(cmd: Sequence[str], *, timeout: float | None = 120, input: str | None = None,
        locale_c: bool = True) -> CmdResult:
    """Runs a command without ever raising. A missing program yields code 127."""
    try:
        p = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=timeout, input=input,
            env=c_env() if locale_c else None, errors="replace",
        )
        return CmdResult(p.returncode, p.stdout, p.stderr)
    except FileNotFoundError:
        return CmdResult(127, "", tr("{cmd}: command not found").format(cmd=cmd[0]))
    except subprocess.TimeoutExpired:
        return CmdResult(124, "", tr("{cmd}: timed out").format(cmd=cmd[0]))


def have(program: str) -> bool:
    return shutil.which(program) is not None


# --------------------------------------------------------------------------- #
# Sizes and formatting
# --------------------------------------------------------------------------- #

_UNITS = ["B", "KB", "MB", "GB", "TB"]


def format_size(num: int | float | None) -> str:
    if num is None or num < 0:
        return "—"
    val = float(num)
    for unit in _UNITS:
        if val < 1024 or unit == _UNITS[-1]:
            return f"{int(val)} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} TB"


_SIZE_MULT = {
    "b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4, "bytes": 1,
}


def parse_size(text: str) -> int:
    """'12.5 MiB', '3,1 GB', '940 bytes' → bytes. Returns 0 when unparseable."""
    parts = text.strip().replace(",", ".").replace("\xa0", " ").split()
    if len(parts) != 2:
        return 0
    try:
        value = float(parts[0])
    except ValueError:
        return 0
    return int(value * _SIZE_MULT.get(parts[1].lower(), 0))


def dir_size(path: Path | str) -> int:
    """Total size of a directory; does not follow symlinks and skips unreadable entries."""
    path = str(path)
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def format_date(dt: datetime | None) -> str:
    return dt.strftime("%d.%m.%Y") if dt else "—"


def tilde(path: Path | str) -> str:
    """/home/user/.config/x → ~/.config/x"""
    p = str(path)
    home = str(HOME)
    if p == home:
        return "~"
    if p.startswith(home + "/"):
        return "~" + p[len(home):]
    return p


def untilde(path: str) -> Path:
    return Path(os.path.expanduser(path))


# --------------------------------------------------------------------------- #
# App directories and JSON
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(HOME / ".config")
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(HOME / ".local/share")
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(HOME / ".cache")
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    """Atomic write: temp file first, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# System info
# --------------------------------------------------------------------------- #

def os_release() -> dict:
    info: dict[str, str] = {}
    for candidate in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in Path(candidate).read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    info[k.strip()] = v.strip().strip('"')
            break
        except OSError:
            continue
    return info


def is_arch_based() -> bool:
    info = os_release()
    ids = {info.get("ID", "")} | set(info.get("ID_LIKE", "").split())
    return "arch" in ids or have("pacman")


def current_desktops() -> list[str]:
    return [d for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]


# --------------------------------------------------------------------------- #
# Trash (freedesktop.org Trash specification)
# --------------------------------------------------------------------------- #

def move_to_trash(path: Path | str) -> None:
    """Moves a file/folder to the Trash. Raises OSError on failure."""
    path = Path(path)
    if not os.path.lexists(path):
        return
    if have("gio"):
        res = run(["gio", "trash", str(path)], locale_c=False)
        if res.ok:
            return
    _trash_manual(path)


def _trash_manual(path: Path) -> None:
    trash = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / "Trash"
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    base = path.name
    name, n = base, 1
    while (files / name).exists() or (info / f"{name}.trashinfo").exists():
        n += 1
        name = f"{base}.{n}"
    (info / f"{name}.trashinfo").write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(path.absolute()))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    try:
        shutil.move(str(path), str(files / name))
    except OSError:
        (info / f"{name}.trashinfo").unlink(missing_ok=True)
        raise


def remove_path(path: Path | str) -> None:
    """Permanent deletion (does not follow symlinks)."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def unique(items: Iterable) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
