# Developer tools

`fake_env/make_fake_env.py` builds a throw-away "machine" (fake `pacman`, `flatpak`,
`systemctl`, `lsblk`, `journalctl`, a home folder with typical app data) so the app can be run
and tested on any Linux, not only Arch.

```bash
python3 tools/fake_env/make_fake_env.py /tmp/lmp-fake
source /tmp/lmp-fake/env.sh
python3 -m lmp                                   # runs against the fake machine
QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py docs/screenshots en dark
```
