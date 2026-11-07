# Auth and Environments

## Environment types

Every session targets exactly one **environment**, which is one of:

- **`saas`** — IBM Cloud watsonx SaaS. Base host is IBM-set, not
  customer-specific, and keyed off where the product instance lives.
  Callers select a **region code**; this server maps it to the host via a
  fixed table (confirmed from the CCS API docs — note it is *not* a fully
  uniform `{region}.dataplatform` pattern, two regions use a different
  second-level domain):

  | region code | base host |
  |---|---|
  | `us-south` (Dallas, default) | `api.dataplatform.cloud.ibm.com` |
  | `eu-de` (Frankfurt) | `api.eu-de.dataplatform.cloud.ibm.com` |
  | `eu-gb` (London) | `api.eu-gb.dataplatform.cloud.ibm.com` |
  | `jp-tok` (Tokyo) | `api.jp-tok.dataplatform.cloud.ibm.com` |
  | `au-syd` (Sydney) | `api.au-syd.dai.cloud.ibm.com` |
  | `ca-tor` (Toronto) | `api.ca-tor.dai.cloud.ibm.com` |

  This table is the single source of truth for region→host resolution; do
  not re-derive it from a guessed pattern.
- **`cpd`** — Cloud Pak for Data / watsonx Software. Base URL is an arbitrary
  **customer-specific route** with no fixed pattern this server can derive —
  e.g. `https://cpd-cpd-instance.apps.<cluster>.<domain>/` on
  OpenShift/ARO, or anything else the cluster admin configured. There is
  nothing to "resolve" here: the caller must always supply the full base URL
  directly (unlike `saas`, where a short region code is enough).

Both cases resolve to a `base_url`; **relative API paths are identical
across `saas` and `cpd`** (`/v2/projects`, `/v2/assets`, etc.) — only the
host and the auth mechanism differ. This is what keeps every tool's `wraps`
endpoint in `02-tools.md` a single path usable against either environment.

This mirrors `cpdctl`'s profile model (a profile = credentials + a target),
but nothing here is persisted to a profile file by default — see
[Session model](#session-model) below.

## Auth flow: SaaS (IAM)

1. Caller supplies an IBM Cloud **apikey**.
2. Exchange it for a bearer token:
   `POST https://iam.cloud.ibm.com/identity/token`
   (`grant_type=urn:ibm:params:oauth:grant-type:apikey`, `apikey=<apikey>`,
   form-urlencoded body, `Accept: application/json`).
3. Use `Authorization: Bearer <token>` on all subsequent CCS calls.
4. **Verified live** response shape:
   ```json
   {"access_token": "<jwt>", "refresh_token": "not_supported",
    "token_type": "Bearer", "expires_in": 3600, "expiration": 1788624990,
    "scope": "ibm openid"}
   ```
   `expires_in: 3600` confirms the ~1 hour lifetime assumption.
   `refresh_token: "not_supported"` — there is no refresh-token flow for
   the apikey grant; re-exchange the apikey again on expiry, same pattern
   as CPD's `/v1/authorize`. The apikey itself is retained in memory for
   the session (never on disk) purely to support this re-exchange.

## Auth flow: CPD (on-prem / Software)

Two supported mechanisms — **prefer ZenApiKey**, fall back to token exchange:

### Preferred: ZenApiKey header (no token exchange, no refresh)

CPD supports a self-contained header scheme that needs no server round-trip
to establish and never expires:

```
Authorization: ZenApiKey base64("<username>:<platform_api_key>")
```

i.e. base64-encode the literal string `username:apikey`, prefix with
`ZenApiKey `. Computed once, reused for the life of the session (and could be
cached/persisted safely by the caller across processes if ever needed, since
it isn't a session token). This is the default CPD path in this spec because
it removes an entire class of "token expired mid-session" failure handling
that the bearer-token flow requires.

Requires: `username` + a **platform API key** (generated in the CPD user's
Profile and settings → API key, or via `POST /usermgmt/v1/user/apiKey`
against an already-authenticated session — same platform API key
concept `cpdctl` profiles use). **Verified against a live cluster**:
`Authorization: ZenApiKey <base64("username:platform_api_key")>` against
`GET /v2/projects` returns `200` with normal resource data, confirming this
path needs no prior call at all.

### Fallback: bearer token via `/v1/authorize`

For cases where only a password (not a platform API key) is available, or a
cluster disables ZenApiKey:

1. `POST {cpd_url}/icp4d-api/v1/authorize` with `{"username": ..., "api_key": ...}`
   (platform API key) or `{"username": ..., "password": ...}` (login
   password) — **verified against a live cluster**: the field must be named
   `api_key`, not `apikey`; sending a platform API key under `password`
   returns `401 {"exception": "Sorry, we could not verify your credentials.", "message": "Unauthorized"}`.
2. Response on success: `{"_messageCode_": 200, "message": "Success", "token": "<jwt>"}`.
   Use `Authorization: Bearer <token>` on subsequent calls.
3. **Verified token lifetime: 12 hours** (`exp - iat` = 43200s in the decoded
   JWT), much longer than a typical IAM token (~1 hour) — worth a longer
   proactive-refresh margin than IAM's. Must be refreshed by re-calling
   `/v1/authorize` (no separate refresh-token endpoint observed;
   re-authenticate with the same credentials).

The `authenticate` tool (below) picks ZenApiKey automatically whenever an
apikey (not just a password) is provided for a `cpd` environment, and only
uses the `/v1/authorize` bearer flow when a password is supplied instead, or
ZenApiKey is explicitly disabled via a tool parameter.

## Session model

**Design constraint: no process-global auth state.** Credentials and the
resolved environment/target live in a session context keyed by the MCP
connection, not in a config file or module-level global. This is what lets
the exact same auth code run unmodified under a future remote/multi-tenant
HTTP transport (each connection gets its own isolated session) without a
rewrite — even though v1 only ships the local stdio transport.

- `authenticate(environment, region_or_url, apikey, username=None, password=None)`
  resolves the base URL, performs the appropriate auth flow above, and
  stores `{environment, base_url, auth_header_or_token, expiry, apikey (for
  refresh only)}` in the connection's session context. Returns a redacted
  confirmation (environment, base_url, expiry if applicable) — never echoes
  the credential back.
- All other tools read the *current* session context implicitly; there is no
  per-call credential parameter in v1 (keeps tool signatures focused on the
  actual CCS operation). Calling `authenticate` again replaces the session's
  target — v1 supports one active target per session, not multiple
  concurrent targets.
- **Local convenience (stdio only):** at process startup, if
  `WX_ENVIRONMENT` / `WX_APIKEY` / `WX_USERNAME` / `WX_URL_OR_REGION`
  env vars are set, the server calls `authenticate` with them automatically
  so a single local user doesn't have to call it by hand every session. This
  is purely a bootstrap convenience — it populates the same session context
  through the same code path, it does not bypass it.
- On a 401 from any CCS call, the server attempts exactly one transparent
  re-auth (IAM token refresh, or re-run `/v1/authorize`; not applicable to
  ZenApiKey, which doesn't expire) and retries the original call once before
  surfacing an error.

## Security notes

- apikeys/passwords/tokens are never written to disk, never included in tool
  output, and never included in error messages or logs surfaced to the
  calling model.
- The `authenticate` tool's input schema should mark `apikey`/`password` as
  sensitive if the MCP SDK supports it, so clients can avoid echoing them in
  transcripts/UI.
