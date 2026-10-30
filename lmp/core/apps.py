"""Merges pacman, AUR and Flatpak applications into one list."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lmp.core import flatpak, pacman
from lmp.core.desktop import DesktopEntry, index_applications
from lmp.core.util import HOME
from lmp.i18n import tr, tr_noop

SOURCE_LABELS = {"repo": tr_noop("Official repo"), "aur": "AUR", "flatpak": "Flatpak"}


@dataclass
class AppEntry:
    ident: str                  # package name or Flatpak application id
    name: str
    source: str                 # repo / aur / flatpak
    version: str = ""
    size: int = 0
    install_date: datetime | None = None
    description: str = ""
    url: str = ""
    explicit: bool = True
    icon: str = ""
    desktop_entries: list[DesktopEntry] = field(default_factory=list)
    required_by: list[str] = field(default_factory=list)
    installation: str = ""      # Flatpak only
    flatpak_app: flatpak.FlatpakApp | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.installation}:{self.ident}"

    @property
    def has_gui(self) -> bool:
        return any(not e.no_display for e in self.desktop_entries)

    @property
    def source_label(self) -> str:
        return tr(SOURCE_LABELS.get(self.source, self.source))

    @property
    def primary_desktop(self) -> DesktopEntry | None:
        visible = [e for e in self.desktop_entries if not e.no_display]
        return (visible or self.desktop_entries or [None])[0]


def _primary_first(pkg: str, entries: list[DesktopEntry]) -> list[DesktopEntry]:
    def score(e: DesktopEntry) -> tuple:
        stem = e.desktop_id[:-8].lower()
        return (e.no_display, not (stem == pkg or stem.endswith("." + pkg)), stem)
    return sorted(entries, key=score)


def load_apps() -> list[AppEntry]:
    """All installed packages + Flatpak apps. Can be slow; call it in the background."""
    desktop_index = index_applications()
    apps: list[AppEntry] = []

    if pacman.available():
        packages = pacman.installed_packages()
        # Entries in the home directory and Flatpak exports cannot belong to pacman
        system_desktops = [str(e.path) for e in desktop_index.values()
                           if not str(e.path).startswith(str(HOME))
                           and "/flatpak/exports/" not in str(e.path)]
        owner_map = pacman.owners(system_desktops)
        by_pkg: dict[str, list[DesktopEntry]] = {}
        for path, owner_set in owner_map.items():
            entry = desktop_index.get(Path(path).name)
            if entry is None:
                continue
            for owner in owner_set:
                by_pkg.setdefault(owner, []).append(entry)
        for p in packages:
            entries = _primary_first(p.name, by_pkg.get(p.name, []))
            primary = next((e for e in entries if not e.no_display), None)
            apps.append(AppEntry(
                ident=p.name,
                name=primary.name if primary else p.name,
                source="aur" if p.foreign else "repo",
                version=p.version, size=p.size, install_date=p.install_date,
                description=p.description, url=p.url, explicit=p.explicit,
                icon=primary.icon if primary else "",
                desktop_entries=entries, required_by=p.required_by,
            ))

    for fp in flatpak.list_apps():
        entry = desktop_index.get(f"{fp.app_id}.desktop")
        apps.append(AppEntry(
            ident=fp.app_id, name=fp.name, source="flatpak", version=fp.version,
            size=fp.size, description=entry.comment if entry else "",
            icon=(entry.icon if entry else "") or fp.app_id,
            desktop_entries=[entry] if entry else [],
            installation=fp.installation, flatpak_app=fp,
        ))

    apps.sort(key=lambda a: a.name.lower())
    return apps
