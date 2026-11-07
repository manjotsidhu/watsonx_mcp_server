"""WX_* env-var local stdio bootstrap — see spec/01-auth-and-environments.md
"Session model" > "Local convenience (stdio only)".

At process startup, if the WX_* env vars are set, the server calls
`authenticate` with them automatically so a single local user doesn't have to
call it by hand every session. This is purely a bootstrap convenience — it
populates the same per-connection SessionState through the same
authenticate_session() code path, it does not bypass it.

Implementation note: the MCP stdio transport has no explicit "session
started" hook we can attach real startup logic to, and state is deliberately
per-connection rather than global (see session.py). So this runs lazily on
the first tool call that needs an authenticated session, guarded by
`bootstrap_attempted` so it's tried at most once per connection.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import Context

from .auth import authenticate_session
from .errors import PlatformError
from .session import SessionState, get_state

# WX_STAGE / WX_PASSWORD / WX_VERIFY_SSL / WX_CA_BUNDLE_PATH are extensions
# beyond the four vars named explicitly in the spec
# (WX_ENVIRONMENT/APIKEY/USERNAME/URL_OR_REGION), added for parity with
# `authenticate`'s full parameter set (stage axis, password fallback, TLS
# verification for self-signed CPD clusters). All optional.
_ENV_ENVIRONMENT = "WX_ENVIRONMENT"
_ENV_APIKEY = "WX_APIKEY"
_ENV_USERNAME = "WX_USERNAME"
_ENV_URL_OR_REGION = "WX_URL_OR_REGION"
_ENV_PASSWORD = "WX_PASSWORD"
_ENV_STAGE = "WX_STAGE"
_ENV_PREFER_ZEN = "WX_PREFER_ZEN_API_KEY"
_ENV_VERIFY_SSL = "WX_VERIFY_SSL"
_ENV_CA_BUNDLE_PATH = "WX_CA_BUNDLE_PATH"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no")


async def ensure_authenticated(ctx: Context) -> SessionState:
    """Return the current session's state, bootstrapping from WX_* env vars
    on first use if no `authenticate` call has happened yet. Raises PlatformError
    if there is still no active session afterwards.
    """
    state = get_state(ctx)

    if state.environment is None and not state.bootstrap_attempted:
        state.bootstrap_attempted = True
        environment = os.environ.get(_ENV_ENVIRONMENT)
        if environment:
            await authenticate_session(
                state,
                environment=environment,
                region_or_url=os.environ.get(_ENV_URL_OR_REGION),
                apikey=os.environ.get(_ENV_APIKEY),
                username=os.environ.get(_ENV_USERNAME),
                password=os.environ.get(_ENV_PASSWORD),
                prefer_zen_api_key=_env_bool(_ENV_PREFER_ZEN, True),
                stage=os.environ.get(_ENV_STAGE, "prod"),
                verify_ssl=_env_bool(_ENV_VERIFY_SSL, True),
                ca_bundle_path=os.environ.get(_ENV_CA_BUNDLE_PATH),
            )

    if state.environment is None:
        raise PlatformError(
            "not_authenticated",
            "No active session. Call the `authenticate` tool first, or set the "
            "WX_ENVIRONMENT/WX_APIKEY/WX_USERNAME/WX_URL_OR_REGION env vars "
            "for local stdio auto-bootstrap.",
            False,
        )
    return state
