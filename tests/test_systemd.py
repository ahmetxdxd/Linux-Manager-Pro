"""Tests for lmp.core.systemd."""

import unittest

from lmp.core import systemd


class SystemdTests(unittest.TestCase):
    def test_parse(self):
        units = systemd.parse_list_units(
            "sshd.service loaded active running OpenSSH Daemon\n"
            "● foo.service loaded failed failed Foo thing\n")
        self.assertEqual(units["foo.service"].active, "failed")
        self.assertEqual(units["sshd.service"].description, "OpenSSH Daemon")
        files = systemd.parse_unit_files("sshd.service enabled disabled\nx@.service static -\n")
        self.assertEqual(files["sshd.service"], ("enabled", "disabled"))

    def test_action_validation(self):
        with self.assertRaises(ValueError):
            systemd.action_cmd("foo; rm -rf /", "start")
        self.assertEqual(systemd.action_cmd(systemd.Unit("a.service", user=True), "stop"),
                         ["systemctl", "--user", "stop", "a.service"])
