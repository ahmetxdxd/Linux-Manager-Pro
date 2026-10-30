"""Tests for lmp.core.flatpak."""

import unittest

from lmp.core import flatpak


class FlatpakTests(unittest.TestCase):
    def test_parse_list(self):
        text = ("com.spotify.Client\tSpotify\t1.2.3\tstable\tsystem\t345.1 MB\tflathub\n"
                "org.gimp.GIMP\tGIMP\t2.10\tstable\tuser\t1.2 GB\tflathub\n")
        apps = flatpak.parse_list(text)
        self.assertEqual(apps[0].install_flag, "--system")
        self.assertEqual(apps[1].install_flag, "--user")
        self.assertEqual(apps[1].size, int(1.2 * 1000**3))
        cmd = flatpak.uninstall_cmd(apps[1])
        self.assertIn("--user", cmd)
        self.assertNotIn("--delete-data", cmd)
