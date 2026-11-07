"""`authenticate` — see spec/01-auth-and-environments.md and spec/02-tools.md #1."""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..auth import authenticate_session
from ..session import get_state
from . import redacted

_SENSITIVE = {"json_schema_extra": {"writeOnly": True}}


@mcp.tool(
    annotations=ToolAnnotations(title="Authenticate", readOnlyHint=False, idempotentHint=True),
)
@redacted
async def authenticate(
    ctx: Context,
    environment: Literal["saas", "cpd"],
    region_or_url: Annotated[
        str,
        Field(
            description=(
                "SaaS: a region code (us-south [default], eu-de, eu-gb, jp-tok, au-syd, ca-tor). "
                "CPD: the full cluster base URL (e.g. https://cpd-cpd-instance.apps.my-cluster.example.com), "
                "always supplied in full — there is no derivable pattern for CPD."
            )
        ),
    ],
    apikey: Annotated[
        Optional[str],
        Field(default=None, description="IBM Cloud apikey (saas) or platform API key (cpd, ZenApiKey/bearer). Never echoed back.", **_SENSITIVE),
    ] = None,
    username: Annotated[
        Optional[str], Field(default=None, description="Required for environment='cpd'.")
    ] = None,
    password: Annotated[
        Optional[str],
        Field(default=None, description="CPD fallback only, used instead of apikey for /v1/authorize. Never echoed back.", **_SENSITIVE),
    ] = None,
    prefer_zen_api_key: Annotated[
        bool,
        Field(default=True, description="CPD only: prefer the no-round-trip ZenApiKey header over the /v1/authorize bearer flow when an apikey is supplied."),
    ] = True,
    stage: Annotated[
        Literal["prod", "dev", "test"],
        Field(
            default="prod",
            description=(
                "SaaS only — deployment stage. Each stage pairs a different IAM authority with its "
                "dataplatform host (dev's dataplatform uses test's IAM; test's uses prod's IAM) — see "
                "spec/01-auth-and-environments.md. This parameter extends the tool beyond 02-tools.md's "
                "literal signature, which predates the discovery of this third resolution axis."
            ),
        ),
    ] = "prod",
    verify_ssl: Annotated[
        bool,
        Field(
            default=True,
            description=(
                "Verify TLS certificates when calling `region_or_url` (the CPD cluster itself, "
                "including its own /v1/authorize) — NOT the SaaS IAM token endpoint, which is a "
                "public IBM-operated host and is always verified regardless of this setting. Set to "
                "false only for a trusted CPD cluster with a self-signed or internal-CA certificate "
                "you cannot otherwise validate. Disabling this removes protection against "
                "man-in-the-middle attacks on that connection — prefer `ca_bundle_path` when you have "
                "the cluster's CA certificate instead."
            ),
        ),
    ] = True,
    ca_bundle_path: Annotated[
        Optional[str],
        Field(
            default=None,
            description=(
                "Path to a CA bundle/certificate file to trust for calls to `region_or_url`, instead of "
                "disabling verification outright. Takes precedence over `verify_ssl` when set."
            ),
        ),
    ] = None,
) -> dict:
    """Establish (or replace) this session's target environment and credentials.

    All other tools operate against the *current* authenticated session — there is
    no per-call credential parameter. Calling this again replaces the session's
    target; v1 supports one active target per session, not multiple concurrent ones.
    """
    state = get_state(ctx)
    await authenticate_session(
        state,
        environment=environment,
        region_or_url=region_or_url,
        apikey=apikey,
        username=username,
        password=password,
        prefer_zen_api_key=prefer_zen_api_key,
        stage=stage,
        verify_ssl=verify_ssl,
        ca_bundle_path=ca_bundle_path,
    )
    if not verify_ssl and not ca_bundle_path:
        await ctx.warning(
            f"TLS certificate verification is DISABLED for {state.base_url} — "
            "traffic to this host is not protected against man-in-the-middle attacks. "
            "Only use this for a trusted cluster; prefer `ca_bundle_path` when possible."
        )
    return state.public_view()
