#!/usr/bin/env bash
# Runs straight from the project folder without installing (development mode).
# Requires: sudo pacman -S --needed python-pyqt6 polkit rsync
cd "$(dirname "$(readlink -f "$0")")"
exec python3 -m lmp "$@"
