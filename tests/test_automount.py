"""Tests for lmp.core.automount."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lmp.core import automount


class AutomountTests(unittest.TestCase):
    def setUp(self):
        self.fstab = Path(tempfile.mkdtemp()) / "fstab"
        self.fstab.write_text("# static\nUUID=root / ext4 rw 0 1\n"
                              "UUID=data /mnt/data ext4 defaults 0 2\n")
        self.patch = mock.patch.object(automount, "FSTAB", self.fstab)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_status(self):
        self.assertEqual(automount.status("/mnt/data"), automount.FSTAB_ENTRY)
        self.assertEqual(automount.status("/run/media/u/disk"), automount.NONE)
        self.assertEqual(automount.status("/somewhere", "data"), automount.FSTAB_ENTRY)

    def test_line_and_remove(self):
        path = "/run/media/u/My Disk"
        line = automount.fstab_lines("1234-ABCD", path, "ext4")
        self.assertIn("/run/media/u/My\\040Disk", line)
        self.assertIn("nofail", line)
        self.fstab.write_text(self.fstab.read_text() + line)
        self.assertEqual(automount.status(path), automount.OURS)
        with mock.patch("subprocess.run"):
            automount.remove(path)
        text = self.fstab.read_text()
        self.assertNotIn(automount.MARK, text)
        self.assertIn("UUID=data /mnt/data", text, "other entries must stay untouched")
        self.assertEqual(automount.status(path), automount.NONE)
