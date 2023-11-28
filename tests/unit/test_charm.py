# Copyright 2023 Marcus Boden
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest

import ops
import ops.testing
from charm import UserKeyServerCharm


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = ops.testing.Harness(UserKeyServerCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_can_connect("nginx", True)
        self.harness.container_pebble_ready("nginx")

    def test_ssl_config(self):
        self.harness.update_config({"ssl_cert": "foo", "ssl_key": "bar"})
        self.assertEqual(
            self.harness.model.unit.status, ops.BlockedStatus("could not decode ssl_key")
        )
        self.harness.update_config({"ssl_cert": "foo", "ssl_key": "aGVsbG8K"})
        self.assertEqual(
            self.harness.model.unit.status, ops.BlockedStatus("could not decode ssl_cert")
        )
        self.harness.update_config({"ssl_cert": "aGVsbG8K", "ssl_key": "aGVsbG8K"})
        self.assertEqual(self.harness.model.unit.status, ops.ActiveStatus())
        self.harness.update_config({"ssl_cert": "", "ssl_key": ""})
        self.assertEqual(
            self.harness.model.unit.status,
            ops.BlockedStatus("charm needs ssl_key configured to work"),
        )

    def test_user_config(self):
        user_config = """username1:
    gecos: Name and other attributes
    keys:
    - ssh-ed25519 XYZ username@machine
    - ssh-rsa ABC username@machine
username2:
    gecos: Other Name
    keys:
    - sk-ssh-ed25519@openssh.com DEF other@machine"""
        # check if a correct config works
        self.harness.update_config(
            {"users": user_config, "ssl_cert": "aGVsbG8K", "ssl_key": "aGVsbG8K"}
        )
        self.assertEqual(self.harness.model.unit.status, ops.ActiveStatus())

        # how about empty user config?
        self.harness.update_config({"users": " "})
        self.assertEqual(self.harness.model.unit.status, ops.ActiveStatus())

        # try some bad yaml
        self.harness.update_config({"users": "badly\nformatted: \n yaml"})
        self.assertEqual(
            self.harness.model.unit.status, ops.BlockedStatus("failed to yaml_load users config")
        )
        # and some correct yaml, but not in the right format
        self.harness.update_config({"users": "testuser: moep"})
        self.assertEqual(
            self.harness.model.unit.status,
            ops.BlockedStatus("malformed users config. gecos for testuser malformed or absent"),
        )
