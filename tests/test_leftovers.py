"""Tests for lmp.core.leftovers."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lmp.core import desktop, leftovers
from helpers import HOME, make_app


class LeftoverTests(unittest.TestCase):
    def setUp(self):
        for rel in (".config/obs-studio", ".config/obs-studio-extra", ".cache/obs-studio",
                    ".config/gnome-shell-x", ".config/Code", ".config/code-oss-thing",
                    ".config/kate", ".local/share/kate", ".var/app/com.spotify.Client",
                    ".config/autostart", ".config/systemd/user", ".mystery-app",
                    ".config/totally-unknown"):
            (HOME / rel).mkdir(parents=True, exist_ok=True)
        (HOME / ".config/katerc").write_text("x")
        (HOME / ".config/obs-studio/basic.ini").write_text("x" * 100)
        (HOME / ".config/autostart/obs.desktop").write_text(
            "[Desktop Entry]\nName=OBS\nExec=obs --startreplaybuffer\n")
        (HOME / ".config/systemd/user/obs-helper.service").write_text("[Unit]\n")
        (HOME / ".bashrc").write_text("x")

    def _entry(self, desktop_id, exec_line, wm=""):
        p = HOME / desktop_id
        p.write_text(f"[Desktop Entry]\nName=App\nExec={exec_line}\nStartupWMClass={wm}\n")
        return desktop.parse(p)

    def test_obs_exact_and_probable(self):
        obs = make_app("obs-studio", "OBS Studio",
                       desktop_entries=[self._entry("com.obsproject.Studio.desktop", "obs")])
        kate = make_app("kate", "Kate")
        found = leftovers.scan(obs, [obs, kate], include_system=False)
        by_path = {str(f.path.relative_to(HOME)): f for f in found}
        self.assertEqual(by_path[".config/obs-studio"].confidence, leftovers.EXACT)
        self.assertEqual(by_path[".cache/obs-studio"].confidence, leftovers.EXACT)
        self.assertEqual(by_path[".config/obs-studio-extra"].confidence, leftovers.PROBABLE)
        self.assertIn(".config/autostart/obs.desktop", by_path)
        self.assertGreater(by_path[".config/obs-studio"].size, 0)
        # unrelated folders that the old faulty algorithm caught must no longer be listed
        self.assertNotIn(".config/gnome-shell-x", by_path)
        self.assertNotIn(".config/kate", by_path)
        self.assertNotIn(".bashrc", by_path)

    def test_shared_with_other_installed_app(self):
        kate = make_app("kate", "Kate")
        kwrite = make_app("kwrite", "KWrite")
        found = leftovers.scan(kate, [kate, kwrite], include_system=False)
        paths = {str(f.path.relative_to(HOME)) for f in found}
        self.assertIn(".config/katerc", paths)
        self.assertIn(".local/share/kate", paths)
        # 'kate' is being removed, but if another 'kate' owner is still listed it is not reported
        other = make_app("kate-git", "Kate dev")
        found2 = leftovers.scan(kate, [kate, other], include_system=False)
        self.assertNotIn(".config/kate", {str(f.path.relative_to(HOME)) for f in found2})

    def test_interpreter_exec_not_used_as_key(self):
        app = make_app("my-java-tool", "My Tool",
                       desktop_entries=[self._entry("my-java-tool.desktop", "java -jar x.jar")])
        (HOME / ".java").mkdir(exist_ok=True)
        found = leftovers.scan(app, [app], include_system=False)
        self.assertNotIn(".java", {f.path.name for f in found})

    def test_icons_and_local_binaries(self):
        icons = HOME / ".local/share/icons/hicolor/48x48/apps"
        icons.mkdir(parents=True, exist_ok=True)
        (icons / "obs-studio.png").write_text("x")
        (icons / "unrelated.png").write_text("x")
        (HOME / ".local/bin").mkdir(parents=True, exist_ok=True)
        (HOME / ".local/bin/obs").write_text("#!/bin/sh\n")
        obs = make_app("obs-studio", "OBS Studio",
                       desktop_entries=[self._entry("com.obsproject.Studio.desktop", "obs")])
        found = leftovers.scan(obs, [obs], include_system=False)
        paths = {str(f.path.relative_to(HOME)) for f in found}
        self.assertIn(".local/share/icons/hicolor/48x48/apps/obs-studio.png", paths)
        self.assertNotIn(".local/share/icons/hicolor/48x48/apps/unrelated.png", paths)
        self.assertIn(".local/bin/obs", paths)

    def test_footprint_groups_package_files(self):
        root = Path(tempfile.mkdtemp())
        listing = [str(root / "usr/lib/obs/a.so"), str(root / "usr/lib/obs/b.so"),
                   str(root / "usr/bin/obs"), "/usr/lib/systemd/user/obs.service"]
        for f in listing[:3]:
            Path(f).parent.mkdir(parents=True, exist_ok=True)
            Path(f).write_text("x" * 10)
        app = make_app("obs-studio")
        fake_rn = subprocess.CompletedProcess([], 0, "obs-studio\njansson\n", "")
        with mock.patch.object(leftovers.pacman, "package_files", return_value=listing), \
                mock.patch.object(leftovers, "run", return_value=mock.Mock(ok=True, out=fake_rn.stdout)):
            fp = leftovers.footprint(app)
        self.assertEqual(fp.files, 4)
        self.assertEqual(fp.size, 30)
        self.assertEqual(fp.services, ["obs.service"])
        self.assertEqual(fp.dependencies, ["jansson"])
        self.assertEqual(leftovers._group_package_file("/usr/share/obs/data/x.png"), "/usr/share/obs")
        self.assertEqual(leftovers._group_package_file("/usr/bin/obs"), "/usr/bin/obs")

    def test_flatpak(self):
        sp = make_app("com.spotify.Client", "Spotify", source="flatpak")
        found = leftovers.scan(sp, [sp], include_system=False)
        self.assertEqual([f.path.name for f in found], ["com.spotify.Client"])
        self.assertTrue(found[0].default_checked)

    def test_orphans(self):
        kate = make_app("kate", "Kate")
        with mock.patch.object(leftovers.pacman, "available", return_value=False):
            found = leftovers.scan_orphans([kate])
        names = {f.path.name for f in found}
        self.assertIn("totally-unknown", names)
        self.assertIn(".mystery-app", names)
        self.assertNotIn("katerc", names)
        self.assertNotIn("autostart", names)
        self.assertTrue(all(not f.default_checked for f in found))
