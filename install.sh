#!/usr/bin/env bash
# Linux Manager Pro installer: builds a pacman package from the project folder and installs it.
# Run the same script again to update.
# To uninstall:  sudo pacman -R linux-manager-pro
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v pacman >/dev/null; then
  echo "This script is for Arch Linux and its derivatives (pacman not found)." >&2
  exit 1
fi
if [[ $EUID -eq 0 ]]; then
  echo "Run this script as a normal user; you will be asked for a password when needed." >&2
  exit 1
fi
if ! command -v makepkg >/dev/null; then
  echo "makepkg not found, installing base-devel…"
  sudo pacman -S --needed base-devel
fi

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
cp packaging/PKGBUILD "$build/"
cd "$build"
# the PKGBUILD takes its files from LMP_SRC (this folder)
LMP_SRC="$OLDPWD" BUILDDIR="$build/work" PKGDEST="$build/out" makepkg -sif --noconfirm

echo
echo "Installed. Open 'Linux Manager Pro' from the application menu, or run 'linux-manager-pro' in a terminal."
