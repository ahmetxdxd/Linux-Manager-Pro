"""Tests for lmp.core.pacman."""

import unittest
from unittest import mock

from lmp.core import pacman, util
from helpers import QI_SAMPLE


class PacmanTests(unittest.TestCase):
    def test_parse_info(self):
        pkgs = pacman.parse_info(QI_SAMPLE)
        self.assertEqual([p.name for p in pkgs], ["obs-studio", "jansson"])
        obs, jansson = pkgs
        self.assertTrue(obs.explicit)
        self.assertFalse(jansson.explicit)
        self.assertEqual(obs.size, int(21.40 * 1024 * 1024))
        self.assertEqual(obs.install_date.year, 2024)
        self.assertEqual(obs.install_date.day, 1)
        self.assertEqual(jansson.install_date.month, 1)   # the en_US format must parse too
        self.assertEqual(jansson.required_by, ["obs-studio", "nftables"])
        self.assertEqual(obs.depends, ["ffmpeg", "jansson", "libxinerama"])

    def test_parse_size(self):
        self.assertEqual(util.parse_size("1.50 KiB"), 1536)
        self.assertEqual(util.parse_size("2,0 MiB"), 2 * 1024 * 1024)
        self.assertEqual(util.parse_size("940 bytes"), 940)
        self.assertEqual(util.parse_size("garbage"), 0)

    def test_format_size(self):
        self.assertEqual(util.format_size(512), "512 B")
        self.assertEqual(util.format_size(1536), "1.5 KB")
        self.assertEqual(util.format_size(None), "—")

    def test_owners_parse(self):
        out = ("/opt/foo/ is owned by foo 1.0-1\n"
               "/usr/share/x/ is owned by a 1-1\n/usr/share/x/ is owned by b 2-1\n")
        with mock.patch.object(pacman, "run", return_value=util.CmdResult(0, out, "")):
            owners = pacman.owners(["/opt/foo", "/usr/share/x"])
        self.assertEqual(owners["/opt/foo"], {"foo"})
        self.assertEqual(owners["/usr/share/x"], {"a", "b"})
