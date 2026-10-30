"""pacman queries. All installed packages are read with a single `pacman -Qi` call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from lmp.core.util import dir_size, have, parse_size, run

NAME_RE = re.compile(r"^[a-zA-Z0-9@._+-]+$")

_DATE_FORMATS = (
    "%a %b %d %H:%M:%S %Y",      # C yereli (%c)
    "%a %d %b %Y %I:%M:%S %p %Z",  # en_US
    "%a %d %b %Y %I:%M:%S %p",
    "%a %d %b %Y %H:%M:%S %Z",
    "%a %d %b %Y %H:%M:%S",
)


def parse_date(text: str) -> datetime | None:
    text = " ".join(text.split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # If the timezone abbreviation is not recognised, drop the last word and retry
    if " " in text:
        head = text.rsplit(" ", 1)[0]
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(head, fmt)
            except ValueError:
                continue
    return None


@dataclass
class Package:
    name: str
    version: str = ""
    description: str = ""
    url: str = ""
    size: int = 0
    install_date: datetime | None = None
    build_date: datetime | None = None
    explicit: bool = True
    foreign: bool = False          # AUR or manually installed (-Qm)
    packager: str = ""
    repository: str = ""           # only filled in -Si output
    depends: list[str] = field(default_factory=list)
    required_by: list[str] = field(default_factory=list)
    optional_for: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)


def _split_list(value: str) -> list[str]:
    value = value.strip()
    if not value or value == "None":
        return []
    return value.split()


def parse_info(text: str) -> list[Package]:
    """Splits `pacman -Qi` / `pacman -Si` output (LC_ALL=C) into packages."""
    packages: list[Package] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        fields: dict[str, str] = {}
        last_key = None
        for line in block.splitlines():
            if not line.strip():
                continue
            if line[:1].isspace() and last_key:
                fields[last_key] += "\n" + line.strip()
                continue
            if " : " in line:
                key, value = line.split(" : ", 1)
                last_key = key.strip()
                fields[last_key] = value.strip()
            elif line.rstrip().endswith(" :"):
                last_key = line.rstrip()[:-2].strip()
                fields[last_key] = ""
        name = fields.get("Name")
        if not name:
            continue
        packages.append(Package(
            name=name,
            version=fields.get("Version", ""),
            description=fields.get("Description", ""),
            url=fields.get("URL", "") if fields.get("URL") != "None" else "",
            size=parse_size(fields.get("Installed Size", "")),
            install_date=parse_date(fields.get("Install Date", "")) if fields.get("Install Date") else None,
            build_date=parse_date(fields.get("Build Date", "")) if fields.get("Build Date") else None,
            explicit=fields.get("Install Reason", "Explicitly").startswith("Explicitly"),
            packager=fields.get("Packager", ""),
            repository=fields.get("Repository", ""),
            depends=_split_list(fields.get("Depends On", "")),
            required_by=_split_list(fields.get("Required By", "")),
            optional_for=_split_list(fields.get("Optional For", "")),
            groups=_split_list(fields.get("Groups", "")),
        ))
    return packages


def available() -> bool:
    return have("pacman")


def installed_packages() -> list[Package]:
    res = run(["pacman", "-Qi"], timeout=300)
    if not res.ok:
        return []
    packages = parse_info(res.out)
    foreign = foreign_names()
    for p in packages:
        p.foreign = p.name in foreign
    return packages


def foreign_names() -> set[str]:
    res = run(["pacman", "-Qqm"])
    return set(res.out.split()) if res.ok else set()


def explicit_native_names() -> list[str]:
    res = run(["pacman", "-Qqen"])
    return res.out.split() if res.ok else []


def explicit_foreign_names() -> list[str]:
    res = run(["pacman", "-Qqem"])
    return res.out.split() if res.ok else []


def installed_versions() -> dict[str, str]:
    res = run(["pacman", "-Q"])
    out: dict[str, str] = {}
    if res.ok:
        for line in res.out.splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[0]] = parts[1]
    return out


def orphans() -> list[str]:
    """Dependencies no package needs anymore (`pacman -Qdtq`)."""
    res = run(["pacman", "-Qdtq"])
    return res.out.split() if res.code == 0 else []


def cache_size() -> int:
    return dir_size("/var/cache/pacman/pkg")


def sync_names() -> set[str]:
    """All package names in the sync repositories."""
    res = run(["pacman", "-Slq"], timeout=60)
    return set(res.out.split()) if res.ok else set()


def sync_info(names: Iterable[str]) -> dict[str, Package]:
    names = [n for n in names if NAME_RE.match(n)]
    if not names:
        return {}
    res = run(["pacman", "-Si", *names], timeout=60)
    return {p.name: p for p in parse_info(res.out)}


def package_files(name: str) -> list[str]:
    res = run(["pacman", "-Qlq", name], timeout=60)
    return [line for line in res.out.splitlines() if line] if res.ok else []


_OWNED_RE = re.compile(r"^(.*) is owned by (\S+) (\S+)$")


def owners(paths: Iterable[str]) -> dict[str, set[str]]:
    """Owning package for each path. Unowned paths are absent from the dict."""
    paths = list(paths)
    result: dict[str, set[str]] = {}
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        res = run(["pacman", "-Qo", *chunk], timeout=120)
        for line in res.out.splitlines():
            m = _OWNED_RE.match(line.strip())
            if m:
                result.setdefault(m.group(1).rstrip("/"), set()).add(m.group(2))
    return result


def vercmp(a: str, b: str) -> int:
    """pacman version comparison: a<b → -1, equal → 0, a>b → 1."""
    res = run(["vercmp", a, b])
    try:
        return max(-1, min(1, int(res.out.strip())))
    except ValueError:
        return 0 if a == b else (-1 if a < b else 1)


def aur_helper(preferred: str = "auto") -> str | None:
    if preferred in ("yay", "paru") and have(preferred):
        return preferred
    if preferred == "none":
        return None
    for helper in ("paru", "yay"):
        if have(helper):
            return helper
    return None


def ignored_packages() -> set[str]:
    out: set[str] = set()
    try:
        for line in Path("/etc/pacman.conf").read_text().splitlines():
            line = line.strip()
            if line.startswith("IgnorePkg") and "=" in line:
                out.update(line.split("=", 1)[1].split())
    except OSError:
        pass
    return out
