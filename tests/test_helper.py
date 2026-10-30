"""Tests for the root helper (argument and path validation)."""

import os
import subprocess
import unittest
from unittest import mock


class HelperTests(unittest.TestCase):
    def test_helper_validation(self):
        from lmp import helper
        not_owned = subprocess.CompletedProcess([], 1, "", "error: No package owns")
        with mock.patch.object(helper.subprocess, "run", return_value=not_owned):
            self.assertEqual(helper._validate_delete("/var/lib/foo"), "/var/lib/foo")
            for bad in ("/etc/pacman.d/hooks/x", "/home/u/x", "/var/lib", "relative/x",
                        "/usr/bin/foo", "/var/lib/../../etc/shadow"):
                with self.assertRaises(ValueError, msg=bad):
                    helper._validate_delete(bad)
        owned = subprocess.CompletedProcess([], 0, "/opt/foo/ is owned by foo 1-1", "")
        with mock.patch.object(helper.subprocess, "run", return_value=owned):
            with self.assertRaises(ValueError):
                helper._validate_delete("/opt/foo")
        with self.assertRaises(ValueError):
            helper.check_packages(["firefox", "-rf"])
        with self.assertRaises(ValueError):
            helper.check_packages(["a;rm"])

    def test_helper_refuses_non_root(self):
        from lmp import helper
        if os.geteuid() != 0:
            self.assertEqual(helper.main(["remove-orphans"]), 1)
