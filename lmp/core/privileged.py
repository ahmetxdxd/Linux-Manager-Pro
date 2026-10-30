"""Builds the command that invokes the root helper (lmp-helper) through pkexec."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from lmp import i18n

INSTALLED_HELPER = Path("/usr/lib/linux-manager-pro/lmp-helper")
SOURCE_HELPER = Path(__file__).resolve().parent.parent / "helper.py"
PROGRESS_PREFIX = "@@LMP "

# pkexec exit codes
PKEXEC_DISMISSED = 126   # user dismissed the password dialog
PKEXEC_NOT_AUTHORIZED = 127


def helper_base() -> list[str]:
    """Start of the command that runs the helper (without pkexec)."""
    if INSTALLED_HELPER.exists():
        return [str(INSTALLED_HELPER)]
    return [sys.executable or "/usr/bin/python3", str(SOURCE_HELPER)]


def helper_cmd(*args: str) -> list[str]:
    base = helper_base() + ["--lang", i18n.current()]
    if os.geteuid() == 0:
        return base + list(args)
    return ["pkexec"] + base + list(args)


def hook_exec_line(*args: str) -> str:
    """Exec line for the pacman hook file (paths with spaces are quoted)."""
    parts = helper_base() + ["--lang", i18n.current()] + list(args)
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def is_installed() -> bool:
    return INSTALLED_HELPER.exists()
