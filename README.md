# watsonx Platform MCP Server

An MCP server exposing IBM watsonx's **Common Core Services (CCS)** — Projects,
Assets, Connections, Jobs — so that other products, agents, and AI assistants
can discover, build in, and deploy to the watsonx platform (SaaS and CPD
on-prem) through a single, product-agnostic interface.

## Status

v1 implemented, Python + the official MCP SDK, stdio transport. See
[`spec/`](spec/) for the full source-of-truth contract this implementation
follows — read it before changing tool behavior.

## Install & run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run directly:

```bash
watsonx-platform-mcp
```

or `python -m watsonx_platform_mcp.server`. It speaks MCP over stdio — point your
MCP client (Claude Code, Claude Desktop, etc.) at that command.

## Auth: call `authenticate`, or bootstrap from env vars

Every other tool operates against the *current* session; there's no per-call
credential parameter. Call `authenticate` first, or set env vars for local
stdio use — the server auto-authenticates on the first tool call if these are
present:

| Env var | Required | Notes |
|---|---|---|
| `WX_ENVIRONMENT` | yes | `saas` or `cpd` |
| `WX_APIKEY` | usually | IBM Cloud apikey (saas) or platform API key (cpd) |
| `WX_USERNAME` | cpd only | required for cpd |
| `WX_URL_OR_REGION` | yes | saas: region code (default `us-south`); cpd: full cluster base URL |
| `WX_PASSWORD` | no | cpd fallback only, used instead of `WX_APIKEY` for the `/v1/authorize` bearer flow |
| `WX_PREFER_ZEN_API_KEY` | no | cpd only, default `true` |
| `WX_VERIFY_SSL` | no | default `true`; set `false` to skip TLS verification for `WX_URL_OR_REGION` — see below |
| `WX_CA_BUNDLE_PATH` | no | path to a CA bundle/cert to trust for `WX_URL_OR_REGION`, instead of disabling verification |

`WX_PASSWORD`/`WX_PREFER_ZEN_API_KEY`/`WX_VERIFY_SSL`/`WX_CA_BUNDLE_PATH`
are extensions beyond the four vars named explicitly in
`spec/01-auth-and-environments.md`, added for parity with `authenticate`'s
full parameter set (the spec's "Local convenience" bootstrap section names
only the first four).

### Self-signed / internal-CA certificates (CPD)

CPD clusters commonly serve a self-signed or internal-CA certificate that
isn't in a default trust store, which otherwise makes every tool call fail
with a TLS verification error. This isn't part of the spec (which covers API
request/response shapes, not client TLS config) — it's a client-side option
this server adds on top:

- `authenticate`'s `verify_ssl` parameter (or `WX_VERIFY_SSL=false`) skips TLS
  verification for calls to `region_or_url` (the CPD cluster itself, including
  its own `/v1/authorize`). It does **not** affect the SaaS IAM token
  endpoint, which is always a public IBM-operated host and is always
  verified.
- Prefer `ca_bundle_path` (or `WX_CA_BUNDLE_PATH`) when you have the
  cluster's CA certificate — it validates against that CA instead of skipping
  verification outright, and takes precedence over `verify_ssl` when both are
  set.
- Disabling verification removes protection against man-in-the-middle
  attacks on that connection; only do this for a cluster/network you trust.
  `authenticate` logs an MCP warning and echoes `tls_verify` in its response
  whenever verification is off, so it's never silent.

**Session state is per-connection, never a module-level global** — it's
attached to the MCP SDK's per-connection `ServerSession` object
(`watsonx_platform_mcp/session.py`). This is a hard design constraint from the
spec: the same code should work unmodified under a future remote/multi-tenant
transport, even though v1 only ships stdio.

## Tool confidence tiers

Every tool is implemented; confidence varies because the spec's own live
validation varied. Docstrings on each tool repeat these caveats — this table
is just the map.

### Production-confident (Phase 1 — live-verified against a real CPD cluster and non-production SaaS tenant)

`authenticate` · `list_projects` · `get_project` · `create_project` ·
`update_project` · `delete_project` · `list_assets` · `get_asset` ·
`create_asset` (generic `asset_type` case)

Also production-grade as cross-cutting layers: pagination normalization
(3 confirmed native shapes), error normalization (5 confirmed native error
envelopes + HTTP 460), and credential redaction (applied generically to every
tool's output, not per-tool).

### Confirmed live, happy-path only (Phase 2)

`create_job` (simple `asset_ref` shape only — `configuration` is a fully
opaque pass-through so the unverified Spark-style `asset_ref_type` shape can
flow through without a code change) · `list_jobs` · `get_job` · `delete_job` ·
`create_connection` (⚠️ **actively tests credentials live against the target
system** — fails with HTTP 460 on bad credentials; cannot be used
speculatively) · `list_connections` · `get_connection` ·
`list_connection_types`

### Experimental — low-confidence or entirely untested (Phase 3)

Implemented from the spec's contract, but treat the exact shapes as
provisional pending a live re-verification pass:

- `upload_asset_file` — only the create-data-asset step is verified; the
  attachment/signed-URL/complete sequence is this server's best-effort guess.
- `update_job` — the JSON-Patch request *format* is confirmed, but two live
  attempts to actually change a job's `schedule` value had no visible effect.
- `list_job_runs`, `get_job_run`, `cancel_job_run`, `create_job_run` — from an
  unverified example request, not independently re-verified live.
- `update_connection`, `delete_connection` — never tested (creating a real
  connection to test against requires genuine external-system credentials).
- `list_project_members`, `add_project_member`, `update_project_member`,
  `remove_project_member` — **zero testing done during spec validation**;
  every field name and endpoint is an unverified assumption.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Unit tests mock HTTP responses using the real captured payloads and error
envelopes quoted verbatim in `spec/02-tools.md`/`spec/03-conventions.md`.
Phase 3 tools have unit tests for the plumbing this server controls (request
shapes, confirm-guard enforcement), plus explicitly `@pytest.mark.skip`
integration stubs for behavior that can't be known without a live cluster —
see `tests/test_phase3_experimental.py`.

## References

- CCS API docs (SaaS): https://cloud.ibm.com/docs/apis/data-ai-common-core
- `cpdctl` command reference (Go CLI over the same APIs): https://github.com/IBM/cpdctl/blob/master/README_command_reference.md

## Spec index

- [`spec/00-overview.md`](spec/00-overview.md) — purpose, scope, non-goals, architecture, roadmap
- [`spec/01-auth-and-environments.md`](spec/01-auth-and-environments.md) — SaaS/CPD auth, session/target model
- [`spec/02-tools.md`](spec/02-tools.md) — full MCP tool catalog with I/O contracts
- [`spec/03-conventions.md`](spec/03-conventions.md) — pagination, error handling, security conventions
- [`spec/04-environment-differences.md`](spec/04-environment-differences.md) — Software/CPD vs SaaS payload differences (storage config, write-path routing, etc.)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
