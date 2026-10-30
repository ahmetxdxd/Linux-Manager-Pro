"""Tests for lmp.core.profile."""

import unittest

from lmp.core import profile
from helpers import HOME


class ProfileTests(unittest.TestCase):
    def test_roundtrip(self):
        cfg = HOME / ".config/myapp"
        (cfg / "Cache").mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text('{"a": 1}')
        (cfg / "Cache/big.bin").write_text("x" * 1000)
        content = profile.ProfileContent(repo=["firefox", "vim"], aur=["yay"],
                                         flatpak=[{"id": "com.spotify.Client", "origin": "flathub"}])
        out = profile.export_profile(HOME / "yedek", content, [cfg])
        self.assertTrue(out.name.endswith(".lmp.tar.gz"))
        read = profile.read_profile(out)
        self.assertEqual(read.repo, ["firefox", "vim"])
        self.assertEqual(read.configs, ["~/.config/myapp"])
        (cfg / "settings.json").write_text('{"a": 2}')
        backup = profile.restore_configs(out, read.configs)
        self.assertEqual((cfg / "settings.json").read_text(), '{"a": 1}')
        self.assertFalse((cfg / "Cache/big.bin").exists(), "caches must not end up in the profile")
        self.assertEqual((backup / ".config/myapp/settings.json").read_text(), '{"a": 2}')

    def test_restore_rejects_paths_outside_home(self):
        for bad in ("~/../etc", "~", "~/.config", "/etc/passwd", "~/.local/share/linux-manager-pro"):
            with self.assertRaises(ValueError, msg=bad):
                profile._config_rel(bad)
        self.assertEqual(profile._config_rel("~/.config/x"), ".config/x")

    def test_restore_rolls_back_on_failure(self):
        cfg = HOME / ".config/rollback-app"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "a.conf").write_text("new")
        out = profile.export_profile(HOME / "rb", profile.ProfileContent(), [cfg])
        (cfg / "a.conf").write_text("original")
        broken = HOME / "broken.lmp.tar.gz"
        broken.write_bytes(out.read_bytes()[:40])          # truncated archive: extract fails
        with self.assertRaises(Exception):
            profile.restore_configs(broken, ["~/.config/rollback-app"])
        self.assertEqual((cfg / "a.conf").read_text(), "original",
                         "the original config must be put back when restoring fails")

    def test_text_list(self):
        content = profile.ProfileContent(repo=["a"], aur=["b"], flatpak=[{"id": "c.d.E"}])
        path = profile.export_text_list(HOME / "liste.txt", content)
        parsed = profile.read_text_list(path)
        self.assertEqual((parsed.repo, parsed.aur, parsed.flatpak[0]["id"]), (["a"], ["b"], "c.d.E"))

    def test_aur_cmd(self):
        cmd = profile.aur_install_cmd("yay", ["foo"])
        self.assertEqual(cmd[:6], ["yay", "-S", "--needed", "--noconfirm", "--sudo", "pkexec"])
