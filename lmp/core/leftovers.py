"""Leftover file scanner.

Confidence-based matching instead of naive short-substring matching:

* EXACT    – name is identical to the application id (e.g. ~/.config/obs-studio, ~/.var/app/<id>)
* PROBABLE – starts with the application name followed by a separator (e.g. ~/.cache/spotify-launcher)
* LOW      – only in the "orphaned leftovers" scan: belongs to no installed application

Paths that also match another installed application are never listed. Only EXACT
matches are pre-selected by default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from lmp.core import desktop, knowledge, pacman
from lmp.core.apps import AppEntry
from lmp.core.util import HOME, dir_size, run, untilde
from lmp.i18n import tr, tr_noop

EXACT, PROBABLE, LOW = "exact", "probable", "low"
CONFIDENCE_LABELS = {EXACT: tr_noop("Exact"), PROBABLE: tr_noop("Probable"), LOW: tr_noop("Low")}

_SUFFIXES = ("-bin", "-git", "-appimage", "-nightly", "-beta", "-stable", "-dev",
             "-wayland", "-electron", "-launcher", "-desktop", "-full", "-qt", "-gtk")
_FILE_SUFFIXES = (".conf", ".json", ".ini", ".toml", ".yaml", ".yml", ".xml", ".lock",
                  ".log", ".db", ".sqlite", ".cfg", ".kcache", "rc", ".rc")
_SEPARATORS = "-_. "

# Display names / desktop fragments that are meaninglessly generic
_GENERIC_WORDS = {
    "app", "apps", "client", "desktop", "settings", "terminal", "editor", "files", "browser",
    "calculator", "monitor", "system", "player", "viewer", "manager", "launcher", "studio",
    "tools", "utils", "config", "data", "cache", "share", "local", "plugin", "plugins",
    "linux", "gnome", "kde", "org", "com", "net", "io", "github", "the", "main", "core",
}

# Generic folders/files never suggested by the orphan scan (normalised names)
_ORPHAN_ALLOW = {
    "autostart", "systemd", "environmentd", "userdirsdirs", "userdirslocale", "userdirs",
    "mimeappslist", "mimeapps", "menus", "applications", "icons", "fonts", "fontconfig", "themes",
    "colorschemes", "gtk", "gtk20", "gtk30", "gtk40", "gtkrc", "gtkrc20", "dconf", "pulse",
    "pipewire", "wireplumber", "xdgdesktopportal", "trash", "recentlyusedxbel", "recentlyused",
    "keyrings", "gvfsmetadata", "mime", "sounds", "desktopdirectories", "kservices", "kservicetypes",
    "kxmlgui", "kio", "session", "sessions", "trolltech", "qtproject", "kdeglobals", "kdedefaults",
    "xsettingsd", "bashhistory", "lesshst", "pythonhistory", "wgethsts", "xauthority",
    "xsessionerrors", "gnupg", "ssh", "pki", "fonts", "local", "config", "cache", "var", "profile",
    "bashrc", "bashprofile", "bashlogout", "zshrc", "zshenv", "zprofile", "zhistory", "gitconfig",
    "directory", "java", "dbus", "nv", "mesashadercache", "mesashadercachedb", "thumbnails",
    "eventsoundcache", "qtshadercache", "ksycoca", "iconcache", "kcrash", "drkonqi", "plasma",
    "plasmashell", "kwin", "kactivitymanagerd", "baloo", "akonadi", "evolution", "tracker",
    "tracker3", "recently", "user", "state", "bin", "lib", "include", "man", "doc", "nano",
    "vim", "viminfo", "selectededitor", "sudoasadminsuccessful", "xinitrc", "xprofile",
    "xresources", "xdefaults", "npmrc", "yarnrc", "inputrc", "dmrc", "face", "faceicon",
    "pam", "esd", "icons", "linuxmanagerpro", "flatpak", "pacman", "paru", "yay",
}

# Interpreters/launchers that appear in Exec lines but are not the application itself
_LAUNCHER_BINARIES = re.compile(
    r"^(sh|bash|zsh|fish|dash|env|python[0-9.]*|java|node|nodejs|electron[0-9]*|wine|wine64|"
    r"mono|perl|ruby|lua|flatpak|gtk-launch|xdg-open|sudo|pkexec|kdesu|kioclient[0-9]*|"
    r"systemctl|dbus-send|gio|appimagelauncher|steam-runtime|bwrap)$"
)

_HOME_SKIP = {".config", ".local", ".cache", ".var", ".ssh", ".gnupg", ".pki", ".icons",
              ".themes", ".fonts", ".mozilla", ".steam"}


@dataclass
class Leftover:
    path: Path
    kind: str
    confidence: str
    app: str
    size: int = 0
    system: bool = False
    reason: str = ""
    modified: datetime | None = None

    @property
    def default_checked(self) -> bool:
        return self.confidence == EXACT

    @property
    def confidence_label(self) -> str:
        return tr(CONFIDENCE_LABELS[self.confidence])


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def norm_loose(text: str) -> str:
    """Also drops trailing version digits: kded5 ≈ kded6 ≈ kded."""
    return norm(text).rstrip("0123456789") or norm(text)


def strip_pkg_suffixes(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if name.endswith(suf) and len(name) > len(suf) + 2:
                name = name[: -len(suf)]
                changed = True
    return name


def strip_file_suffixes(name: str) -> str:
    low = name.lower().lstrip(".")
    for suf in _FILE_SUFFIXES:
        if low.endswith(suf) and len(low) > len(suf) + 2:
            return low[: -len(suf)]
    return low


@dataclass
class _Keys:
    strong: set[str]           # normalised, for exact matching
    prefixes: set[str]         # raw lowercase, for "key + separator" prefix matching


def app_keys(app: AppEntry) -> _Keys:
    strong: set[str] = set()
    prefixes: set[str] = set()

    def add(raw: str, prefix: bool = True, min_len: int = 3):
        raw = raw.strip().lower()
        if len(raw) < min_len or norm(raw) in _GENERIC_WORDS:
            return
        strong.add(norm(raw))
        if prefix and len(raw) >= 4:
            prefixes.add(raw)

    if app.source == "flatpak":
        add(app.ident, prefix=False)
        return _Keys(strong, prefixes)

    add(app.ident)
    add(strip_pkg_suffixes(app.ident))
    for entry in app.desktop_entries:
        stem = entry.desktop_id[:-8]
        add(stem)
        last = stem.split(".")[-1]
        if last != stem:
            add(last)
        if entry.binary and not _LAUNCHER_BINARIES.match(entry.binary.lower()):
            add(entry.binary)
        if entry.wm_class:
            add(entry.wm_class)
    if app.name and len(norm(app.name)) >= 4:
        add(app.name, prefix=False, min_len=4)
    return _Keys(strong, prefixes)


def _cached_keys(app: AppEntry, cache: dict[str, _Keys] | None) -> _Keys:
    if cache is None:
        return app_keys(app)
    keys = cache.get(app.key)
    if keys is None:
        keys = cache[app.key] = app_keys(app)
    return keys


def _other_keys(apps: Iterable[AppEntry], exclude: set[str],
                cache: dict[str, _Keys] | None = None) -> tuple[set[str], set[str]]:
    strong: set[str] = set()
    prefixes: set[str] = set()
    for a in apps:
        if a.key in exclude:
            continue
        k = _cached_keys(a, cache)
        strong |= k.strong
        prefixes |= k.prefixes
    return strong, prefixes


def _classify(name: str, keys: _Keys) -> str | None:
    base = strip_file_suffixes(name)
    if norm(base) in keys.strong or norm(name.lstrip(".")) in keys.strong:
        return EXACT
    for p in keys.prefixes:
        if len(base) > len(p) and base.startswith(p) and base[len(p)] in _SEPARATORS:
            return PROBABLE
    return None


def _owned_by_other(name: str, match: str, other_strong: set[str], other_prefixes: set[str],
                    own_prefixes: set[str]) -> bool:
    base = strip_file_suffixes(name)
    if norm(base) in other_strong:
        return True
    if match == PROBABLE:
        # e.g. "obs" is removed but "obs-studio" is still installed
        longest_own = max((len(p) for p in own_prefixes if base.startswith(p)), default=0)
        for p in other_prefixes:
            if len(p) > longest_own and (base == p or (base.startswith(p) and
                                                       base[len(p):len(p) + 1] in ("", *_SEPARATORS))):
                return True
    return False


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.lstat().st_mtime)
    except OSError:
        return None


USER_ROOTS = [
    (".config", tr_noop("Configuration")),
    (".local/share", tr_noop("Data")),
    (".local/state", tr_noop("State")),
    (".cache", tr_noop("Cache")),
]
SYSTEM_ROOTS = [
    ("/etc", tr_noop("System configuration")),
    ("/var/lib", tr_noop("System data")),
    ("/var/cache", tr_noop("System cache")),
    ("/var/log", tr_noop("System log")),
    ("/opt", tr_noop("Application folder")),
    ("/usr/share", tr_noop("Shared data")),
    ("/usr/lib", tr_noop("Library folder")),
]


def _iter_dir(path: Path):
    try:
        with os.scandir(path) as it:
            for entry in it:
                yield Path(entry.path)
    except OSError:
        return


def scan(app: AppEntry, all_apps: list[AppEntry], *, still_installed: Iterable[str] = (),
         include_system: bool = True,
         progress: Callable[[int | None, str], None] | None = None,
         keys_cache: dict | None = None) -> list[Leftover]:
    """Finds leftovers for `app` (already removed or about to be removed).

    `all_apps`: the application list from BEFORE the removal. `still_installed`: keys of
    applications still installed afterwards; if empty, everything in all_apps except `app` is assumed.
    `keys_cache`: pass the same dict when scanning several apps in a row, so the match keys of
    every installed application are computed only once.
    """
    keys = _cached_keys(app, keys_cache)
    excluded = {app.key} | ({a.key for a in all_apps} - set(still_installed)
                            if still_installed else set())
    other_strong, other_prefixes = _other_keys(all_apps, excluded, keys_cache)
    found: dict[str, Leftover] = {}

    def add(path: Path, kind: str, confidence: str, system: bool = False, reason: str = ""):
        key = str(path)
        if key in found or not os.path.lexists(path):
            return
        found[key] = Leftover(path=path, kind=tr(kind), confidence=confidence, app=app.name,
                              system=system, reason=tr(reason) if reason else "",
                              modified=_mtime(path))

    if progress:
        progress(None, tr("{app}: scanning user folders").format(app=app.name))

    # 1) Flatpak sandbox
    if app.source == "flatpak":
        add(HOME / ".var/app" / app.ident, "Flatpak data", EXACT)

    # 2) XDG roots
    for rel, kind in USER_ROOTS:
        for child in _iter_dir(HOME / rel):
            match = _classify(child.name, keys)
            if match and not _owned_by_other(child.name, match, other_strong, other_prefixes,
                                             keys.prefixes):
                add(child, kind, match)

    # 3) Hidden folders in the home directory (~/.appname)
    for child in _iter_dir(HOME):
        if not child.name.startswith(".") or child.name in _HOME_SKIP:
            continue
        match = _classify(child.name, keys)
        if match and not _owned_by_other(child.name, match, other_strong, other_prefixes,
                                         keys.prefixes):
            add(child, "Hidden folder", match)

    # 4) Known application definitions
    definition = knowledge.for_package(app.ident)
    if definition:
        installed_idents = {a.ident for a in all_apps if a.key not in excluded}
        sharing = [p for p in definition.packages if p in installed_idents and p != app.ident]
        for raw in definition.all_paths:
            path = untilde(raw)
            system = not str(path).startswith(str(HOME) + "/")
            if system and (not include_system or app.source == "flatpak"):
                continue
            if sharing:
                add(path, "Application data", LOW, system=system,
                    reason=tr("{pkgs} still installed; may be shared").format(pkgs=", ".join(sharing)))
            else:
                add(path, "Application data", EXACT, system=system,
                    reason="Known application folder")

    # 5) Startup entries, user launchers and user services
    binaries = {norm(e.binary) for e in app.desktop_entries
                if e.binary and not _LAUNCHER_BINARIES.match(e.binary.lower())} | {norm(app.ident)}
    for f in _iter_dir(HOME / ".config/autostart"):
        if f.suffix != ".desktop":
            continue
        entry = desktop.parse(f)
        hit = _classify(f.name[:-8], keys) or (entry and norm(entry.binary) in binaries
                                                and EXACT)
        if hit:
            add(f, "Startup entry", EXACT)
    for f in _iter_dir(HOME / ".local/share/applications"):
        if f.suffix != ".desktop":
            continue
        entry = desktop.parse(f)
        if _classify(f.name[:-8], keys) or (entry and (norm(entry.binary) in binaries or
                                                         app.ident in entry.exec.split())):
            add(f, "User launcher", PROBABLE)
    user_units = HOME / ".config/systemd/user"
    for f in _iter_dir(user_units):
        if f.suffix in (".service", ".timer", ".socket") and _classify(f.stem, keys) == EXACT:
            add(f, "User service", EXACT)
        elif f.is_dir() and f.name.endswith(".wants"):
            for link in _iter_dir(f):
                if _classify(link.stem, keys) == EXACT:
                    add(link, "User service link", EXACT)

    # 5b) User icons and binaries dropped into ~/.local
    for f in _user_icon_files(keys):
        add(f, "User icon", EXACT)
    for f in _iter_dir(HOME / ".local/bin"):
        if norm(f.name) in binaries:
            add(f, "User program", PROBABLE)

    # 6) System locations (pacman packages only, exact name, only if unowned)
    if include_system and app.source != "flatpak":
        if progress:
            progress(None, tr("{app}: scanning system folders").format(app=app.name))
        names = {app.ident, strip_pkg_suffixes(app.ident)}
        candidates = [Path(root) / n for root, _ in SYSTEM_ROOTS for n in names]
        existing = [c for c in candidates if os.path.lexists(c)]
        if existing:
            owned = pacman.owners(str(c) for c in existing)
            kind_of = dict(SYSTEM_ROOTS)
            for c in existing:
                if str(c) not in owned:
                    add(c, kind_of[str(c.parent)], EXACT, system=True,
                        reason="No package owns it")
        for f in _pacsave_files(names):
            add(f, "Saved old configuration (.pacsave)", PROBABLE, system=True)
        for f in _dangling_unit_links(keys):
            add(f, "Broken service link", EXACT, system=True,
                reason="Points to a service that no longer exists")

    if progress:
        progress(None, tr("{app}: calculating sizes").format(app=app.name))
    for item in found.values():
        item.size = dir_size(item.path)
    order = {EXACT: 0, PROBABLE: 1, LOW: 2}
    return sorted(found.values(), key=lambda x: (order[x.confidence], x.system, -x.size))


def _user_icon_files(keys: _Keys) -> list[Path]:
    """~/.local/share/icons/<theme>/<size>/apps/<name>.png and friends."""
    out = []
    root = HOME / ".local/share/icons"
    for theme_dir in _iter_dir(root):
        if theme_dir.is_file():
            if norm(theme_dir.stem) in keys.strong:
                out.append(theme_dir)
            continue
        for size_dir in _iter_dir(theme_dir):
            apps_dir = size_dir / "apps"
            for icon in _iter_dir(apps_dir):
                if norm(icon.stem) in keys.strong:
                    out.append(icon)
    return out


def _dangling_unit_links(keys: _Keys) -> list[Path]:
    """Enabled-at-boot links in /etc/systemd/system whose unit file is gone."""
    out = []
    base = Path("/etc/systemd/system")
    for wants in _iter_dir(base):
        if not wants.is_dir() or not wants.name.endswith((".wants", ".requires")):
            continue
        for link in _iter_dir(wants):
            if link.is_symlink() and not link.exists() and _classify(link.stem, keys) == EXACT:
                out.append(link)
    return out


def _pacsave_files(names: set[str]) -> list[Path]:
    out = []
    norm_names = {norm(n) for n in names if len(n) >= 3}
    for root, dirs, files in os.walk("/etc"):
        for f in files:
            if f.endswith(".pacsave") or f.endswith(".pacnew"):
                full = Path(root) / f
                parts = {norm(p) for p in full.parts} | {norm(strip_file_suffixes(f.split(".pac")[0]))}
                if parts & norm_names:
                    out.append(full)
        if root.count("/") > 3:
            dirs.clear()
    return out


# --------------------------------------------------------------------------- #
# Orphaned-leftover scan (what previously removed applications left behind)
# --------------------------------------------------------------------------- #

def _known_names(all_apps: list[AppEntry]) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    prefixes: set[str] = set()
    for a in all_apps:
        k = app_keys(a)
        exact |= k.strong
        prefixes |= k.prefixes
        if a.source == "flatpak":
            exact |= {norm(part) for part in a.ident.split(".") if len(part) >= 3}
    bin_dirs = [Path("/usr/bin"), HOME / ".local/bin", Path("/usr/local/bin")]
    for d in bin_dirs:
        for f in _iter_dir(d):
            exact.add(norm(f.name))
    for p in pacman.installed_versions() if pacman.available() else {}:
        exact.add(norm(p))
        exact.add(norm(strip_pkg_suffixes(p)))
    exact |= _ORPHAN_ALLOW
    exact.discard("")
    return exact, prefixes


def scan_orphans(all_apps: list[AppEntry],
                 progress: Callable[[int | None, str], None] | None = None) -> list[Leftover]:
    """User folders that belong to no installed application. All are LOW confidence."""
    exact, prefixes = _known_names(all_apps)
    exact_loose = {e.rstrip("0123456789") for e in exact if len(e) >= 3}
    known_paths = set()
    for d in knowledge.definitions():
        if any(a.ident in d.packages for a in all_apps):
            known_paths |= {str(untilde(p)) for p in d.all_paths}

    def owned(name: str) -> bool:
        base = strip_file_suffixes(name)
        n = norm(base)
        if not n or n in exact or norm_loose(base) in exact_loose:
            return True
        for p in prefixes:
            if base.startswith(p) and (len(base) == len(p) or base[len(p)] in _SEPARATORS):
                return True
        # a "jetbrains" folder ↔ the "jetbrains-toolbox" package
        if len(n) >= 5 and any(e.startswith(n) for e in exact if len(e) > len(n)):
            return True
        return False

    out: list[Leftover] = []
    roots = USER_ROOTS + [("", tr_noop("Hidden folder"))]
    for rel, kind in roots:
        root = HOME / rel if rel else HOME
        if progress:
            progress(None, tr("Scanning: ~/{rel}").format(rel=rel))
        for child in _iter_dir(root):
            if not rel and (not child.name.startswith(".") or child.name in _HOME_SKIP):
                continue
            if str(child) in known_paths or owned(child.name):
                continue
            out.append(Leftover(path=child, kind=tr(kind), confidence=LOW, app=tr("(orphaned)"),
                                reason=tr("Matches no installed application"),
                                modified=_mtime(child)))
    if progress:
        progress(None, tr("Calculating sizes"))
    for item in out:
        item.size = dir_size(item.path)
    return sorted(out, key=lambda x: -x.size)


# --------------------------------------------------------------------------- #
# Footprint: what an installed application occupies (shown before removal)
# --------------------------------------------------------------------------- #

@dataclass
class Footprint:
    files: int = 0
    size: int = 0
    folders: list[tuple[str, int]] = field(default_factory=list)   # biggest locations
    services: list[str] = field(default_factory=list)              # systemd units it ships
    dependencies: list[str] = field(default_factory=list)          # removed with it (-Rns)
    location: str = ""                                             # Flatpak install path


def _group_package_file(path: str) -> str:
    """/usr/share/obs/data/x.png -> /usr/share/obs ; /usr/bin/obs -> /usr/bin/obs"""
    parts = path.strip("/").split("/")
    if len(parts) <= 3:
        return "/" + "/".join(parts)
    if parts[0] == "usr" and parts[1] in ("lib", "share", "lib32", "include"):
        return "/" + "/".join(parts[:3])
    return "/" + "/".join(parts[:2])


def footprint(app: AppEntry, limit: int = 8) -> Footprint:
    """Everything the application itself owns (its package files, units and dependencies)."""
    fp = Footprint()
    if app.source == "flatpak":
        flag = app.flatpak_app.install_flag if app.flatpak_app else "--system"
        res = run(["flatpak", "info", flag, "--show-location", app.ident], timeout=20)
        if res.ok and res.out.strip():
            fp.location = res.out.strip()
            fp.size = dir_size(fp.location)
            fp.folders = [(fp.location, fp.size)]
        return fp

    sizes: dict[str, int] = {}
    for raw in pacman.package_files(app.ident):
        if raw.endswith("/"):
            continue
        fp.files += 1
        if raw.startswith(("/usr/lib/systemd/system/", "/usr/lib/systemd/user/")) \
                and raw.endswith((".service", ".timer", ".socket")):
            fp.services.append(Path(raw).name)
        try:
            st = os.lstat(raw)
        except OSError:
            continue
        fp.size += st.st_size
        group = _group_package_file(raw)
        sizes[group] = sizes.get(group, 0) + st.st_size
    fp.folders = sorted(sizes.items(), key=lambda kv: -kv[1])[:limit]
    res = run(["pacman", "-Rnsp", "--print-format", "%n", app.ident], timeout=60)
    if res.ok:
        fp.dependencies = [n for n in res.out.split() if n and n != app.ident]
    return fp
