"""Persistent application settings (~/.config/linux-manager-pro/settings.json)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from lmp.core.builder import BuildOptions
from lmp.core.util import config_dir, load_json, save_json


@dataclass
class Settings:
    language: str = "en"                # en / tr
    theme: str = "dark"                 # dark / light
    delete_mode: str = "trash"          # trash / permanent
    aur_helper: str = "auto"            # auto / yay / paru / none
    scan_leftovers_after_uninstall: bool = True
    include_system_leftovers: bool = True
    cache_keep: int = 2
    build: BuildOptions = field(default_factory=BuildOptions)

    @classmethod
    def load(cls) -> "Settings":
        raw = load_json(config_dir() / "settings.json", {})
        s = cls()
        names = {f.name for f in fields(cls)}
        for k, v in raw.items():
            if k == "build" and isinstance(v, dict):
                valid = {f.name for f in fields(BuildOptions)}
                s.build = BuildOptions(**{bk: bv for bk, bv in v.items() if bk in valid})
            elif k in names:
                setattr(s, k, v)
        return s

    def save(self) -> None:
        save_json(config_dir() / "settings.json", asdict(self))
