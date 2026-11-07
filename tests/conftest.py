from __future__ import annotations

from types import SimpleNamespace

import pytest

from watsonx_platform_mcp.session import SessionState, set_state


def make_ctx(state: SessionState) -> SimpleNamespace:
    """A minimal stand-in for FastMCP's Context, exposing only the `.session`
    attribute that get_state()/set_state() actually use."""
    fake_session = SimpleNamespace()
    ctx = SimpleNamespace(session=fake_session)
    set_state(ctx, state)
    return ctx


@pytest.fixture
def ctx_factory():
    """Returns `make_ctx` so tests can do `ctx_factory(some_state)`."""
    return make_ctx


@pytest.fixture
def cpd_zen_state() -> SessionState:
    """A pre-authenticated CPD session using ZenApiKey (no expiry, no re-auth possible)."""
    return SessionState(
        environment="cpd",
        base_url="https://cpd.example.com",
        auth_method="zen_api_key",
        zen_header="ZenApiKey dXNlcjprZXk=",
        username="user",
        apikey="key",
        prefer_zen_api_key=True,
    )


@pytest.fixture
def saas_iam_state() -> SessionState:
    """A pre-authenticated SaaS session using an IAM bearer token."""
    return SessionState(
        environment="saas",
        base_url="https://api.dataplatform.cloud.ibm.com",
        auth_method="iam",
        token="tok-1",
        apikey="apikey-1",
        stage="prod",
        region_or_url="us-south",
    )
