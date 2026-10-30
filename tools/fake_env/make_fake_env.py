#!/usr/bin/env python3
"""Fake pacman/flatpak/systemctl environment for trying the UI on a non-Arch machine.

Usage:
    python3 tools/fake_env/make_fake_env.py /tmp/lmp-fake
    source /tmp/lmp-fake/env.sh && python3 -m lmp
"""

import hashlib
import os
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/lmp-fake")
BIN = ROOT / "bin"
DATA = ROOT / "share"
HOME = ROOT / "home"

PACKAGES = (HERE / "packages.txt").read_text().split()
FLATPAKS = (HERE / "flatpaks.txt").read_text().split()
AUR = {"brave-bin", "visual-studio-code-bin", "spotify", "discord", "upscayl-bin", "yay", "paru"}
DEPS = ["glibc", "gcc-libs", "zlib", "openssl", "qt6-base", "gtk3", "mesa", "ffmpeg", "python-pyqt6",
        "libpipewire", "kio", "kcoreaddons", "nss", "systemd-libs", "libx11"]
GUI = {
    "firefox": ("Firefox", "firefox", "Web Browser"),
    "brave-bin": ("Brave", "brave-browser", "Web browser"),
    "code": ("Code - OSS", "com.visualstudio.code.oss", "Code Editing. Redefined."),
    "spotify": ("Spotify", "spotify", "Music streaming"),
    "discord": ("Discord", "discord", "Voice and text chat"),
    "obs-studio": ("OBS Studio", "com.obsproject.Studio", "Free and Open Source Streaming/Recording"),
    "gimp": ("GNU Image Manipulation Program", "gimp", "Create images and edit photographs"),
    "kdenlive": ("Kdenlive", "kdenlive", "Video Editor"),
    "steam": ("Steam", "steam", "Application for managing and playing games on Steam"),
    "kate": ("Kate", "kate", "Advanced Text Editor"),
    "dolphin": ("Dolphin", "system-file-manager", "File Manager"),
    "konsole": ("Konsole", "utilities-terminal", "Terminal"),
    "kitty": ("kitty", "kitty", "Fast, feature-rich, GPU based terminal"),
    "alacritty": ("Alacritty", "Alacritty", "A fast, cross-platform, OpenGL terminal emulator"),
    "libreoffice-fresh": ("LibreOffice", "libreoffice-startcenter", "Office suite"),
    "vlc": ("VLC media player", "vlc", "Read, capture, broadcast your multimedia streams"),
    "obsidian": ("Obsidian", "obsidian", "Knowledge base"),
    "gparted": ("GParted", "gparted", "Create, reorganize, and delete partitions"),
    "virt-manager": ("Virtual Machine Manager", "virt-manager", "Manage virtual machines"),
    "krita": ("Krita", "krita", "Digital painting"),
    "inkscape": ("Inkscape", "org.inkscape.Inkscape", "Vector graphics editor"),
    "audacity": ("Audacity", "audacity", "Audio editor"),
    "filelight": ("Filelight", "filelight", "Disk usage"),
    "spectacle": ("Spectacle", "spectacle", "Screenshot capture utility"),
    "gwenview": ("Gwenview", "gwenview", "Image viewer"),
    "kcalc": ("KCalc", "accessories-calculator", "Scientific calculator"),
    "ark": ("Ark", "ark", "Archiving tool"),
    "partitionmanager": ("KDE Partition Manager", "partitionmanager", "Partition editor"),
    "kdeconnect": ("KDE Connect", "kdeconnect", "Connect to your phone"),
    "easyeffects": ("Easy Effects", "com.github.wwmm.easyeffects", "Audio effects"),
    "localsend": ("LocalSend", "localsend", "Share files to nearby devices"),
    "upscayl-bin": ("Upscayl", "upscayl", "AI image upscaler"),
    "thunderbird": ("Thunderbird", "thunderbird", "Mail client"),
    "btop": ("btop++", "btop", "Resource monitor"),
    "htop": ("Htop", "htop", "Process viewer"),
    "qemu-full": ("QEMU", "qemu", "Machine emulator"),
    "gamescope": ("Gamescope", "gamescope", "SteamOS session compositor"),
    "discover": ("Discover", "plasmadiscover", "Software center"),
    "pavucontrol": ("Volume Control", "multimedia-volume-control", "PulseAudio volume control"),
}
BIG = {"steam": 12, "libreoffice-fresh": 420, "firefox": 260, "code": 380, "obs-studio": 120,
       "ollama": 2900, "linux-firmware": 640, "qemu-full": 160, "gimp": 140, "kdenlive": 70,
       "brave-bin": 410, "blender": 880, "krita": 210, "obsidian": 260, "wine": 940,
       "upscayl-bin": 610}


def h(name: str, mod: int) -> int:
    return int(hashlib.md5(name.encode()).hexdigest(), 16) % mod


def qi_block(name: str, version: str, explicit: bool) -> str:
    size = BIG.get(name, 0.2 + h(name, 9000) / 100)
    unit = "MiB"
    date = datetime(2026, 9, 28) - timedelta(days=h(name, 400), minutes=h(name + "m", 900))
    gui = GUI.get(name)
    desc = gui[2] if gui else f"{name} package"
    req = "None" if explicit else " ".join(sorted({PACKAGES[h(name, len(PACKAGES))].split('=')[0]}))
    return (f"Name            : {name}\nVersion         : {version}\nDescription     : {desc}\n"
            f"Architecture    : x86_64\nURL             : https://example.org/{name}\n"
            f"Licenses        : GPL\nGroups          : None\nProvides        : None\n"
            f"Depends On      : glibc  zlib\nOptional Deps   : None\nRequired By     : {req}\n"
            f"Optional For    : None\nConflicts With  : None\nReplaces        : None\n"
            f"Installed Size  : {size:.2f} {unit}\nPackager        : Arch Linux <arch@example.org>\n"
            f"Build Date      : {date:%a %b %d %H:%M:%S %Y}\n"
            f"Install Date    : {date:%a %b %d %H:%M:%S %Y}\n"
            f"Install Reason  : {'Explicitly installed' if explicit else 'Installed as a dependency for another package'}\n"
            f"Install Script  : No\nValidated By    : Signature\n")


def write_exec(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def main() -> None:
    for d in (BIN, DATA / "applications", HOME, ROOT / "out"):
        d.mkdir(parents=True, exist_ok=True)
    pkgs = [p.split("=", 1) for p in PACKAGES]
    blocks = [qi_block(n, v, True) for n, v in pkgs]
    blocks += [qi_block(d, "1.0-1", False) for d in DEPS]
    (ROOT / "out/qi.txt").write_text("\n".join(blocks) + "\n")
    (ROOT / "out/q.txt").write_text("".join(f"{n} {v}\n" for n, v in pkgs) +
                                    "".join(f"{d} 1.0-1\n" for d in DEPS))
    (ROOT / "out/qqm.txt").write_text("".join(f"{n}\n" for n, _ in pkgs if n in AUR))
    (ROOT / "out/qqen.txt").write_text("".join(f"{n}\n" for n, _ in pkgs if n not in AUR))
    (ROOT / "out/slq.txt").write_text("".join(f"{n}\n" for n, _ in pkgs if n not in AUR) +
                                      "".join(f"{d}\n" for d in DEPS))
    owners = []
    for name, (title, icon, comment) in GUI.items():
        did = f"{icon if '.' in icon else name}.desktop"
        exe = name.replace("-bin", "").replace("-fresh", "")
        (DATA / "applications" / did).write_text(
            f"[Desktop Entry]\nType=Application\nName={title}\nComment={comment}\n"
            f"Exec={exe} %U\nIcon={icon}\nCategories=Utility;\n")
        owners.append(f"{DATA}/applications/{did} is owned by {name} 1.0-1")
    (ROOT / "out/qo.txt").write_text("\n".join(owners) + "\n")
    flat_lines = []
    for item in FLATPAKS:
        app_id, ver = item.split("=", 1)
        name = app_id.split(".")[-1]
        size = 80 + h(app_id, 900)
        inst = "user" if h(app_id, 4) == 0 else "system"
        flat_lines.append(f"{app_id}\t{name}\t{ver}\tstable\t{inst}\t{size}.0 MB\tflathub")
    (ROOT / "out/flatpak.txt").write_text("\n".join(flat_lines) + "\n")

    write_exec(BIN / "pacman", f"""#!/bin/bash
O={ROOT}/out
case "$1" in
  -Qi) if [ $# -gt 1 ]; then python3 - "$@" <<'EOF'
import sys,re
text=open("{ROOT}/out/qi.txt").read()
want=set(sys.argv[2:])
for b in re.split(r"\\n(?=Name)",text):
    m=re.match(r"Name\\s+:\\s+(\\S+)",b)
    if m and m.group(1) in want: print(b.strip()+"\\n")
EOF
       else cat $O/qi.txt; fi ;;
  -Q) cat $O/q.txt ;;
  -Qqm) cat $O/qqm.txt ;;
  -Qqen) cat $O/qqen.txt ;;
  -Qqem) cat $O/qqm.txt ;;
  -Qdtq) printf 'python-old-dep\\nlib32-unused\\nqt5-webkit\\n' ;;
  -Slq) cat $O/slq.txt ;;
  -Qo) cat $O/qo.txt ;;
  -Si) shift; for n in "$@"; do grep -q "^$n " $O/q.txt && printf 'Repository      : extra\\nName            : %s\\nVersion         : %s\\n\\n' "$n" "$(grep "^$n " $O/q.txt | cut -d' ' -f2)"; done ;;
  -Qlq) echo "/opt/$2/"; echo "/opt/$2/bin/$2"; echo "/usr/bin/$2"; echo "/usr/share/$2/" ;;
  *) echo "fake pacman: $*" >&2; exit 1 ;;
esac
""")
    write_exec(BIN / "flatpak", f"""#!/bin/bash
case "$1" in list) cat {ROOT}/out/flatpak.txt ;; *) echo "fake flatpak $*"; sleep 1 ;; esac
""")
    units = [("NetworkManager.service", "loaded active running", "Network Manager", "enabled"),
             ("bluetooth.service", "loaded active running", "Bluetooth service", "enabled"),
             ("sddm.service", "loaded active running", "Simple Desktop Display Manager", "enabled"),
             ("cups.service", "loaded inactive dead", "CUPS Scheduler", "disabled"),
             ("ollama.service", "loaded active running", "Ollama Service", "enabled"),
             ("docker.service", "loaded inactive dead", "Docker Application Container Engine", "disabled"),
             ("tlp.service", "loaded active exited", "TLP system startup/shutdown", "enabled"),
             ("ufw.service", "loaded active exited", "CLI Netfilter Manager", "enabled"),
             ("example-vpn.service", "loaded failed failed", "Example VPN client daemon", "enabled"),
             ("power-profiles-daemon.service", "loaded active running", "Power Profiles daemon", "enabled"),
             ("cronie.service", "loaded inactive dead", "Command Scheduler", "disabled"),
             ("sshd.service", "loaded inactive dead", "OpenSSH Daemon", "disabled"),
             ("systemd-journald.service", "loaded active running", "Journal Service", "static"),
             ("libvirtd.service", "loaded inactive dead", "libvirt legacy monolithic daemon", "disabled"),
             ("avahi-daemon.service", "loaded active running", "Avahi mDNS/DNS-SD Stack", "enabled"),
             ("fwupd.service", "loaded inactive dead", "Firmware update daemon", "static"),
             ("upower.service", "loaded active running", "Daemon for power management", "enabled"),
             ("plocate-updatedb.service", "loaded inactive dead", "Update the plocate database", "static")]
    (ROOT / "out/units.txt").write_text("".join(f"{u} {s} {d}\n" for u, s, d, _ in units))
    (ROOT / "out/unitfiles.txt").write_text("".join(f"{u} {f} disabled\n" for u, _, _, f in units)
                                            + "getty@.service enabled enabled\n")
    write_exec(BIN / "systemctl", f"""#!/bin/bash
[ "$1" = "--user" ] && shift && U=1
case "$1" in
  list-units) [ -n "$U" ] && printf 'pipewire.service loaded active running PipeWire Multimedia Service\\nsyncthing.service loaded inactive dead Syncthing\\n' || cat {ROOT}/out/units.txt ;;
  list-unit-files) [ -n "$U" ] && printf 'pipewire.service enabled enabled\\nsyncthing.service disabled disabled\\n' || cat {ROOT}/out/unitfiles.txt ;;
  cat) echo "# /usr/lib/systemd/system/$2"; printf '[Unit]\\nDescription=Fake\\n\\n[Service]\\nExecStart=/usr/bin/fake\\n' ;;
  is-active) exit 3 ;;
  *) echo "fake systemctl $*"; sleep 1 ;;
esac
""")
    write_exec(BIN / "systemd-analyze", """#!/bin/bash
if [ "$1" = blame ]; then printf '  6.412s example-vpn.service\\n  3.208s avahi-daemon.service\\n  2.511s NetworkManager-wait-online.service\\n  1.902s ollama.service\\n  1.104s libvirtd.service\\n   842ms tlp.service\\n   604ms upower.service\\n   512ms sddm.service\\n   356ms bluetooth.service\\n   201ms ufw.service\\n'
else echo 'Startup finished in 4.120s (firmware) + 2.031s (loader) + 1.904s (kernel) + 9.880s (userspace) = 17.935s'; fi
""")
    write_exec(BIN / "lsblk", """#!/bin/bash
cat <<'EOF'
{"blockdevices":[{"name":"nvme0n1","path":"/dev/nvme0n1","model":"Example NVMe SSD 1TB","rm":false,"hotplug":false,"ro":false,"type":"disk","mountpoints":[null],"children":[
 {"name":"nvme0n1p2","path":"/dev/nvme0n1p2","fstype":"btrfs","label":"Arch","uuid":"a1","rm":false,"hotplug":false,"ro":false,"type":"part","mountpoints":["/"]}]},
 {"name":"sda","path":"/dev/sda","model":"Portable USB Disk","rm":true,"hotplug":true,"ro":false,"type":"disk","mountpoints":[null],"children":[
 {"name":"sda1","path":"/dev/sda1","fstype":"ext4","label":"Backup","uuid":"b2","rm":true,"hotplug":true,"ro":false,"type":"part","mountpoints":["/tmp/lmp-fake/mnt/Backup"]}]}]}
EOF
""")
    write_exec(BIN / "journalctl", "#!/bin/bash\necho '2026-10-28T10:00:01+0300 archbox systemd[1]: Started Fake.'\n")
    write_exec(BIN / "vercmp", "#!/bin/bash\n[ \"$1\" = \"$2\" ] && echo 0 || ( [ \"$1\" \\< \"$2\" ] && echo -1 || echo 1 )\n")
    (ROOT / "mnt/Backup").mkdir(parents=True, exist_ok=True)

    # fake home directory
    for rel in [".config/obs-studio", ".config/Code - OSS/User", ".config/kitty", ".config/BraveSoftware",
                ".config/gimp-old", ".config/spotify", ".config/discord", ".local/share/Steam",
                ".config/autostart", ".mozilla/firefox", ".ollama/models", ".config/fish",
                ".local/share/TelegramDesktop", ".config/old-game", ".cache/yarn", ".minecraft/saves"]:
        (HOME / rel).mkdir(parents=True, exist_ok=True)
    (HOME / ".ollama/models/llama.bin").write_bytes(os.urandom(1024) * 60)
    (HOME / ".config/spotify/prefs").write_text("x" * 20000)
    (HOME / ".config/discord/settings.json").write_text("{}" * 9000)
    (HOME / ".config/autostart/discord.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Discord\nExec=discord --start-minimized\nIcon=discord\n")
    (HOME / ".config/autostart/kdeconnect.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=KDE Connect\nComment=Connect to your phone\nExec=kdeconnectd\nIcon=kdeconnect\nHidden=true\n")
    sysauto = ROOT / "xdg/autostart"
    sysauto.mkdir(parents=True, exist_ok=True)
    for did, name, exe, extra in [
            ("org.kde.kdeconnect.daemon.desktop", "KDE Connect", "kdeconnectd", ""),
            ("easyeffects-service.desktop", "Easy Effects", "easyeffects --gapplication-service", ""),
            ("geoclue-demo-agent.desktop", "Geoclue Demo agent", "geoclue-agent", "OnlyShowIn=GNOME;\n"),
            ("xembedsniproxy.desktop", "Xembed SNI Proxy", "xembedsniproxy", ""),
            ("org.kde.plasma-browser-integration-host.desktop", "Plasma Browser Integration", "plasma-browser-integration-host", ""),
            ("arch-hello.desktop", "Welcome Screen", "arch-hello", ""),
            ("pamac-tray.desktop", "Pamac Tray", "pamac-tray", "")]:
        (sysauto / did).write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exe}\nIcon={exe.split()[0]}\n{extra}")
    (ROOT / "env.sh").write_text(
        f'export PATH="{BIN}:$PATH"\nexport HOME="{HOME}"\nexport XDG_DATA_DIRS="{DATA}"\n'
        f'export XDG_CONFIG_DIRS="{ROOT}/xdg"\nexport XDG_CURRENT_DESKTOP=KDE\n')
    print(f"Ready: source {ROOT}/env.sh")


if __name__ == "__main__":
    main()
