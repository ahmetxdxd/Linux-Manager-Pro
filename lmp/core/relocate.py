"""Moving packages and user data to an external disk.

Design (lessons learned from the earlier "pacman mover" attempts):

* Only directories DEDICATED to the package are moved (e.g. /opt/X, /usr/lib/X, /usr/share/X,
  /var/lib/X); directories that pacman says another package also owns are never moved. Single
  files under /usr/bin are left alone, so system commands keep working with the disk unplugged.
* IgnorePkg is NOT used; updates continue normally. Instead a pacman pre-transaction hook
  blocks updates of the moved packages only, while the disk is unplugged.
* All directories are copied and verified first, then swapped, and old copies are deleted
  last. If anything fails at any stage, the system returns to its initial state.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from lmp.core.util import dir_size, load_json, save_json
from lmp.core.verified_copy import verify_trees
from lmp.i18n import tr

STATE_DIR = Path("/var/lib/linux-manager-pro")
REGISTRY = STATE_DIR / "relocations.json"
HOOK_DIR = Path("/etc/pacman.d/hooks")
GUARD_HOOK = HOOK_DIR / "lmp-relocation-guard.hook"
CLEANUP_HOOK = HOOK_DIR / "lmp-relocation-cleanup.hook"
DISK_SUBDIR = ".linux-manager-pro"

ALLOWED_ROOTS = ("/opt", "/usr/lib", "/usr/lib32", "/usr/share", "/var/lib", "/var/cache",
                 "/usr/local/lib", "/usr/local/share")
NEVER_MOVE = {"/usr/lib/systemd", "/usr/lib/modules", "/usr/lib/firmware", "/usr/lib/udev",
              "/usr/lib/pacman", "/var/lib/pacman", "/usr/share/applications", "/usr/share/icons",
              "/usr/share/licenses", "/usr/share/doc", "/usr/share/man", "/usr/lib/python3",
              "/usr/share/dbus-1", "/usr/share/polkit-1", "/usr/lib/sysusers.d",
              "/usr/lib/tmpfiles.d", "/usr/share/locale", "/usr/share/fonts", "/usr/share/mime"}
SPACE_MARGIN = 512 * 1024 * 1024
# filesystems that keep Linux permissions, owners and symlinks
NATIVE_FS = ("ext4", "ext3", "btrfs", "xfs", "f2fs", "zfs", "bcachefs")
# filesystems that cannot store permissions, owners or symlinks
LOSSY_FS = ("vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk", "iso9660", "udf")
_RSYNC_PROGRESS = re.compile(rb"\s(\d{1,3})%\s")

Emit = Callable[..., None]


@dataclass
class MovedDir:
    path: str          # original location (now a symlink)
    stored: str        # real location on the disk
    size: int = 0


@dataclass
class Relocation:
    package: str
    target_mount: str
    target_uuid: str
    base: str
    date: str
    version: str = ""
    dirs: list[MovedDir] = field(default_factory=list)
    services: list[str] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return all(os.path.isdir(d.stored) for d in self.dirs)

    @property
    def total_size(self) -> int:
        return sum(d.size for d in self.dirs)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Relocation":
        dirs = [MovedDir(**x) for x in d.get("dirs", [])]
        return cls(package=d["package"], target_mount=d.get("target_mount", ""),
                   target_uuid=d.get("target_uuid", ""), base=d.get("base", ""),
                   date=d.get("date", ""), version=d.get("version", ""), dirs=dirs,
                   services=d.get("services", []))


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def load_registry() -> dict[str, Relocation]:
    raw = load_json(REGISTRY, {})
    out = {}
    for name, data in raw.items():
        try:
            out[name] = Relocation.from_dict(data)
        except (KeyError, TypeError):
            continue
    return out


def save_registry(entries: dict[str, Relocation]) -> None:
    save_json(REGISTRY, {k: v.to_dict() for k, v in entries.items()})


def _update_disk_manifest(rel: Relocation, remove: bool = False) -> None:
    manifest = Path(rel.target_mount) / DISK_SUBDIR / "system-relocations.json"
    if not manifest.parent.is_dir():
        return
    data = load_json(manifest, {})
    if remove:
        data.pop(rel.package, None)
    else:
        data[rel.package] = rel.to_dict()
    save_json(manifest, data)


# --------------------------------------------------------------------------- #
# Analysis (user side, no privileges needed)
# --------------------------------------------------------------------------- #

@dataclass
class Candidate:
    path: str
    size: int
    note: str = ""


def _is_allowed_path(path: str) -> bool:
    path = path.rstrip("/")
    if path in NEVER_MOVE or path in ALLOWED_ROOTS:
        return False
    return any(path.startswith(root + "/") for root in ALLOWED_ROOTS)


def known_system_dirs(package: str) -> list[str]:
    """System folders the app definitions list for this package (e.g. /var/lib/ollama, where
    the Ollama service keeps its models). They are created at runtime, so pacman does not
    own them and the package-name check alone would not recognise them."""
    from lmp.core import knowledge
    definition = knowledge.for_package(package)
    if not definition:
        return []
    return [p.rstrip("/") for p in definition.data if p.startswith("/") and _is_allowed_path(p)]


def _family(package: str) -> set[str]:
    """The package plus its variants from the same definition (ollama, ollama-cuda…)."""
    from lmp.core import knowledge
    definition = knowledge.for_package(package)
    return set(definition.packages) | {package} if definition else {package}


def _dedicated(d: str, owners: set[str], package: str, known: set[str]) -> bool:
    if owners == {package}:
        return True
    if d in known:
        return owners <= _family(package)
    return not owners and os.path.basename(d) == package


def candidate_dirs(package: str, files: list[str],
                   owners_fn: Callable[[list[str]], dict[str, set[str]]]) -> list[Candidate]:
    """Finds the top-level package-dedicated directories that can be moved."""
    known = set(known_system_dirs(package))
    dirs: set[str] = set(d for d in known if os.path.isdir(d))
    for f in files:
        p = f.rstrip("/")
        for root in ALLOWED_ROOTS:
            if p.startswith(root + "/"):
                rest = p[len(root) + 1:].split("/")
                # candidates up to two levels deep, like /opt/X, /usr/lib/X and /opt/vendor/X
                for depth in (1, 2):
                    if len(rest) > depth or (len(rest) == depth and f.endswith("/")):
                        dirs.add(root + "/" + "/".join(rest[:depth]))
    for root in ("/var/lib", "/var/cache"):
        extra = f"{root}/{package}"
        if os.path.isdir(extra):
            dirs.add(extra)
    dirs = {d for d in dirs if _is_allowed_path(d) and os.path.isdir(d) and not os.path.islink(d)
            and not os.path.ismount(d)}
    if not dirs:
        return []
    owner_map = owners_fn(sorted(dirs))
    dedicated = []
    for d in sorted(dirs):
        if _dedicated(d, owner_map.get(d, set()), package, known):
            dedicated.append(d)
    # keep only the top level (if /opt/x is listed, /opt/x/y is not shown again)
    top = [d for d in dedicated if not any(d != o and d.startswith(o + "/") for o in dedicated)]
    out = []
    for d in top:
        note = tr("Package's own folder") if os.path.basename(d) == package else ""
        if d.startswith("/var/"):
            note = tr("Application data (created at runtime)")
        if d in known:
            note = tr("Application data (models, libraries…)")
        out.append(Candidate(d, dir_size(d), note))
    return sorted(out, key=lambda c: -c.size)


# --------------------------------------------------------------------------- #
# Root-side operations (called from lmp-helper)
# --------------------------------------------------------------------------- #

class RelocationError(Exception):
    pass


def _systemd_services(package: str) -> list[str]:
    res = subprocess.run(["pacman", "-Qlq", package], capture_output=True, text=True)
    services = []
    for line in res.stdout.splitlines():
        if "/systemd/system/" in line and line.endswith(".service") and "@" not in line:
            services.append(os.path.basename(line))
    return services


def _active(service: str) -> bool:
    res = subprocess.run(["systemctl", "is-active", "--quiet", service])
    return res.returncode == 0


def _copy_tree(src: str, dest: str, emit: Emit, label: str) -> None:
    os.makedirs(dest, exist_ok=True)
    if shutil.which("rsync"):
        cmd = ["rsync", "-aHAX", "--numeric-ids", "--info=progress2", "--no-inc-recursive",
               src.rstrip("/") + "/", dest.rstrip("/") + "/"]
        # stderr is merged into stdout: with two separate pipes, rsync blocks once it has
        # written a pipe-full of error messages that nobody reads, and the copy hangs forever
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buf = b""
        last = -1
        errors: list[str] = []
        assert proc.stdout is not None
        with proc.stdout:
            while True:
                chunk = proc.stdout.read(512)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for part in parts:
                    m = _RSYNC_PROGRESS.search(part)
                    if m:
                        pct = int(m.group(1))
                        if pct != last:
                            last = pct
                            emit("progress", percent=pct, message=tr("Copying: {label}").format(label=label))
                    elif part.strip():
                        errors.append(part.decode(errors="replace").strip())
                        del errors[:-20]          # the last lines are the useful ones
            if buf.strip() and not _RSYNC_PROGRESS.search(buf):
                errors.append(buf.decode(errors="replace").strip())
        if proc.wait() != 0:
            raise RelocationError(tr("rsync error ({label}): {err}").format(
                label=label, err="\n".join(errors[-8:])))
    else:
        res = subprocess.run(["cp", "-a", "--reflink=auto", src.rstrip("/") + "/.", dest],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RelocationError(tr("Copy error ({label}): {err}").format(label=label, err=res.stderr.strip()))


def _tree_signature(path: str) -> tuple[int, int]:
    count = size = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                size += os.lstat(os.path.join(root, f)).st_size
                count += 1
            except OSError:
                pass
        count += len(dirs)
    return count, size


def _fs_type(path: str) -> str:
    res = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", path],
                         capture_output=True, text=True)
    return res.stdout.strip()


def _uuid(path: str) -> str:
    res = subprocess.run(["findmnt", "-n", "-o", "UUID", "--target", path],
                         capture_output=True, text=True)
    return res.stdout.strip()


def validate_dirs(package: str, dirs: list[str]) -> None:
    from lmp.core import pacman  # local import: keeps the helper fast to start
    if not re.match(r"^[a-zA-Z0-9@._+-]+$", package):
        raise RelocationError(tr("Invalid package name."))
    if subprocess.run(["pacman", "-Q", package], capture_output=True).returncode != 0:
        raise RelocationError(tr("{package} is not installed.").format(package=package))
    owner_map = pacman.owners(dirs)
    known = set(known_system_dirs(package))
    for d in dirs:
        d_norm = os.path.normpath(d)
        if d_norm != d.rstrip("/") or not _is_allowed_path(d_norm):
            raise RelocationError(tr("This location cannot be moved: {d}").format(d=d))
        if os.path.islink(d_norm) or not os.path.isdir(d_norm) or os.path.ismount(d_norm):
            raise RelocationError(tr("Not a directory, or already moved: {d}").format(d=d))
        owners = owner_map.get(d_norm, set())
        if not _dedicated(d_norm, owners, package, known):
            if owners:
                raise RelocationError(tr("{d} is shared with other packages: {owners}").format(
                    d=d, owners=", ".join(sorted(owners))))
            raise RelocationError(tr("{d} does not belong to this package.").format(d=d))


def move_package(package: str, target_mount: str, dirs: list[str], emit: Emit,
                 hook_exec: str | None = None) -> Relocation:
    registry = load_registry()
    if package in registry:
        raise RelocationError(tr("{package} has already been moved.").format(package=package))
    target_mount = os.path.normpath(target_mount)
    if not os.path.ismount(target_mount):
        raise RelocationError(tr("{path} is not a mount point.").format(path=target_mount))
    fstype = _fs_type(target_mount)
    if fstype not in NATIVE_FS:
        raise RelocationError(
            tr("The target filesystem ({fs}) does not support Linux permissions. Use a disk "
               "formatted as ext4, btrfs or xfs.").format(fs=fstype or tr("unknown")))
    validate_dirs(package, dirs)

    sizes = {d: dir_size(d) for d in dirs}
    need = sum(sizes.values()) + SPACE_MARGIN
    if shutil.disk_usage(target_mount).free < need:
        raise RelocationError(tr("Not enough free space on the target disk."))

    base = os.path.join(target_mount, DISK_SUBDIR, "system", package)
    rootfs = os.path.join(base, "rootfs")
    version = subprocess.run(["pacman", "-Q", package], capture_output=True,
                             text=True).stdout.split()[-1:] or [""]

    stopped = [s for s in _systemd_services(package) if _active(s)]
    for s in stopped:
        emit("log", message=tr("Stopping service: {s}").format(s=s))
        subprocess.run(["systemctl", "stop", s])

    moved: list[MovedDir] = []
    swapped: list[MovedDir] = []
    try:
        # 1) Copy + verify (nothing on the system has changed yet)
        for i, d in enumerate(dirs, 1):
            stored = os.path.join(rootfs, d.lstrip("/"))
            if os.path.exists(stored):
                shutil.rmtree(stored)
            emit("status", message=tr("[{i}/{n}] copying {d}").format(i=i, n=len(dirs), d=d))
            _copy_tree(d, stored, emit, d)
            emit("status", message=tr("Verifying the copy byte by byte: {d}").format(d=d))
            if verify_trees(d, stored):
                raise RelocationError(tr("Verification failed: the copy of {d} does not match the source.").format(d=d))
            st = os.stat(d)
            os.chmod(stored, st.st_mode)
            os.chown(stored, st.st_uid, st.st_gid)
            moved.append(MovedDir(d, stored, sizes[d]))
        # 2) Swap: directory → .lmp-old, a symlink takes its place
        emit("status", message=tr("Creating symlinks"))
        for m in moved:
            os.rename(m.path, m.path + ".lmp-old")
            try:
                os.symlink(m.stored, m.path)
            except OSError:
                os.rename(m.path + ".lmp-old", m.path)
                raise
            swapped.append(m)
    except BaseException as exc:
        emit("status", message=tr("An error occurred, rolling back changes"))
        for m in reversed(swapped):
            if os.path.islink(m.path):
                os.unlink(m.path)
            if os.path.isdir(m.path + ".lmp-old"):
                os.rename(m.path + ".lmp-old", m.path)
        shutil.rmtree(base, ignore_errors=True)
        for s in stopped:
            subprocess.run(["systemctl", "start", s])
        if isinstance(exc, RelocationError):
            raise
        raise RelocationError(str(exc)) from exc

    # 3) Delete the old copies
    for m in moved:
        shutil.rmtree(m.path + ".lmp-old", ignore_errors=True)

    rel = Relocation(package=package, target_mount=target_mount, target_uuid=_uuid(target_mount),
                     base=base, date=datetime.now().strftime("%Y-%m-%d %H:%M"),
                     version=version[0], dirs=moved, services=stopped)
    registry[package] = rel
    save_registry(registry)
    _update_disk_manifest(rel)
    write_hooks(registry, hook_exec)

    for s in stopped:
        emit("log", message=tr("Restarting service: {s}").format(s=s))
        subprocess.run(["systemctl", "start", s])
    return rel


def restore_package(package: str, emit: Emit, hook_exec: str | None = None) -> None:
    registry = load_registry()
    rel = registry.get(package)
    if not rel:
        raise RelocationError(tr("There is no relocation record for {package}.").format(package=package))
    if not rel.connected:
        raise RelocationError(tr("{path} is not mounted. Attach the disk first.").format(path=rel.target_mount))
    need = rel.total_size + SPACE_MARGIN
    if shutil.disk_usage("/").free < need:
        raise RelocationError(tr("Not enough free space on the system disk to restore."))

    stopped = [s for s in _systemd_services(package) if _active(s)]
    for s in stopped:
        subprocess.run(["systemctl", "stop", s])

    restored: list[MovedDir] = []
    swapped_back: list[MovedDir] = []
    try:
        for i, d in enumerate(rel.dirs, 1):
            if not os.path.islink(d.path):
                raise RelocationError(tr("{path} is not the expected symlink, please check it manually.").format(path=d.path))
            emit("status", message=tr("[{i}/{n}] copying {path} back").format(i=i, n=len(rel.dirs), path=d.path))
            tmp = d.path + ".lmp-new"
            shutil.rmtree(tmp, ignore_errors=True)
            _copy_tree(d.stored, tmp, emit, d.path)
            if verify_trees(d.stored, tmp):
                raise RelocationError(tr("Verification failed: the copy of {d} does not match the source.").format(d=d.path))
            st = os.stat(d.stored)
            os.chmod(tmp, st.st_mode)
            os.chown(tmp, st.st_uid, st.st_gid)
            restored.append(d)
        for d in restored:
            os.unlink(d.path)
            os.rename(d.path + ".lmp-new", d.path)
            swapped_back.append(d)
    except BaseException as exc:
        for d in restored:
            if os.path.isdir(d.path + ".lmp-new") and os.path.islink(d.path):
                shutil.rmtree(d.path + ".lmp-new", ignore_errors=True)
        if swapped_back:
            # some folders are already back on the system disk: record that, otherwise a
            # later restore stops at them with "not the expected symlink"
            for d in swapped_back:
                shutil.rmtree(d.stored, ignore_errors=True)
            rel.dirs = [d for d in rel.dirs if d not in swapped_back]
            if not rel.dirs:
                del registry[package]
            save_registry(registry)
            _update_disk_manifest(rel, remove=not rel.dirs)
            write_hooks(registry, hook_exec)
        for s in stopped:
            subprocess.run(["systemctl", "start", s])
        if isinstance(exc, RelocationError):
            raise
        raise RelocationError(str(exc)) from exc

    shutil.rmtree(rel.base, ignore_errors=True)
    del registry[package]
    save_registry(registry)
    _update_disk_manifest(rel, remove=True)
    write_hooks(registry, hook_exec)
    for s in stopped:
        subprocess.run(["systemctl", "start", s])


HOOK_SCRIPT = STATE_DIR / "relocation-hook"

# The pacman hooks call this small stand-alone script instead of Linux Manager Pro itself.
# It lives in a root-owned folder, so moving packages also works when Linux Manager Pro runs
# from the source folder: a hook running as root must never execute user-writable code.
_HOOK_TEMPLATE = r'''#!/usr/bin/python3 -I
"""Linux Manager Pro relocation hook (generated, do not edit)."""
import json, os, shutil, subprocess, sys

REGISTRY = "@REGISTRY@"
HOOKS = @HOOKS@
MSG_BLOCKED = @MSG_BLOCKED@
MSG_HINT = @MSG_HINT@
MSG_CLEANED = @MSG_CLEANED@


def load():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data):
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, REGISTRY)


def connected(rel):
    return all(os.path.isdir(d.get("stored", "")) for d in rel.get("dirs", []))


def guard(targets):
    registry = load()
    blocked = [registry[t] for t in targets if t in registry and not connected(registry[t])]
    for rel in blocked:
        print(MSG_BLOCKED.format(package=rel.get("package"), mount=rel.get("target_mount")),
              file=sys.stderr)
    if blocked:
        print(MSG_HINT, file=sys.stderr)
        return 1
    return 0


def cleanup(targets):
    registry = load()
    changed = False
    for t in targets:
        rel = registry.get(t)
        if not rel:
            continue
        if subprocess.run(["pacman", "-Q", t], capture_output=True).returncode == 0:
            continue
        for d in rel.get("dirs", []):
            if os.path.islink(d.get("path", "")):
                os.unlink(d["path"])
        if rel.get("base"):
            shutil.rmtree(rel["base"], ignore_errors=True)
        manifest = os.path.join(rel.get("target_mount", ""), ".linux-manager-pro",
                                "system-relocations.json")
        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
            data.pop(t, None)
            with open(manifest, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (OSError, ValueError):
            pass
        del registry[t]
        changed = True
        print(MSG_CLEANED.format(t=t))
    if changed:
        save(registry)
        if not registry:
            for hook in HOOKS:
                try:
                    os.unlink(hook)
                except OSError:
                    pass
    return 0


if __name__ == "__main__":
    names = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    sys.exit(guard(names) if sys.argv[1:] == ["guard"] else cleanup(names))
'''


def hook_script_text() -> str:
    """The stand-alone hook script, with its messages in the active language."""
    values = {
        "@REGISTRY@": str(REGISTRY),
        "@HOOKS@": repr([str(GUARD_HOOK), str(CLEANUP_HOOK)]),
        "@MSG_BLOCKED@": repr(tr("ERROR: the files of package '{package}' are on an external "
                                 "disk ({mount}) which is not currently mounted.")),
        "@MSG_HINT@": repr(tr("Mount the disk and retry, or move the package back to the system "
                              "disk from Linux Manager Pro > External Disk.")),
        "@MSG_CLEANED@": repr(tr("Linux Manager Pro: relocation record of {t} removed.")),
    }
    text = _HOOK_TEMPLATE
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def write_hooks(registry: dict[str, Relocation], hook_exec: str | None = None) -> None:
    """(Re)writes the pacman hooks for the moved packages. `hook_exec` is ignored (kept for
    older callers): the hooks always run the stand-alone script in the state folder."""
    if not registry:
        GUARD_HOOK.unlink(missing_ok=True)
        CLEANUP_HOOK.unlink(missing_ok=True)
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o755)
    tmp = HOOK_SCRIPT.with_name(HOOK_SCRIPT.name + ".tmp")
    tmp.write_text(hook_script_text(), encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, HOOK_SCRIPT)
    exec_line = f"/usr/bin/python3 -I {HOOK_SCRIPT}"
    HOOK_DIR.mkdir(parents=True, exist_ok=True)
    targets = "".join(f"Target = {name}\n" for name in sorted(registry))
    GUARD_HOOK.write_text(
        "# Managed by Linux Manager Pro, do not edit by hand.\n"
        "[Trigger]\nOperation = Upgrade\nOperation = Remove\nType = Package\n" + targets +
        "\n[Action]\nDescription = " + tr("Checking packages moved to an external disk...") + "\n"
        "When = PreTransaction\n"
        f"Exec = {exec_line} guard\nNeedsTargets\nAbortOnFail\n")
    CLEANUP_HOOK.write_text(
        "# Managed by Linux Manager Pro, do not edit by hand.\n"
        "[Trigger]\nOperation = Remove\nType = Package\n" + targets +
        "\n[Action]\nDescription = " + tr("Cleaning up records of removed relocated packages...") + "\n"
        "When = PostTransaction\n"
        f"Exec = {exec_line} cleanup\nNeedsTargets\n")


def guard(targets: list[str]) -> int:
    registry = load_registry()
    blocked = [registry[t] for t in targets if t in registry and not registry[t].connected]
    for rel in blocked:
        print(tr("ERROR: the files of package '{package}' are on an external disk ({mount}) "
                 "which is not currently mounted.").format(package=rel.package,
                                                           mount=rel.target_mount), file=sys.stderr)
    if blocked:
        print(tr("Mount the disk and retry, or move the package back to the system disk from "
                 "Linux Manager Pro > External Disk."), file=sys.stderr)
        return 1
    return 0


def cleanup(targets: list[str], hook_exec: str | None = None) -> int:
    registry = load_registry()
    changed = False
    for t in targets:
        rel = registry.get(t)
        if not rel:
            continue
        if subprocess.run(["pacman", "-Q", t], capture_output=True).returncode == 0:
            continue
        for d in rel.dirs:
            if os.path.islink(d.path):
                os.unlink(d.path)
        shutil.rmtree(rel.base, ignore_errors=True)
        _update_disk_manifest(rel, remove=True)
        del registry[t]
        changed = True
        print(tr("Linux Manager Pro: relocation record of {t} removed.").format(t=t))
    if changed:
        save_registry(registry)
        write_hooks(registry, hook_exec)
    return 0


# --------------------------------------------------------------------------- #
# User-data relocation (no privileges needed)
# --------------------------------------------------------------------------- #

def user_registry_path() -> Path:
    from lmp.core.util import data_dir
    return data_dir() / "user-relocations.json"


@dataclass
class UserRelocation:
    path: str
    stored: str
    target_mount: str
    size: int
    date: str

    @property
    def connected(self) -> bool:
        return os.path.isdir(self.stored)


def load_user_relocations() -> list[UserRelocation]:
    raw = load_json(user_registry_path(), [])
    out = []
    for item in raw:
        try:
            out.append(UserRelocation(**item))
        except TypeError:
            continue
    return out


def _save_user_relocations(items: list[UserRelocation]) -> None:
    save_json(user_registry_path(), [asdict(i) for i in items])


def move_user_dir(path: str, target_mount: str,
                  progress: Callable[[int, str], None] | None = None) -> UserRelocation:
    from lmp.core.util import HOME
    src = os.path.abspath(os.path.expanduser(path))
    if not src.startswith(str(HOME) + "/"):
        raise RelocationError(tr("Only folders inside your home directory can be moved."))
    fstype = _fs_type(target_mount)
    if fstype.lower() in LOSSY_FS:
        # exFAT/NTFS/FAT cannot store permissions, owners or symlinks: the copy either fails
        # or silently loses them (think ~/.ssh)
        raise RelocationError(
            tr("The target filesystem ({fs}) does not support Linux permissions. Use a disk "
               "formatted as ext4, btrfs or xfs.").format(fs=fstype))
    if os.path.islink(src) or not os.path.isdir(src):
        raise RelocationError(tr("Folder not found, or it is already a link."))
    if any(r.path == src for r in load_user_relocations()):
        raise RelocationError(tr("This folder has already been moved."))
    size = dir_size(src)
    if shutil.disk_usage(target_mount).free < size + SPACE_MARGIN:
        raise RelocationError(tr("Not enough free space on the target disk."))
    user = os.environ.get("USER") or Path(HOME).name
    stored = os.path.join(target_mount, DISK_SUBDIR, "home", user, os.path.relpath(src, HOME))

    def emit(kind, **kw):
        if progress and kind == "progress":
            progress(kw.get("percent", 0), kw.get("message", ""))

    if os.path.exists(stored):
        raise RelocationError(tr("A folder with the same name already exists at the destination: {path}").format(path=stored))
    try:
        _copy_tree(src, stored, emit, os.path.basename(src))
        if verify_trees(src, stored):
            raise RelocationError(tr("Verification failed: the copy does not match the source."))
        os.rename(src, src + ".lmp-old")
        try:
            os.symlink(stored, src)
        except OSError:
            os.rename(src + ".lmp-old", src)
            raise
    except BaseException:
        shutil.rmtree(stored, ignore_errors=True)
        raise
    shutil.rmtree(src + ".lmp-old", ignore_errors=True)
    item = UserRelocation(src, stored, target_mount, size, datetime.now().strftime("%Y-%m-%d %H:%M"))
    items = load_user_relocations() + [item]
    _save_user_relocations(items)
    return item


def restore_user_dir(item: UserRelocation,
                     progress: Callable[[int, str], None] | None = None) -> None:
    if not item.connected:
        raise RelocationError(tr("{path} is not mounted.").format(path=item.target_mount))
    if not os.path.islink(item.path):
        raise RelocationError(tr("{path} is not the expected link, please check it manually.").format(path=item.path))
    if shutil.disk_usage(os.path.dirname(item.path)).free < item.size + SPACE_MARGIN:
        raise RelocationError(tr("Not enough free space on the home disk."))

    def emit(kind, **kw):
        if progress and kind == "progress":
            progress(kw.get("percent", 0), kw.get("message", ""))

    tmp = item.path + ".lmp-new"
    shutil.rmtree(tmp, ignore_errors=True)
    _copy_tree(item.stored, tmp, emit, os.path.basename(item.path))
    if verify_trees(item.stored, tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        raise RelocationError(tr("Verification failed: the copy does not match the source."))
    os.unlink(item.path)
    os.rename(tmp, item.path)
    shutil.rmtree(item.stored, ignore_errors=True)
    items = [i for i in load_user_relocations() if i.path != item.path]
    _save_user_relocations(items)
