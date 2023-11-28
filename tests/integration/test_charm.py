#!/usr/bin/env python3
# Copyright 2023 Marcus Boden
# See LICENSE file for licensing details.

import base64
import json
import logging
import subprocess
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

user_config = """username1:
    gecos: Name and other attributes
    keys:
    - ssh-ed25519 XYZ username@machine
    - ssh-rsa ABC username@machine
username2:
    gecos: Other Name
    keys:
    - sk-ssh-ed25519@openssh.com DEF other@machine"""


@pytest.mark.abort_on_fail
async def test_01_build_and_deploy(ops_test: OpsTest):
    """Build the charm- and deploy it.

    Assert on the unit status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(".")
    resources = {"nginx-image": METADATA["resources"]["nginx-image"]["upstream-source"]}

    # Deploy the charm and wait for active/idle status
    app = await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME)
    await ops_test.model.block_until(lambda: app.status == "blocked")

    await app.set_config({"users": ""})
    address = await get_address(ops_test=ops_test)
    ssl_config = generate_certificate(ops_test.tmp_path, address)

    await app.set_config(ssl_config)

    await ops_test.model.wait_for_idle()


async def test_02_users_config(ops_test: OpsTest):
    app = ops_test.model.applications[APP_NAME]

    await app.set_config({"users": user_config})

    await ops_test.model.wait_for_idle()

    address = await get_address(ops_test=ops_test)

    resp = requests.get(f"https://{address}", verify=ops_test.tmp_path / "cert.pem")
    users = json.loads(resp.text)
    assert users == yaml.safe_load(user_config)


async def get_address(ops_test: OpsTest, app_name=APP_NAME, unit_num=0) -> str:
    """Get the address for a unit."""
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][app_name]["units"][f"{app_name}/{unit_num}"]["address"]
    return address


def generate_certificate(cert_dir, ip):
    cmd = [
        "openssl",
        "req",
        "-x509",
        "-addext",
        f"subjectAltName=IP:{ip},DNS:localhost",
        "-newkey",
        "rsa:4096",
        "-keyout",
        cert_dir / "key.pem",
        "-out",
        cert_dir / "cert.pem",
        "-sha256",
        "-days",
        "365",
        "-nodes",
        "-subj",
        "/O=Canonical/OU=ManagesSolutions/CN=keyserver",
    ]
    subprocess.run(cmd)

    ssl_conf = {}
    with open(cert_dir / "key.pem", "rb") as f:
        ssl_conf["ssl_key"] = base64.b64encode(f.read()).decode("utf-8")
    with open(cert_dir / "cert.pem", "rb") as f:
        ssl_conf["ssl_cert"] = base64.b64encode(f.read()).decode("utf-8")

    return ssl_conf
