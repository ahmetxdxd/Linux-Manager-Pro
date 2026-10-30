#!/usr/bin/env python3
"""Linux Manager Pro root helper.

Runs through pkexec. Accepts only the commands below and validates every argument.
Progress for the UI is sent as "@@LMP {json}" lines; everything else is plain text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    # So the package can be found when run from the source tree (development mode)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lmp import i18n  # noqa: E402
from lmp.core import automount  # noqa: E402
from lmp.core import flatpak as fp  # noqa: E402
from lmp.core import relocate  # noqa: E402
from lmp.core.privileged import PROGRESS_PREFIX, hook_exec_line  # noqa: E402
from lmp.i18n import tr  # noqa: E402

PKG_RE = re.compile(r"^[a-zA-Z0-9@._+-]+$")
DELETE_ROOTS = ("/etc", "/var/lib", "/var/cache", "/var/log", "/opt", "/usr/share", "/usr/lib",
                "/usr/local")
PROTECTED = {"/etc/pacman.d", "/etc/systemd", "/var/lib/pacman", "/usr/lib/modules",
             "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/fstab", "/etc/pacman.conf",
             "/usr/lib/systemd", "/var/lib/systemd", "/etc/sudoers", "/etc/sudoers.d",
             "/usr/share/applications", "/usr/share/icons", "/usr/lib/firmware"}


def emit(kind: str, **data) -> None:
    print(PROGRESS_PREFIX + json.dumps({"type": kind, **data}, ensure_ascii=False), flush=True)


def fail(message: str, code: int = 2) -> int:
    emit("error", message=message)
    print(message, file=sys.stderr, flush=True)
    return code


def check_packages(names: list[str]) -> None:
    bad = [n for n in names if not PKG_RE.match(n) or n.startswith("-")]
    if bad:
        raise ValueError(tr("Invalid package name: {names}").format(names=", ".join(bad)))


def passthrough(cmd: list[str]) -> int:
    print("$ " + " ".join(cmd), flush=True)
    env = dict(os.environ, LC_ALL="C", LANG="C")
    return subprocess.run(cmd, env=env).returncode


# --------------------------------------------------------------------------- #

def cmd_pacman_remove(args) -> int:
    check_packages(args.packages)
    return passthrough(["pacman", "-Rns", "--noconfirm", *args.packages])


def cmd_pacman_install(args) -> int:
    check_packages(args.packages)
    base = ["pacman", "-Syu"] if args.sysupgrade else ["pacman", "-S"]
    return passthrough(base + ["--needed", "--noconfirm", *args.packages])


def cmd_remove_orphans(args) -> int:
    res = subprocess.run(["pacman", "-Qdtq"], capture_output=True, text=True)
    orphans = res.stdout.split()
    if not orphans:
        print(tr("No orphaned packages."))
        return 0
    check_packages(orphans)
    return passthrough(["pacman", "-Rns", "--noconfirm", *orphans])


def cmd_clean_cache(args) -> int:
    keep = max(0, min(int(args.keep), 10))
    if shutil.which("paccache"):
        code = passthrough(["paccache", "-r", f"-k{keep}"])
        if code == 0 and args.uninstalled:
            code = passthrough(["paccache", "-ruk0"])
        return code
    print(tr("paccache not found (pacman-contrib), falling back to pacman -Sc."))
    return passthrough(["pacman", "-Sc", "--noconfirm"])


def _validate_delete(path: str) -> str:
    if not os.path.isabs(path):
        raise ValueError(tr("Not an absolute path: {path}").format(path=path))
    norm = os.path.normpath(path)
    parent_real = os.path.realpath(os.path.dirname(norm))
    full = os.path.join(parent_real, os.path.basename(norm))
    if full in DELETE_ROOTS or full in PROTECTED or any(full.startswith(p + "/") for p in PROTECTED):
        raise ValueError(tr("Protected location: {path}").format(path=path))
    if not any(full.startswith(root + "/") for root in DELETE_ROOTS):
        raise ValueError(tr("Outside the allowed locations: {path}").format(path=path))
    res = subprocess.run(["pacman", "-Qo", full], capture_output=True, text=True,
                         env=dict(os.environ, LC_ALL="C"))
    if res.returncode == 0 and "is owned by" in res.stdout:
        raise ValueError(tr("Owned by a package, not deleted: {path}").format(path=path))
    return full


def cmd_delete_paths(args) -> int:
    errors = 0
    for path in args.paths:
        try:
            full = _validate_delete(path)
            if os.path.islink(full) or os.path.isfile(full):
                os.unlink(full)
            elif os.path.isdir(full):
                shutil.rmtree(full)
            print(tr("Deleted: {path}").format(path=full), flush=True)
        except (ValueError, OSError) as exc:
            errors += 1
            print(tr("Skipped: {exc}").format(exc=exc), file=sys.stderr, flush=True)
    return 1 if errors else 0


def _automount(mountpoint: str) -> int:
    """Shared by the commands that accept --automount. Failure here is reported but does not
    undo the main operation (the disk still works, it just has to be opened by hand)."""
    try:
        emit("log", message=automount.add(mountpoint))
    except automount.AutomountError as exc:
        emit("log", message=tr("Automatic mounting could not be set up: {e}").format(e=exc))
        return 1
    return 0


def _user_folder(mountpoint: str) -> int:
    """Creates <disk>/.linux-manager-pro/home/<user>, owned by the user who called pkexec,
    so their personal folders can be moved there without root."""
    import pwd
    uid = os.environ.get("PKEXEC_UID")
    mountpoint = os.path.normpath(mountpoint)
    if not uid or not uid.isdigit() or not os.path.ismount(mountpoint):
        return 1
    user = pwd.getpwuid(int(uid))
    base = os.path.join(mountpoint, relocate.DISK_SUBDIR)
    os.makedirs(os.path.join(base, "home"), mode=0o755, exist_ok=True)
    folder = os.path.join(base, "home", user.pw_name)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chown(folder, user.pw_uid, user.pw_gid)
    emit("log", message=tr("Created: {path}").format(path=folder))
    return 0


def _prepare(args, mountpoint: str) -> None:
    if getattr(args, "user_folder", False):
        _user_folder(mountpoint)
    if getattr(args, "automount", False):
        _automount(mountpoint)


def cmd_prepare_disk(args) -> int:
    """Prepares a disk when no other root step runs: automount and/or the user folder."""
    if not os.path.ismount(os.path.normpath(args.mountpoint)):
        return fail(tr("{path} is not a mount point.").format(path=args.mountpoint))
    _prepare(args, args.mountpoint)
    return 0


def cmd_automount_add(args) -> int:
    try:
        emit("done", message=automount.add(args.mountpoint))
    except automount.AutomountError as exc:
        return fail(str(exc))
    return 0


def cmd_automount_remove(args) -> int:
    emit("done", message=automount.remove(args.mountpoint))
    return 0


def cmd_relocate_move(args) -> int:
    # The pacman hooks run a stand-alone script from a root-owned folder (see
    # relocate.write_hooks), so this also works when the app runs from its source folder.
    try:
        rel = relocate.move_package(args.package, args.target, args.dirs, emit)
    except relocate.RelocationError as exc:
        return fail(str(exc))
    _prepare(args, rel.target_mount)
    emit("done", message=tr("{package} moved to {mount}").format(package=rel.package, mount=rel.target_mount))
    return 0


def cmd_relocate_restore(args) -> int:
    try:
        relocate.restore_package(args.package, emit)
    except relocate.RelocationError as exc:
        return fail(str(exc))
    emit("done", message=tr("{package} moved back to the system disk").format(package=args.package))
    return 0


def _stdin_targets() -> list[str]:
    return [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]


def cmd_relocate_guard(args) -> int:
    return relocate.guard(_stdin_targets())


def cmd_relocate_cleanup(args) -> int:
    return relocate.cleanup(_stdin_targets(), hook_exec_line())


def cmd_flatpak_add(args) -> int:
    ident = fp.LMP_PREFIX + fp.slug(args.ident)
    path = os.path.normpath(args.path)
    mount = path
    while not os.path.ismount(mount) and mount != "/":
        mount = os.path.dirname(mount)
    if mount == "/":
        return fail(tr("The Flatpak location must be on an external disk."))
    _prepare(args, mount)
    os.makedirs(path, exist_ok=True)
    os.makedirs(fp.INSTALLATIONS_DIR, exist_ok=True)
    conf = fp.INSTALLATIONS_DIR / f"{ident}.conf"
    conf.write_text(fp.installation_conf_text(ident, path, args.name.replace("\n", " ")[:60]))
    print(tr("Created: {path}").format(path=conf))
    return passthrough(["flatpak", f"--installation={ident}", "remote-add", "--if-not-exists",
                        "flathub", "https://dl.flathub.org/repo/flathub.flatpakrepo"])


def cmd_flatpak_remove(args) -> int:
    if not args.ident.startswith(fp.LMP_PREFIX) or not fp.APP_ID_RE.match(args.ident):
        return fail(tr("Only locations created by Linux Manager Pro can be removed."))
    conf = fp.INSTALLATIONS_DIR / f"{args.ident}.conf"
    conf.unlink(missing_ok=True)
    print(tr("Removed: {path} (files on the disk were left untouched)").format(path=conf))
    return 0


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        print("This helper must be run with root privileges (pkexec).", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(prog="lmp-helper")
    parser.add_argument("--lang", default=i18n.DEFAULT)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pacman-remove"); p.add_argument("packages", nargs="+")
    p.set_defaults(fn=cmd_pacman_remove)
    p = sub.add_parser("pacman-install"); p.add_argument("--sysupgrade", action="store_true")
    p.add_argument("packages", nargs="+"); p.set_defaults(fn=cmd_pacman_install)
    p = sub.add_parser("remove-orphans"); p.set_defaults(fn=cmd_remove_orphans)
    p = sub.add_parser("clean-cache"); p.add_argument("--keep", default="2")
    p.add_argument("--uninstalled", action="store_true"); p.set_defaults(fn=cmd_clean_cache)
    p = sub.add_parser("delete-paths"); p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_delete_paths)
    p = sub.add_parser("relocate-move"); p.add_argument("package"); p.add_argument("target")
    p.add_argument("--automount", action="store_true")
    p.add_argument("--user-folder", action="store_true")
    p.add_argument("dirs", nargs="+"); p.set_defaults(fn=cmd_relocate_move)
    p = sub.add_parser("prepare-disk"); p.add_argument("mountpoint")
    p.add_argument("--automount", action="store_true")
    p.add_argument("--user-folder", action="store_true")
    p.set_defaults(fn=cmd_prepare_disk)
    p = sub.add_parser("automount-add"); p.add_argument("mountpoint")
    p.set_defaults(fn=cmd_automount_add)
    p = sub.add_parser("automount-remove"); p.add_argument("mountpoint")
    p.set_defaults(fn=cmd_automount_remove)
    p = sub.add_parser("relocate-restore"); p.add_argument("package")
    p.set_defaults(fn=cmd_relocate_restore)
    p = sub.add_parser("relocate-guard"); p.set_defaults(fn=cmd_relocate_guard)
    p = sub.add_parser("relocate-cleanup"); p.set_defaults(fn=cmd_relocate_cleanup)
    p = sub.add_parser("flatpak-installation-add"); p.add_argument("ident")
    p.add_argument("--automount", action="store_true")
    p.add_argument("--user-folder", action="store_true")
    p.add_argument("path"); p.add_argument("name"); p.set_defaults(fn=cmd_flatpak_add)
    p = sub.add_parser("flatpak-installation-remove"); p.add_argument("ident")
    p.set_defaults(fn=cmd_flatpak_remove)

    args = parser.parse_args(argv)
    i18n.set_language(args.lang)
    try:
        return args.fn(args)
    except ValueError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
