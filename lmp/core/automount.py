"""Mounting an external disk automatically, so moved apps start like any other app.

Desktop file managers mount external disks under /run/media/<user>/… only when you open them.
After a reboot such a disk is not mounted until you click it, so an app moved there does not
start. Here an /etc/fstab entry is added for the disk, at the path it already uses:

* ``nofail`` + a short device timeout: booting never waits for a missing disk.
* ``x-systemd.automount``: the disk is mounted the moment something (e.g. a moved app) opens
  a file on it, also when it is plugged in later.
* ``x-gvfs-show``: it keeps showing up in the file manager.

Our lines are marked with a comment, so they can be found and removed again. /etc/fstab is
backed up before every change.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lmp.i18n import tr

FSTAB = Path("/etc/fstab")
MARK = "# Linux Manager Pro: automount"
OPTIONS = "nofail,x-systemd.automount,x-systemd.device-timeout=5s,x-gvfs-show"
# only filesystems that keep Linux permissions; others need per-user mount options
SUPPORTED_FS = ("ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs", "jfs")
ALLOWED_PREFIXES = ("/run/media/", "/media/", "/mnt/")
UUID_RE = re.compile(r"^[A-Za-z0-9-]{4,64}$")

NONE, OURS, FSTAB_ENTRY = "none", "ours", "fstab"


class AutomountError(Exception):
    pass


def escape(path: str) -> str:
    """fstab escapes whitespace as octal (a space is \\040)."""
    return (path.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")
            .replace("\n", "\\012"))


def unescape(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


@dataclass
class FstabEntry:
    source: str
    target: str
    fstype: str
    options: str
    ours: bool


def parse_fstab(text: str) -> list[FstabEntry]:
    out = []
    previous = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            parts = stripped.split()
            if len(parts) >= 3:
                out.append(FstabEntry(parts[0], unescape(parts[1]).rstrip("/") or "/",
                                      parts[2], parts[3] if len(parts) > 3 else "",
                                      previous.strip() == MARK))
        previous = line
    return out


def read_fstab() -> str:
    try:
        return FSTAB.read_text(encoding="utf-8")
    except OSError:
        return ""


def status(mountpoint: str, uuid: str = "") -> str:
    """OURS: we set it up; FSTAB_ENTRY: the user's own fstab already mounts this disk at a
    fixed place (nothing to do); NONE: mounted only on demand by the file manager."""
    mountpoint = mountpoint.rstrip("/") or "/"
    for e in parse_fstab(read_fstab()):
        if e.target == mountpoint or (uuid and e.source in (f"UUID={uuid}",
                                                             f"/dev/disk/by-uuid/{uuid}")):
            return OURS if e.ours else FSTAB_ENTRY
    return NONE


def fstab_lines(uuid: str, mountpoint: str, fstype: str) -> str:
    return f"{MARK}\nUUID={uuid} {escape(mountpoint)} {fstype} {OPTIONS} 0 0\n"


# --------------------------------------------------------------------------- #
# Root side (called from lmp-helper)
# --------------------------------------------------------------------------- #

def _findmnt(mountpoint: str, column: str) -> str:
    res = subprocess.run(["findmnt", "-n", "-o", column, "--mountpoint", mountpoint],
                         capture_output=True, text=True)
    return res.stdout.strip()


def _write_fstab(text: str) -> None:
    if FSTAB.exists():
        shutil.copy2(FSTAB, FSTAB.with_name("fstab.lmp-backup"))
    tmp = FSTAB.with_name("fstab.lmp-tmp")
    tmp.write_text(text, encoding="utf-8")
    if FSTAB.exists():
        st = os.stat(FSTAB)
        os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, FSTAB)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)


def add(mountpoint: str) -> str:
    """Adds the automount entry for the disk mounted at `mountpoint`. Returns a message."""
    mountpoint = os.path.normpath(mountpoint)
    if not mountpoint.startswith(ALLOWED_PREFIXES) or not os.path.ismount(mountpoint):
        raise AutomountError(tr("{path} is not a mount point.").format(path=mountpoint))
    uuid = _findmnt(mountpoint, "UUID")
    fstype = _findmnt(mountpoint, "FSTYPE")
    if not uuid or not UUID_RE.match(uuid):
        raise AutomountError(tr("Could not read the disk's UUID."))
    if fstype not in SUPPORTED_FS:
        raise AutomountError(
            tr("The target filesystem ({fs}) does not support Linux permissions. Use a disk "
               "formatted as ext4, btrfs or xfs.").format(fs=fstype or tr("unknown")))
    current = status(mountpoint, uuid)
    if current != NONE:
        return tr("{path} is already mounted automatically.").format(path=mountpoint)
    line = fstab_lines(uuid, mountpoint, fstype)
    check = Path("/run/lmp-fstab-check")
    check.write_text(line, encoding="utf-8")
    try:
        res = subprocess.run(["findmnt", "--verify", "--tab-file", str(check)],
                             capture_output=True, text=True)
    finally:
        check.unlink(missing_ok=True)
    # only a syntax problem is fatal; "source unreachable" style findings depend on the
    # moment (the disk is mounted right now, and nofail covers boots without it)
    output = res.stdout + res.stderr
    m = re.search(r"(\d+) parse error", output)
    if m is None or int(m.group(1)) > 0:
        raise AutomountError(output.strip() or tr("Could not check the new /etc/fstab line."))
    text = read_fstab()
    if text and not text.endswith("\n"):
        text += "\n"
    _write_fstab(text + line)
    return tr("{path} will now be mounted automatically.").format(path=mountpoint)


def remove(mountpoint: str) -> str:
    """Removes only the entry we added (marker comment + the line right after it)."""
    mountpoint = os.path.normpath(mountpoint)
    lines = read_fstab().splitlines(keepends=True)
    out, i, removed = [], 0, False
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        parts = nxt.split()
        if (line.strip() == MARK and len(parts) >= 2
                and unescape(parts[1]).rstrip("/") == mountpoint):
            removed = True
            i += 2
            continue
        out.append(line)
        i += 1
    if not removed:
        return tr("There is no automount entry for {path}.").format(path=mountpoint)
    _write_fstab("".join(out))
    return tr("{path} is no longer mounted automatically.").format(path=mountpoint)
