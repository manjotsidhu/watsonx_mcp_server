"""Per-connection session state — see spec/01-auth-and-environments.md "Session model".

Hard constraint: session state (auth target + token) must live in a
per-connection session object, never in module-level globals or a config
file, so the exact same code works unmodified under a future remote/
multi-tenant transport, even though v1 only ships local stdio.

The MCP SDK already hands every tool call a `Context` whose `.session` is the
per-connection `ServerSession` object (a new instance per client connection,
even under stdio). We piggyback our state directly onto that object instead of
keeping any module-level dict — there is exactly one `SessionState` per
`ServerSession`, and it disappears when the connection does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from mcp.server.fastmcp import Context

_STATE_ATTR = "_watsonx_platform_session_state"


@dataclass
class SessionState:
    environment: Optional[str] = None  # "saas" | "cpd"
    base_url: Optional[str] = None
    auth_method: Optional[str] = None  # "iam" | "zen_api_key" | "cpd_bearer"

    # Current credential material for platform calls. Never exposed in tool output.
    token: Optional[str] = None  # bearer token, for "iam" and "cpd_bearer"
    zen_header: Optional[str] = None  # full "ZenApiKey <b64>" header value
    expires_at: Optional[float] = None  # epoch seconds; None = never expires (zen_api_key)

    # Retained *only* to support transparent re-auth on 401 (see 01-auth-and-environments.md).
    # Never written to disk, never included in tool output.
    apikey: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    stage: str = "prod"
    region_or_url: Optional[str] = None
    prefer_zen_api_key: bool = True

    # Local stdio convenience bootstrap (WX_* env vars) is attempted at most once.
    bootstrap_attempted: bool = False

    # TLS verification for calls to `base_url` (the CPD cluster or SaaS
    # dataplatform host) — NOT the IAM token endpoint, which is always a
    # public IBM-operated host with a valid certificate and is always
    # verified regardless of these settings. Not part of the spec (which
    # covers API request/response shapes, not client TLS config); added
    # because CPD clusters commonly run with a self-signed or internal-CA
    # certificate that a default trust store won't validate.
    verify_ssl: bool = True
    ca_bundle_path: Optional[str] = None  # path to a CA bundle/cert to trust instead of disabling verification

    def verify_param(self) -> Union[bool, str]:
        """The value to pass as httpx's `verify=` for calls to `base_url`.

        A CA bundle path takes precedence over the plain verify_ssl flag —
        prefer supplying the cluster's CA certificate over disabling
        verification outright.
        """
        if self.ca_bundle_path:
            return self.ca_bundle_path
        return self.verify_ssl

    def public_view(self) -> dict:
        """Redacted confirmation shape for the `authenticate` tool's output."""
        return {
            "environment": self.environment,
            "base_url": self.base_url,
            "auth_method": self.auth_method,
            "expires_at": self.expires_at,
            "tls_verify": self.verify_param(),
        }


def get_state(ctx: Context) -> SessionState:
    """Fetch (or lazily create) this connection's session state.

    Attached as a plain attribute on the SDK-provided per-connection
    `ServerSession` object — deliberately *not* a module-level dict keyed by
    id(session), so there is no server-wide registry to leak across
    connections or to clean up.
    """
    session = ctx.session
    state = getattr(session, _STATE_ATTR, None)
    if state is None:
        state = SessionState()
        setattr(session, _STATE_ATTR, state)
    return state


def set_state(ctx: Context, state: SessionState) -> None:
    """Explicitly attach a SessionState to this connection — mainly useful for
    tests that want to pre-seed an already-authenticated session."""
    setattr(ctx.session, _STATE_ATTR, state)
