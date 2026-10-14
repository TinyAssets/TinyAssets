"""Broker-only KMS credential file.

The daemon never imports this module. The three names are withheld from the
daemon environment. The broker reads the mode-0400 file the host installs for
its own uid. There is no application-held master key and no offline copy.
"""

from __future__ import annotations

import os

from tinyassets.broker.root_key import (
    AwsKmsRootKey,
    KeyServiceUnavailable,
    MemoryRootKey,
    RootKeyError,
    RootKeyProvider,
)

KMS_KEY_ARN = "TINYASSETS_VAULT_KMS_KEY_ARN"
AWS_ACCESS_KEY_ID = "TINYASSETS_VAULT_AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY = "TINYASSETS_VAULT_AWS_SECRET_ACCESS_KEY"
VAULT_CREDENTIAL_NAMES = frozenset({KMS_KEY_ARN, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY})
BROKER_CREDENTIAL_FILE = os.environ.get(
    "TINYASSETS_VAULT_KMS_FILE",
    "/etc/tinyassets/vault-kms.env",
)


def read_broker_credential_file(path: str | None = None) -> dict[str, str]:
    """Read the broker-only credential file. Never the process environment."""
    target = path or BROKER_CREDENTIAL_FILE
    try:
        text = open(target, encoding="utf-8").read()
    except OSError as exc:
        raise KeyServiceUnavailable("vault KMS key is not configured") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    missing = VAULT_CREDENTIAL_NAMES - values.keys()
    if missing or any(not values[name] for name in VAULT_CREDENTIAL_NAMES):
        raise KeyServiceUnavailable("vault KMS key is not configured")
    return values


def production_provider(path: str | None = None) -> AwsKmsRootKey:
    if os.environ.get("TINYASSETS_VAULT_TEST_DOUBLE"):
        raise RootKeyError("in-memory vault provider is refused in production")
    values = read_broker_credential_file(path)
    arn = values[KMS_KEY_ARN]
    region = "us-west-2"
    parts = arn.split(":")
    if len(parts) > 3 and parts[3]:
        region = parts[3]
    return AwsKmsRootKey(
        key_arn=arn,
        access_key_id=values[AWS_ACCESS_KEY_ID],
        secret_access_key=values[AWS_SECRET_ACCESS_KEY],
        region=region,
    )


def provider_for_process() -> RootKeyProvider:
    """Fail closed unless production has the KMS file or tests installed the double."""
    production = os.environ.get("TINYASSETS_ENV") == "production"
    if production:
        return production_provider()
    if os.environ.get("TINYASSETS_VAULT_TEST_DOUBLE") == "1":
        return MemoryRootKey()
    file_path = BROKER_CREDENTIAL_FILE
    if os.path.isfile(file_path):
        return production_provider(file_path)
    raise KeyServiceUnavailable("vault KMS key is not configured")
