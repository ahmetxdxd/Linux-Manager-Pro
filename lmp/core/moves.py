"""One-step moving of an application to an external disk (and back).

An application can consist of up to three kinds of parts, each moved the way that is safe for
it:

* ``program`` – folders that belong only to a pacman package (/opt/discord, /usr/share/X,
  /var/lib/ollama…). Moved as root by the helper, replaced by symlinks; a pacman hook keeps
  updates safe (see relocate.py).
* ``flatpak`` – a Flatpak app is reinstalled into a Flatpak installation on the disk.
* ``data``    – personal folders in the home directory (~/.ollama, ~/.local/share/Steam,
  ~/.var/app/<id>…). Moved as the user, replaced by a symlink.

Everything keeps its old path, so the app starts exactly as before while the disk is mounted
(automount.py makes sure it is).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from lmp.core import automount, disks, flatpak, knowledge, pacman, relocate
from lmp.core.apps import AppEntry
from lmp.core.util import HOME, dir_size, untilde
from lmp.i18n import tr

PROGRAM, FLATPAK, DATA = "program", "flatpak", "data"

SYSTEM, EXTERNAL, PARTIAL, DISCONNECTED = "system", "external", "partial", "disconnected"


@dataclass
class MovePart:
    kind: str
    path: str               # folder, or the Flatpak app id
    size: int
    label: str
    moved: bool = False     # already on the external disk
    connected: bool = True  # (moved parts) the disk is mounted
    mount: str = ""         # (moved parts) where it is


@dataclass
class AppPlan:
    app: AppEntry
    parts: list[MovePart] = field(default_factory=list)
    note: str = ""

    @property
    def movable(self) -> list[MovePart]:
        return [p for p in self.parts if not p.moved]

    @property
    def moved(self) -> list[MovePart]:
        return [p for p in self.parts if p.moved]


@dataclass
class MoveState:
    """What is already on external disks. Cheap to load; refreshed after every move."""
    registry: dict[str, relocate.Relocation] = field(default_factory=dict)
    user_moves: list[relocate.UserRelocation] = field(default_factory=list)
    installations: list[flatpak.Installation] = field(default_factory=list)

    @classmethod
    def load(cls) -> "MoveState":
        return cls(relocate.load_registry(), relocate.load_user_relocations(),
                   flatpak.custom_installations() if flatpak.available() else [])

    def user_move(self, path: str) -> relocate.UserRelocation | None:
        return next((m for m in self.user_moves if m.path == path), None)

    def installation(self, ident: str) -> flatpak.Installation | None:
        return next((i for i in self.installations if i.ident == ident), None)

    def app_status(self, app: AppEntry) -> tuple[str, str]:
        """(status, mount point) without expensive package queries."""
        moved: list[tuple[bool, str]] = []          # (connected, mount)
        remaining = 0
        if app.source == "flatpak":
            inst = self.installation(app.installation)
            if inst:
                moved.append((os.path.isdir(inst.path), _mount_of(inst.path)))
        else:
            rel = self.registry.get(app.ident)
            if rel:
                moved.append((rel.connected, rel.target_mount))
        for path in data_paths(app):
            m = self.user_move(path)
            if m:
                moved.append((m.connected, m.target_mount))
            elif os.path.isdir(path) and not os.path.islink(path):
                remaining += 1
        if not moved:
            return SYSTEM, ""
        mount = moved[0][1]
        if not all(c for c, _ in moved):
            return DISCONNECTED, mount
        return (PARTIAL if remaining else EXTERNAL), mount


def _mount_of(path: str) -> str:
    path = os.path.abspath(path)
    while path != "/" and not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def data_paths(app: AppEntry) -> list[str]:
    """Personal folders the app keeps its (large) data in."""
    out: list[str] = []
    definition = knowledge.for_package(app.ident)
    if definition:
        for raw in definition.data:
            path = str(untilde(raw))
            if path.startswith(str(HOME) + "/") and path not in out:
                out.append(path)
    if app.source == "flatpak":
        out.append(str(HOME / ".var/app" / app.ident))
    return out


def plan_app(app: AppEntry, state: MoveState) -> AppPlan:
    """Everything of `app` that can be (or already was) moved. Slow: call in the background."""
    plan = AppPlan(app)
    if app.source == "flatpak":
        inst = state.installation(app.installation)
        plan.parts.append(MovePart(
            FLATPAK, app.ident, app.size, tr("Application (Flatpak)"),
            moved=inst is not None, connected=inst is None or os.path.isdir(inst.path),
            mount=_mount_of(inst.path) if inst else ""))
    elif app.source in ("repo", "aur"):
        rel = state.registry.get(app.ident)
        if rel:
            for d in rel.dirs:
                plan.parts.append(MovePart(PROGRAM, d.path, d.size, _program_label(app, d.path),
                                           moved=True, connected=rel.connected,
                                           mount=rel.target_mount))
        else:
            cands = relocate.candidate_dirs(app.ident, pacman.package_files(app.ident),
                                            pacman.owners)
            for c in cands:
                plan.parts.append(MovePart(PROGRAM, c.path, c.size, _program_label(app, c.path)))
    for path in data_paths(app):
        m = state.user_move(path)
        if m:
            plan.parts.append(MovePart(DATA, path, m.size, _data_label(path), moved=True,
                                       connected=m.connected, mount=m.target_mount))
        elif os.path.isdir(path) and not os.path.islink(path):
            plan.parts.append(MovePart(DATA, path, dir_size(path), _data_label(path)))
    has_program = any(p.kind in (PROGRAM, FLATPAK) for p in plan.parts)
    if not plan.parts:
        plan.note = tr("This app has nothing that can be moved: its files are spread over "
                       "shared system folders and it keeps no large data of its own.")
    elif not has_program:
        plan.note = tr("Its program files are spread over shared system folders and stay on "
                       "the system disk; its data, usually the large part, can be moved.")
    return plan


def _program_label(app: AppEntry, path: str) -> str:
    if path in relocate.known_system_dirs(app.ident):
        return tr("Data: {path}").format(path=path)
    return tr("Program files: {path}").format(path=path)


def _data_label(path: str) -> str:
    rel = os.path.relpath(path, HOME)
    return tr("Data: {path}").format(path="~/" + rel)


# --------------------------------------------------------------------------- #
# Target disks
# --------------------------------------------------------------------------- #

@dataclass
class Target:
    drive: disks.Drive
    automount: str          # automount.NONE / OURS / FSTAB_ENTRY

    @property
    def mountpoint(self) -> str:
        return self.drive.mountpoint

    @property
    def mounts_by_itself(self) -> bool:
        return self.automount != automount.NONE

    @property
    def can_automount(self) -> bool:
        return (self.automount == automount.NONE
                and self.mountpoint.startswith(automount.ALLOWED_PREFIXES)
                and self.drive.fstype in automount.SUPPORTED_FS and bool(self.drive.uuid))

    def user_folder_ready(self) -> bool:
        """Can the user write their data folder on the disk without help from root?"""
        user = os.environ.get("USER") or HOME.name
        own = os.path.join(self.mountpoint, relocate.DISK_SUBDIR, "home", user)
        if os.path.isdir(own):
            return os.access(own, os.W_OK)
        probe = self.mountpoint
        for part in (relocate.DISK_SUBDIR, "home"):
            nxt = os.path.join(probe, part)
            if not os.path.isdir(nxt):
                break
            probe = nxt
        return os.access(probe, os.W_OK)


def targets() -> list[Target]:
    """Mounted disks an app can be moved to: Linux filesystems that are not the system or
    home disk itself."""
    out = []
    for d in disks.list_drives():
        if d.is_root or d.mountpoint in ("/home", "/var", "/usr", "/boot") or not d.native:
            continue
        if disks.same_filesystem(d.mountpoint, "/") or disks.same_filesystem(d.mountpoint,
                                                                             str(HOME)):
            continue
        out.append(Target(d, automount.status(d.mountpoint, d.uuid)))
    return out


def flatpak_installation_for(mountpoint: str, state: MoveState) -> flatpak.Installation | None:
    for inst in state.installations:
        if inst.path and _mount_of(inst.path) == mountpoint.rstrip("/"):
            return inst
    return None


def new_installation_ident(drive: disks.Drive) -> str:
    """Must match what the helper creates (flatpak-installation-add)."""
    name = drive.label or os.path.basename(drive.mountpoint) or "disk"
    return flatpak.LMP_PREFIX + flatpak.slug(name)
