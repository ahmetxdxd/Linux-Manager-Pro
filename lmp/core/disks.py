"""Detects attached disks via `lsblk`."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from lmp.core.util import format_size, run
from lmp.i18n import tr

LINUX_NATIVE_FS = {"ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs", "zfs", "bcachefs", "jfs"}
_SKIP_MOUNTS = ("/boot", "/efi", "/proc", "/sys", "/dev", "/run/user", "/snap",
                "/var/lib/docker", "/var/lib/flatpak", "/tmp", "/home/.snapshots", "/.snapshots")


@dataclass
class Drive:
    device: str
    mountpoint: str
    fstype: str
    label: str
    uuid: str
    model: str
    total: int
    used: int
    free: int
    removable: bool
    writable: bool

    @property
    def is_root(self) -> bool:
        return self.mountpoint == "/"

    @property
    def native(self) -> bool:
        return self.fstype.lower() in LINUX_NATIVE_FS

    @property
    def percent(self) -> float:
        return (self.used / self.total * 100) if self.total else 0.0

    @property
    def title(self) -> str:
        if self.is_root:
            return tr("System disk (/)")
        if self.mountpoint == "/home":
            return tr("Home disk (/home)")
        return self.label or self.model or os.path.basename(self.mountpoint) or self.device

    @property
    def summary(self) -> str:
        return tr("{free} free / {total} · {fs}").format(
            free=format_size(self.free), total=format_size(self.total), fs=self.fstype)


def _flatten(devices: list) -> list:
    out = []
    for d in devices:
        out.append(d)
        out.extend(_flatten(d.get("children") or []))
    return out


def parse_lsblk(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return _flatten(data.get("blockdevices", []))


def list_drives(include_system: bool = True) -> list[Drive]:
    res = run(["lsblk", "-J", "-b", "-o",
               "NAME,PATH,FSTYPE,LABEL,UUID,MODEL,MOUNTPOINTS,RM,HOTPLUG,RO,TYPE"])
    devices = parse_lsblk(res.out) if res.ok else []
    drives: list[Drive] = []
    seen = set()
    parent_model = {}
    for dev in devices:
        if dev.get("model"):
            parent_model[dev.get("name")] = dev.get("model")
    for dev in devices:
        mounts = [m for m in (dev.get("mountpoints") or []) if m]
        for mp in mounts:
            if mp in seen or mp.startswith("[") or any(
                    mp == s or mp.startswith(s + "/") for s in _SKIP_MOUNTS):
                continue
            if not include_system and mp in ("/", "/home", "/var", "/usr"):
                continue
            try:
                usage = shutil.disk_usage(mp)
            except OSError:
                continue
            seen.add(mp)
            model = dev.get("model") or ""
            if not model:
                # take the partition's model from its parent disk
                for name, m in parent_model.items():
                    if (dev.get("name") or "").startswith(name):
                        model = m
                        break
            drives.append(Drive(
                device=dev.get("path") or f"/dev/{dev.get('name')}",
                mountpoint=mp, fstype=dev.get("fstype") or "",
                label=dev.get("label") or "", uuid=dev.get("uuid") or "",
                model=(model or "").strip(),
                total=usage.total, used=usage.used, free=usage.free,
                removable=bool(dev.get("rm")) or bool(dev.get("hotplug")),
                writable=not dev.get("ro") and os.access(mp, os.W_OK),
            ))
    drives.sort(key=lambda d: (not d.is_root, d.mountpoint != "/home", d.mountpoint))
    return drives


def drive_for_path(path: str, drives: list[Drive] | None = None) -> Drive | None:
    drives = drives if drives is not None else list_drives()
    real = os.path.realpath(path)
    best = None
    for d in drives:
        mp = d.mountpoint.rstrip("/") or "/"
        if real == mp or real.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) > len(best.mountpoint):
                best = d
    return best


def same_filesystem(a: str, b: str) -> bool:
    try:
        return Path(a).stat().st_dev == Path(b).stat().st_dev
    except OSError:
        return False
