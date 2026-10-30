"""Flatpak applications and installations."""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from lmp.core.util import HOME, have, parse_size, run
from lmp.i18n import tr

INSTALLATIONS_DIR = Path("/etc/flatpak/installations.d")
LMP_PREFIX = "lmp-"
APP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class FlatpakApp:
    app_id: str
    name: str
    version: str = ""
    branch: str = ""
    installation: str = "system"    # system / user / custom installation id
    origin: str = ""
    size: int = 0

    @property
    def install_flag(self) -> str:
        if self.installation == "user":
            return "--user"
        if self.installation == "system":
            return "--system"
        return f"--installation={self.installation}"


@dataclass
class Installation:
    ident: str          # "system", "user" or a custom id
    path: str
    display_name: str
    custom: bool = False

    @property
    def flag(self) -> str:
        if self.ident == "user":
            return "--user"
        if self.ident == "system":
            return "--system"
        return f"--installation={self.ident}"


def available() -> bool:
    return have("flatpak")


def parse_list(text: str) -> list[FlatpakApp]:
    apps = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2 or not parts[0]:
            continue
        parts += [""] * (7 - len(parts))
        apps.append(FlatpakApp(
            app_id=parts[0], name=parts[1] or parts[0], version=parts[2], branch=parts[3],
            installation=parts[4] or "system", size=parse_size(parts[5]), origin=parts[6],
        ))
    return apps


def list_apps() -> list[FlatpakApp]:
    if not available():
        return []
    res = run(["flatpak", "list", "--app",
               "--columns=application,name,version,branch,installation,size,origin"])
    return parse_list(res.out) if res.ok else []


def custom_installations() -> list[Installation]:
    out = []
    if not INSTALLATIONS_DIR.is_dir():
        return out
    for conf in sorted(INSTALLATIONS_DIR.glob("*.conf")):
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(conf)
        except configparser.Error:
            continue
        for section in parser.sections():
            m = re.match(r'Installation\s+"(.+)"', section)
            if not m:
                continue
            ident = m.group(1)
            out.append(Installation(
                ident=ident,
                path=parser.get(section, "Path", fallback=""),
                display_name=parser.get(section, "DisplayName", fallback=ident),
                custom=True,
            ))
    return out


def installations() -> list[Installation]:
    result = [
        Installation("system", "/var/lib/flatpak", tr("System")),
        Installation("user", str(HOME / ".local/share/flatpak"), tr("User")),
    ]
    return result + custom_installations()


def uninstall_cmd(app: FlatpakApp) -> list[str]:
    # User data (~/.var/app) is not deleted here; the leftover scanner shows it
    # and the user chooses.
    return ["flatpak", "uninstall", "-y", "--noninteractive", app.install_flag, app.app_id]


def install_cmd(app_id: str, remote: str = "flathub", installation: str = "system") -> list[str]:
    flag = Installation(installation, "", "").flag
    return ["flatpak", "install", "-y", "--noninteractive", flag, remote, app_id]


def installation_conf_text(ident: str, path: str, display_name: str) -> str:
    return (
        f'[Installation "{ident}"]\n'
        f"Path={path}\n"
        f"DisplayName={display_name}\n"
        "StorageType=harddisk\n"
    )


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "disk"
