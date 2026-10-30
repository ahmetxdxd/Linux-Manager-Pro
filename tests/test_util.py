"""Tests for lmp.core.util (Trash handling)."""

import unittest
from unittest import mock

from lmp.core import util
from helpers import HOME


class TrashTests(unittest.TestCase):
    def test_manual_trash(self):
        victim = HOME / ".config/to-delete"
        victim.mkdir(parents=True, exist_ok=True)
        (victim / "a.txt").write_text("x")
        with mock.patch.object(util, "have", return_value=False):
            util.move_to_trash(victim)
        self.assertFalse(victim.exists())
        trash = HOME / ".local/share/Trash"
        self.assertTrue((trash / "files/to-delete/a.txt").exists())
        info = (trash / "info/to-delete.trashinfo").read_text()
        self.assertIn("Path=", info)
