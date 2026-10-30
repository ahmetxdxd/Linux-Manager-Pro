# Linux Manager Pro: Plan

An all-in-one system manager for Linux, in the spirit of HiBit Uninstaller on Windows.
Target platform for now: **Arch Linux and pacman-based derivatives** (CachyOS, EndeavourOS, Manjaro, …).

## Modules

| Page | What it does |
|---|---|
| **Overview** | System summary, disk usage, package counts, orphaned packages, pacman cache; one-click cleanup |
| **Uninstaller** | Lists pacman, AUR and Flatpak apps and removes them in bulk. A visible *Find leftovers after removal* tick searches for config, cache, data, icons, autostart entries, user services, `.pacsave` files and broken service links, graded by confidence. *Scan leftovers* shows everything tied to an app (package files, services, dependencies, user data) without removing it. Every deletion is reviewed and confirmed first; user items go to the Trash. Also scans for **orphaned** folders left by apps removed earlier. |
| **Startup** | Toggles XDG autostart entries (user + system), adds new ones, *Disable all* in one click with an *Undo* that restores exactly what it switched off; shows services that slow down boot via `systemd-analyze blame` |
| **Services** | systemd system and user services: start/stop/restart, enable/disable, mask, view journal and unit file |
| **Backup & Transfer** | Saves the app list (official / AUR / Flatpak) and selected config folders into one `.tar.gz` profile. On another Arch install it opens the profile, shows what is missing, installs the selection and restores configs (backing up existing ones first). Also exports/imports a plain-text list. |
| **External Disk** | (1) Creates a real Flatpak installation on an external disk and installs or moves apps there. (2) Moves large per-package directories of pacman packages to an external disk; a pacman hook safely stops the update while the disk is unplugged. (3) Moves user data folders (e.g. `~/.ollama`). All reversible. |
| **Builder** | Gentoo-style local builds: an official-repo package, an AUR package or a git repository is built with `-march=native` + O2/O3 + LTO + ccache and installed. Built packages are recorded and listed for rebuild when the repo ships a new version. |
| **Settings** | Language (English / Türkçe), theme, deletion behaviour (Trash / permanent), AUR helper, build defaults |

## Architecture

```
lmp/
  core/        # UI-independent logic, unit-testable
    util.py        command execution, sizes, formatting
    pacman.py      pacman -Qi parser, installed packages, orphans, cache
    flatpak.py     flatpak apps, installations
    apps.py        unified app list + .desktop matching
    leftovers.py   leftover scanner (with confidence levels)
    autostart.py   XDG autostart
    systemd.py     systemctl / journalctl
    profile.py     profile export/import
    relocate.py    external-disk relocation logic
    disks.py       disk detection via lsblk
    builder.py     build profiles, PKGBUILD generation
    privileged.py  calling the helper through pkexec
  helper.py    # The single root entry point, allow-listed commands only
  i18n.py      # Tiny gettext-style translation layer (English source, Turkish catalog)
  locales/     # tr.py: Turkish catalog
  ui/          # PyQt6 interface
    theme.py, widgets.py, runner.py, models.py, main_window.py
    pages/ dashboard, uninstaller, startup, services, profile, external, builder, settings
  data/        # apps.json (known app → config paths), logo
```

**Privilege model:** The UI always runs as the normal user. Every operation that needs root goes
through `pkexec /usr/lib/linux-manager-pro/lmp-helper <command>`. The helper only accepts defined
commands and validates every path against an allow-list. The polkit action uses
`auth_admin_keep`, so the password is asked once for a few minutes.

**Long operations:** All commands run in the background via `QProcess`. The task dialog shows a
live log and progress and can be cancelled; it stays open when the job finishes.

**Internationalisation:** English strings in the source code are the message ids. `tr()` looks
them up in `lmp/locales/<lang>.py`; a missing entry falls back to English. The helper receives
`--lang` so root-side messages and pacman hooks use the same language. A unit test fails if a
string is missing from the Turkish catalog.

## Repository layout

```
lmp/      application
tests/    pytest, one file per core module; conftest.py gives every run a throw-away HOME
tools/    fake_env/ (fake pacman, flatpak, systemctl… for non-Arch machines) and screenshots.py
packaging/ docs/
```

## Roadmap

- **v1.0 (this release):** All 8 pages above, Arch + Flatpak + AUR (yay/paru), English + Turkish; leftover scan on demand and after removal; one-click disable of all startup programs.
- **v1.1:** Install-time tracking in the Uninstaller (diff the file system before/after an install for perfect cleanup later); scheduled automatic profile backups (systemd timer).
- **v1.2:** Bulk "rebuild the whole system natively" queue in the Builder, `x86-64-v3/v4` detection, build-time estimates.
- **v2.0:** Debian/Fedora providers (apt/dnf) and a cross-distro package-name mapping table. The profile format is ready for it and is versioned with `schema_version`.
