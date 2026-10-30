"""System summary for the Overview page."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

from lmp.core import autostart, disks, flatpak, pacman
from lmp.core.util import current_desktops, os_release


@dataclass
class Overview:
    os_name: str = ""
    kernel: str = ""
    cpu: str = ""
    cores: int = 0
    ram_total: int = 0
    ram_available: int = 0
    uptime: float = 0.0
    desktop: str = ""
    pkg_total: int = 0
    pkg_explicit: int = 0
    pkg_foreign: int = 0
    flatpak_count: int = 0
    orphans: list[str] = field(default_factory=list)
    cache_size: int = 0
    boot_seconds: float | None = None
    drives: list[disks.Drive] = field(default_factory=list)


def _meminfo() -> tuple[int, int]:
    total = avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return total, avail


def _cpu() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return " ".join(line.split(":", 1)[1].split())
    except OSError:
        pass
    return platform.processor()


def collect(include_packages: bool = True) -> Overview:
    """`include_packages=False` skips the package and Flatpak counts; the UI derives those
    from the application list it already loads, instead of querying pacman twice."""
    ov = Overview()
    info = os_release()
    ov.os_name = info.get("PRETTY_NAME") or info.get("NAME", "Linux")
    ov.kernel = platform.release()
    ov.cpu = _cpu()
    ov.cores = os.cpu_count() or 0
    ov.ram_total, ov.ram_available = _meminfo()
    try:
        ov.uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    ov.desktop = " / ".join(current_desktops()) or os.environ.get("DESKTOP_SESSION", "")
    if pacman.available():
        if include_packages:
            ov.pkg_total = len(pacman.installed_versions())
            ov.pkg_explicit = (len(pacman.explicit_native_names())
                               + len(pacman.explicit_foreign_names()))
            ov.pkg_foreign = len(pacman.foreign_names())
        ov.orphans = pacman.orphans()
        ov.cache_size = pacman.cache_size()
    if include_packages:
        ov.flatpak_count = len(flatpak.list_apps())
    report = autostart.boot_report(limit=0)
    ov.boot_seconds = report.total_seconds if report else None
    ov.drives = disks.list_drives()
    return ov
