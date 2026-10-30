"""Tests for lmp.core.desktop."""

import unittest

from lmp.core import desktop
from helpers import HOME


class DesktopTests(unittest.TestCase):
    def test_exec_binary(self):
        self.assertEqual(desktop.exec_binary("/usr/bin/code --unity-launch %F"), "code")
        self.assertEqual(desktop.exec_binary("env GDK_BACKEND=x11 obs"), "obs")
        self.assertEqual(desktop.exec_binary(
            "/usr/bin/flatpak run --branch=stable --arch=x86_64 com.spotify.Client %U"),
            "com.spotify.Client")

    def test_parse_and_set_key(self):
        f = HOME / "t.desktop"
        f.write_text("[Desktop Entry]\nName=Foo\nName[tr]=Fu\nExec=foo %U\n# comment\n"
                     "[Desktop Action new]\nName=Yeni\nExec=foo --new\n")
        e = desktop.parse(f, lang="tr")
        self.assertEqual(e.name, "Fu")
        self.assertEqual(e.exec, "foo %U")
        text = desktop.set_key(f.read_text(), "Hidden", "true")
        self.assertIn("Hidden=true\n[Desktop Action new]", text)
        self.assertIn("# comment", text)
        text = desktop.set_key(text, "Hidden", None)
        self.assertNotIn("Hidden", text)
