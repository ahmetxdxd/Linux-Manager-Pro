"""Profile: application list + config backup, carried as a single .tar.gz file.

Archive layout:
    manifest.json             package lists, config records, source system info
    home/<relative path>       config files relative to the home directory
"""

from __future__ import annotations

import copy
import fnmatch
import io
import json
import os
import shutil
import socket
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from lmp import APP_ID, __version__
from lmp.core import flatpak, knowledge, pacman
from lmp.core.util import HOME, data_dir, dir_size, os_release, tilde
from lmp.i18n import tr

SCHEMA_VERSION = 2

# Files not worth carrying in a profile; they get regenerated
EXCLUDE_PATTERNS = [
    "*/Cache/*", "*/cache/*", "*/Code Cache/*", "*/GPUCache/*", "*/ShaderCache/*",
    "*/GrShaderCache/*", "*/Service Worker/CacheStorage/*", "*/cache2/*", "*/startupCache/*",
    "*/Crash Reports/*", "*/crashes/*", "*/.git/objects/*", "*/node_modules/*",
    "*/Singleton*", "*.lock", "*/lock", "*/.parentlock", "*/LOCK",
]

Progress = Callable[[int, str], None]


@dataclass
class ConfigCandidate:
    path: Path
    label: str
    size: int = 0
    sensitive: bool = False
    known: bool = False


@dataclass
class ProfileContent:
    repo: list[str] = field(default_factory=list)
    aur: list[str] = field(default_factory=list)
    flatpak: list[dict] = field(default_factory=list)   # {id, origin}
    configs: list[str] = field(default_factory=list)    # in "~/.config/x" form
    created_at: str = ""
    source_host: str = ""
    source_os: str = ""


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def current_packages() -> ProfileContent:
    content = ProfileContent()
    if pacman.available():
        content.repo = sorted(pacman.explicit_native_names())
        content.aur = sorted(pacman.explicit_foreign_names())
    content.flatpak = [{"id": a.app_id, "origin": a.origin or "flathub"}
                       for a in flatpak.list_apps()]
    return content


def config_candidates(with_sizes: bool = True) -> list[ConfigCandidate]:
    """Configs of known applications + everything under ~/.config."""
    out: dict[str, ConfigCandidate] = {}
    installed = set(pacman.installed_versions()) if pacman.available() else set()
    installed |= {a.app_id for a in flatpak.list_apps()}
    for d in knowledge.definitions():
        if d.packages and not (set(d.packages) & installed):
            continue
        for raw in d.config:
            p = Path(os.path.expanduser(raw))
            if p.exists() and str(p) not in out:
                out[str(p)] = ConfigCandidate(p, tr(d.name), sensitive=d.sensitive, known=True)
    cfg = HOME / ".config"
    if cfg.is_dir():
        for child in sorted(cfg.iterdir(), key=lambda c: c.name.lower()):
            if child.name in (APP_ID, "autostart") or str(child) in out:
                continue
            out[str(child)] = ConfigCandidate(child, child.name)
    autostart = cfg / "autostart"
    if autostart.is_dir():
        out[str(autostart)] = ConfigCandidate(autostart, tr("Startup applications"), known=True)
    result = list(out.values())
    if with_sizes:
        for c in result:
            c.size = dir_size(c.path)
    return result


def _excluded(rel: str) -> bool:
    probe = "/" + rel
    return any(fnmatch.fnmatch(probe, pat) for pat in EXCLUDE_PATTERNS)


def export_profile(dest: Path, content: ProfileContent, config_paths: list[Path],
                   progress: Progress | None = None) -> Path:
    dest = Path(dest)
    if not dest.name.endswith((".tar.gz", ".tgz")):
        dest = dest.with_name(dest.name + ".lmp.tar.gz")
    info = os_release()
    content.created_at = datetime.now().isoformat(timespec="seconds")
    content.source_host = socket.gethostname()
    content.source_os = info.get("PRETTY_NAME", info.get("ID", ""))

    files: list[tuple[Path, str]] = []
    configs: list[str] = []
    for root in config_paths:
        root = Path(root)
        if not root.exists():
            continue
        try:
            rel_root = root.relative_to(HOME)
        except ValueError:
            continue
        configs.append(tilde(root))
        if root.is_file() or root.is_symlink():
            files.append((root, str(rel_root)))
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for name in filenames:
                full = Path(dirpath) / name
                rel = str(full.relative_to(HOME))
                if not _excluded(rel):
                    files.append((full, rel))
    content.configs = configs

    manifest = {
        "schema_version": SCHEMA_VERSION, "app": APP_ID, "app_version": __version__,
        "created_at": content.created_at, "source_host": content.source_host,
        "source_os": content.source_os,
        "packages": {"repo": content.repo, "aur": content.aur, "flatpak": content.flatpak},
        "configs": content.configs,
    }
    tmp = dest.with_name(dest.name + ".part")
    total = len(files) or 1
    with tarfile.open(tmp, "w:gz", compresslevel=6) as tar:
        data = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(data)
        ti.mtime = int(datetime.now().timestamp())
        tar.addfile(ti, io.BytesIO(data))
        for i, (full, rel) in enumerate(files, 1):
            try:
                tar.add(str(full), arcname=f"home/{rel}", recursive=False)
            except OSError:
                continue
            if progress and (i % 50 == 0 or i == total):
                progress(int(i * 100 / total), rel)
    os.replace(tmp, dest)
    return dest


def export_text_list(dest: Path, content: ProfileContent) -> Path:
    lines = [f"# {APP_ID} application list · {datetime.now():%Y-%m-%d %H:%M}",
             "# Format: source:name  (source = repo, aur, flatpak)"]
    lines += [f"repo:{n}" for n in content.repo]
    lines += [f"aur:{n}" for n in content.aur]
    lines += [f"flatpak:{f['id']}" for f in content.flatpak]
    Path(dest).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(dest)


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

def read_text_list(path: Path) -> ProfileContent:
    content = ProfileContent()
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        source, _, name = line.partition(":") if ":" in line else ("repo", "", line)
        name = name.strip()
        if not pacman.NAME_RE.match(name):
            continue
        if source == "aur":
            content.aur.append(name)
        elif source == "flatpak":
            content.flatpak.append({"id": name, "origin": "flathub"})
        else:
            content.repo.append(name)
    return content


def read_profile(path: Path) -> ProfileContent:
    with tarfile.open(path, "r:*") as tar:
        member = tar.getmember("manifest.json")
        f = tar.extractfile(member)
        if f is None:
            raise ValueError(tr("Could not read manifest.json"))
        manifest = json.loads(f.read().decode("utf-8"))
    if manifest.get("app") != APP_ID:
        raise ValueError(tr("This file is not a Linux Manager Pro profile."))
    pk = manifest.get("packages", {})
    return ProfileContent(
        repo=list(pk.get("repo", [])), aur=list(pk.get("aur", [])),
        flatpak=list(pk.get("flatpak", [])), configs=list(manifest.get("configs", [])),
        created_at=manifest.get("created_at", ""), source_host=manifest.get("source_host", ""),
        source_os=manifest.get("source_os", ""),
    )


@dataclass
class RestorePlan:
    repo_missing: list[str] = field(default_factory=list)
    repo_unavailable: list[str] = field(default_factory=list)
    aur_missing: list[str] = field(default_factory=list)
    flatpak_missing: list[dict] = field(default_factory=list)
    already_installed: int = 0
    configs: list[str] = field(default_factory=list)


def plan_restore(content: ProfileContent) -> RestorePlan:
    plan = RestorePlan(configs=list(content.configs))
    installed = set(pacman.installed_versions()) if pacman.available() else set()
    sync = pacman.sync_names() if pacman.available() else set()
    for name in content.repo:
        if name in installed:
            plan.already_installed += 1
        elif name in sync:
            plan.repo_missing.append(name)
        else:
            plan.repo_unavailable.append(name)
    for name in content.aur:
        if name in installed:
            plan.already_installed += 1
        elif name in sync:
            plan.repo_missing.append(name)   # moved from the AUR to an official repo
        else:
            plan.aur_missing.append(name)
    flatpaks = {a.app_id for a in flatpak.list_apps()}
    for item in content.flatpak:
        if item.get("id") in flatpaks:
            plan.already_installed += 1
        else:
            plan.flatpak_missing.append(item)
    return plan


def aur_install_cmd(helper: str, names: list[str]) -> list[str]:
    """Non-interactive install through the AUR helper. pkexec instead of sudo since there is no terminal."""
    cmd = [helper, "-S", "--needed", "--noconfirm", "--sudo", "pkexec"]
    if helper == "paru":
        cmd.append("--skipreview")
    else:
        cmd += ["--answerdiff", "None", "--answerclean", "None", "--answeredit", "None"]
    return cmd + names


def _safe_member(member: tarfile.TarInfo) -> bool:
    name = member.name
    if not name.startswith("home/") or name.startswith("/") or ".." in Path(name).parts:
        return False
    if member.islnk() or member.isdev():
        return False
    if member.issym():
        target = member.linkname
        if target.startswith("/") and not target.startswith(str(HOME)):
            return False
    return True


def _config_rel(entry: str) -> str:
    """'~/.config/x' → '.config/x'. Rejects anything that is not strictly inside the home
    directory (a profile file may come from elsewhere, so its manifest is not trusted)."""
    rel = Path(os.path.expanduser(entry))
    try:
        rel = rel.relative_to(HOME)
    except ValueError:
        raise ValueError(tr("Not inside the home directory: {path}").format(path=entry)) from None
    parts = rel.parts
    containers = {".local", ".local/share", ".config", ".cache"}   # would hold the backup itself
    if (not parts or ".." in parts or str(rel) in containers
            or str(rel).startswith((".local/share/" + APP_ID, ".config/" + APP_ID))):
        raise ValueError(tr("Not inside the home directory: {path}").format(path=entry))
    return str(rel)


def restore_configs(path: Path, selected: list[str],
                    progress: Progress | None = None) -> Path:
    """Restores the selected configs. Existing files are backed up first; returns the backup folder.

    If extracting fails, the partly restored folders are removed and the backed-up originals
    are put back, so the user never ends up with half a configuration.
    """
    backup = data_dir() / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    rels = [_config_rel(s) for s in selected]          # validate everything before touching
    prefixes = ["home/" + rel for rel in rels]
    # locations whose original is now safe in the backup (or that did not exist before)
    processed: list[str] = []
    try:
        for rel in rels:
            current = HOME / rel
            if os.path.lexists(current):
                target = backup / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(target))
            processed.append(rel)
        with tarfile.open(path, "r:*") as tar:
            members = [m for m in tar.getmembers()
                       if _safe_member(m) and any(m.name == p or m.name.startswith(p + "/")
                                                  for p in prefixes)]
            total = len(members) or 1
            for i, m in enumerate(members, 1):
                m_copy = copy.copy(m)
                m_copy.name = m.name[len("home/"):]
                if hasattr(tarfile, "data_filter"):
                    tar.extract(m_copy, path=HOME, filter="data")
                else:
                    tar.extract(m_copy, path=HOME)
                if progress and (i % 50 == 0 or i == total):
                    progress(int(i * 100 / total), m_copy.name)
    except BaseException:
        for rel in processed:
            current = HOME / rel
            if current.is_dir() and not current.is_symlink():
                shutil.rmtree(current, ignore_errors=True)       # partly extracted
            elif os.path.lexists(current):
                current.unlink()
            if os.path.lexists(backup / rel):
                shutil.move(str(backup / rel), str(current))
        raise
    return backup
