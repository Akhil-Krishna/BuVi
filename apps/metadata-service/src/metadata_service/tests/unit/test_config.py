"""Production safety: a laptop configuration must not boot in staging or prod."""

from __future__ import annotations

import pytest

from metadata_service.core.config import Settings

pytestmark = pytest.mark.unit


def test_dev_defaults_are_refused_in_prod() -> None:
    with pytest.raises(RuntimeError) as info:
        Settings(environment="prod").assert_production_safe()
    message = str(info.value)
    for problem in (
        "vault_token",
        "service_client_secret",
        "require_gateway_token",
        "connector_allowed_internal_hosts allows loopback",
    ):
        assert problem in message
    assert "devroot" not in message
    assert "dev-metadata-secret" not in message


def test_a_hardened_configuration_boots() -> None:
    Settings(
        environment="prod",
        vault_token="s.real-token",  # type: ignore[arg-type]
        service_client_secret="a-real-secret",  # type: ignore[arg-type]
        require_gateway_token=True,
        connector_allowed_internal_hosts=[],
    ).assert_production_safe()


def test_dev_is_permissive() -> None:
    Settings(environment="dev").assert_production_safe()
