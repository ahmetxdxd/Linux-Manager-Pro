"""Known-application definitions (lmp/data/apps.json): app → config/data paths."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "apps.json"


@dataclass
class AppDefinition:
    ident: str
    name: str
    packages: list[str] = field(default_factory=list)
    config: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    cache: list[str] = field(default_factory=list)
    sensitive: bool = False

    @property
    def all_paths(self) -> list[str]:
        return self.config + self.data + self.cache


@lru_cache(maxsize=1)
def definitions() -> tuple[AppDefinition, ...]:
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    out = []
    for item in raw.get("apps", []):
        out.append(AppDefinition(
            ident=item["id"], name=item.get("name", item["id"]),
            packages=item.get("packages", []), config=item.get("config", []),
            data=item.get("data", []), cache=item.get("cache", []),
            sensitive=bool(item.get("sensitive", False)),
        ))
    return tuple(out)


def for_package(name: str) -> AppDefinition | None:
    for d in definitions():
        if name in d.packages:
            return d
    return None
