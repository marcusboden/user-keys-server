#!/usr/bin/env python3
# Copyright 2023 Marcus Boden
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following tutorial that will help you
develop a new k8s charm using the Operator Framework:

https://juju.is/docs/sdk/create-a-minimal-kubernetes-charm
"""

import base64
import json
import logging

import ops
import yaml

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class UserKeyServerCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on["nginx"].pebble_ready, self._on_nginx_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.nginx_config_path = "/etc/nginx/nginx-users.conf"
        self.user_path = "/var/www/users.json"

    def _set_certificate(self):
        """Take the ssl_cert and ssl_key config option and push them to the nginx container."""
        self.unit.status = ops.MaintenanceStatus("Setting SSL Certificate")
        container = self.unit.get_container("nginx")

        if not container.can_connect():
            return

        for ssl_opt in ["ssl_key", "ssl_cert"]:
            if self.config[ssl_opt]:
                try:
                    base64.b64decode(self.config[ssl_opt])
                except base64.binascii.Error:  # pyright: ignore reportGeneralTypeIssues
                    logger.error(f"could not decode {ssl_opt}")
                    self.unit.status = ops.BlockedStatus(f"could not decode {ssl_opt}")
                    return
            else:
                self.unit.status = ops.BlockedStatus(f"charm needs {ssl_opt} configured to work")
                return

        logging.info("Pushing cert")
        container.push(
            "/etc/ssl/private/cert.pem",
            base64.b64decode(self.config["ssl_cert"]).decode("utf-8"),
            make_dirs=True,
        )
        logging.info("Pushing key")
        container.push(
            "/etc/ssl/private/key.pem",
            base64.b64decode(self.config["ssl_key"]).decode("utf-8"),
            make_dirs=True,
        )

        # The first time this happens, we need to set the right config
        services = container.get_plan().to_dict().get("services", {})
        if not services:
            logger.warning("Pebble layer not initialized yet")
            return

        # if we still use the original command, replace with configured one
        if services["nginx"]["command"] == self._pebble_layer["services"]["nginx"]["command"]:
            conf_layer = {
                "services": {
                    "nginx": {
                        "command": f'nginx -c {self.nginx_config_path} -g "daemon off; master_process on;"',
                        "override": "merge",
                    }
                }
            }
            container.add_layer("nginx", conf_layer, combine=True)
            container.replan()
            logging.info("added layer with the right config to nginx")
        else:
            # just restart to get new certificates
            container.restart("nginx")

        self.unit.status = ops.ActiveStatus()

    def _on_nginx_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Define and start a workload using the Pebble API."""
        container = event.workload

        container = self.unit.get_container("nginx")
        # put nginx config into the right place
        with open(self.charm_dir / "src/files/nginx-users.conf", "r") as f:
            container.push(self.nginx_config_path, f, make_dirs=True)

        # start without the config, as certificates may not be there yet.
        container.add_layer("nginx", self._pebble_layer, combine=True)
        container.replan()

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle changed configuration."""
        self.unit.status = ops.MaintenanceStatus("Running config-changed hook")

        # defer if the container is not reachable yet, i.e. config-changed is running before pebble_ready
        container = self.unit.get_container("nginx")
        if not container.can_connect():
            # wait until we can connect
            event.defer()
            return

        # set up certificates. This happens every time the config changes, but
        # the other option would be to set a stored state or compare to
        # existing file. It's not like overwriting the cert hurts...
        self._set_certificate()

        # handle new users
        users = self._validate_users()
        logger.info(users)
        if users:
            current_users = {}
            if container.exists(self.user_path):
                current_users = json.load(container.pull(self.user_path))

            if current_users != users:
                container.push(self.user_path, json.dumps(users), make_dirs=True)

            self.unit.status = ops.ActiveStatus()

    @property
    def _pebble_layer(self) -> ops.pebble.LayerDict:
        """Return a dictionary representing a Pebble layer."""
        return {
            "summary": "nginx layer",
            "description": "pebble config layer for nginx",
            "services": {
                "nginx": {
                    "override": "replace",
                    "summary": "nginx",
                    "command": 'nginx -g "daemon off; master_process on;"',
                    "startup": "enabled",
                }
            },
        }

    def _validate_users(self):
        """Take the config for the users and validates it."""
        logger.info(f"parsing users:\n{self.config['users']}")
        if not self.config["users"]:
            logger.warning("'users' config is not configured")
            return {}

        try:
            users = yaml.safe_load(self.config["users"])
        except (
            yaml.YAMLError,
            yaml.scanner.ScannerError,  # pyright: ignore reportGeneralTypeIssues
        ) as error:
            logger.warning(error)
            self.unit.status = ops.BlockedStatus("failed to yaml_load users config")
            return

        if not isinstance(users, dict):
            logger.warning("not a string")
            return

        for u in users.keys():
            if "gecos" not in users[u] or not isinstance(users[u]["gecos"], str):
                logger.info(f"no gecos for {u}")
                self.unit.status = ops.BlockedStatus(
                    f"malformed users config. gecos for {u} malformed or absent"
                )
                return False
            if "keys" not in users[u]:
                self.unit.status = ops.BlockedStatus(f"no keys specified for {u}")
                return False
            for k in users[u]["keys"]:
                if not isinstance(k, str):
                    self.unit.status = ops.BlockedStatus(f"key {k} for {u} is not a string")
                    return False

        return users


if __name__ == "__main__":  # pragma: nocover
    ops.main(UserKeyServerCharm)  # type: ignore
