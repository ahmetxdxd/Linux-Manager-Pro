"""Tests for lmp.core.disks."""

import unittest

from lmp.core import disks


class DiskTests(unittest.TestCase):
    def test_parse_lsblk(self):
        text = ('{"blockdevices":[{"name":"sda","model":"Samsung SSD","children":['
                '{"name":"sda1","fstype":"ext4","mountpoints":["/"]}]}]}')
        devs = disks.parse_lsblk(text)
        self.assertEqual([d["name"] for d in devs], ["sda", "sda1"])
