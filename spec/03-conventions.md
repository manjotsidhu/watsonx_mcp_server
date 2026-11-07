# Conventions

## Pagination

Every `list_*` tool takes:
- `limit` (default 50, max 200)
- `cursor` (opaque string, omit for the first page)

and returns:
- `items[]`
- `next_cursor` (omitted/null when there are no more pages)

The underlying CCS APIs use different native pagination schemes across
resource types — **confirmed live against a CPD cluster**, not just assumed:

| resource | native envelope |
|---|---|
| projects | `{total_results, bookmark, resources: []}` |
| asset search | `{total_rows, results: []}` |
| connections | `{resources: [], first: {href}, total_count}` |

Three different shapes for three resource types on the same platform.
`cursor` is an opaque value this server encodes/decodes per-resource-type
internally so every tool looks identical to the caller regardless of what
the underlying API does. Never expose the native pagination mechanics
through the MCP tool surface.

## Error handling

All errors are normalized to `{code, message, retryable}` before being
surfaced as a tool error:

| HTTP status | `code` | `retryable` | notes |
|---|---|---|---|
| 400 | `invalid_request` | false | include the API's validation detail in `message` |
| 401 | — | — | handled internally: one transparent re-auth + retry (see `01-auth-and-environments.md`); only surfaced to the caller if the retry also fails |
| 403 | `forbidden` | false | insufficient role/access on the target project or resource |
| 404 | `not_found` | false | |
| 409 | `conflict` | false | e.g. name collision |
| 429 | `rate_limited` | true | honor `Retry-After` if present |
| 5xx | `upstream_error` | true | |
| **460** | `connection_test_failed` | false | **non-standard status, confirmed live** — `create_connection` actively tests credentials against the external system at creation time; `460` means the target system rejected them (e.g. invalid access key). Not a standard HTTP code, so a naive "5xx is retryable" range check would misclassify it — handle it as its own explicit case, and treat it as non-retryable (the credentials are wrong, not the network). See `02-tools.md`'s `create_connection`. |
| network/timeout | `network_error` | true | |

This server does not implement automatic retries beyond the single 401
re-auth case — retryable errors are surfaced to the caller (the calling
model/agent) to decide whether to retry, rather than silently retrying
potentially non-idempotent POSTs.

**Verified native error envelope (CPD, 400 case)**:
```json
{"id":"WSCPA0000E","code":400,"error":"Bad Request",
 "reason":"Failed to parse request parameters: ...",
 "message":"The server cannot or will not process the request due to an apparent client error (e.g. malformed request syntax)."}
```
and (401 from `/v1/authorize`, a different endpoint family with its own
shape):
```json
{"_statusCode_":401,"exception":"Sorry, we could not verify your credentials.","message":"Unauthorized"}
```
A third shape, from `POST /transactional/v2/projects` missing a required
field:
```json
{"code":400,"error":"Bad Request","reason":"Missing required properties for project: storage",
 "message":"...","description":"[400] Bad Request: ...","transactionId":"..."}
```
And a fourth, from the connections subsystem (`wdp-connect-connection`),
wrapping errors in an array with its own nested `extra` block:
```json
{"trace":"...","errors":[{"code":"invalid_payload","message":"CDICO2034E: One of the properties [host, password, port, server, username] is required.",
  "more_info":"null","extra":{"environment_name":"icp4data","http_status":400,"id":"CDICO9007E", ...}}]}
```
Four different native error shapes observed already on one cluster — the
normalized `{code, message, retryable}` table above is this server's own
contract; map `reason` (when present, more specific), `exception`/`message`,
or `errors[0].message` into the normalized `message` field, never assume
one raw error shape covers every endpoint. In several cases (missing
`storage`, missing connection properties, invalid `datasource_type`) the
platform's own error message is specific and actionable enough to surface
to the calling model close to verbatim, rather than this server trying to
re-derive or improve on it.

## PATCH body format is resource-type-specific (RESOLVED, verified live)

There is no one PATCH convention on this platform:
- `update_project` → `PATCH /v2/projects/{id}` takes a **flat, partial JSON
  object** (e.g. `{"description": "..."}`), merged directly.
- `update_job` → `PATCH /v2/jobs/{id}` requires a **JSON Patch (RFC 6902)
  array** instead (e.g. `[{"op": "replace", "path": "/job/schedule", "value": "..."}]`)
  — the flat-object form is rejected outright (`400 ... must be array`).

This server's implementation needs a per-resource-type PATCH-body builder,
not one shared "merge this object" helper — assuming one convention across
`update_project`/`update_connection`/`update_job` would silently break at
least one of them. `update_connection`'s PATCH format is untested; don't
assume it matches either of the two confirmed shapes without checking.

## Destructive operations

Any `delete_*`/`remove_*`/`cancel_*` tool requires an explicit `confirm: true`
input field. This is a belt-and-suspenders guard at the tool-schema level, in
addition to whatever confirmation the calling agent/host applies — the tool
call itself fails fast with a validation error if `confirm` is missing or
false, rather than silently proceeding.

## Async operations (job runs)

`create_job_run` and `cancel_job_run` return immediately with the run's
current `state` — they do not block waiting for completion. There is no
blocking "wait for run" tool in v1: an MCP tool call holding a connection
open for a potentially long-running job is a poor fit for the request/
response model, and would behave especially badly under a future remote/
multi-tenant transport. Callers poll `get_job_run` / `list_job_runs`
themselves.

## Security / redaction

- Credentials (apikeys, passwords, tokens, connection `properties` that
  contain secrets) are never included in tool output, logs, or error
  messages.
- `create_connection`'s `properties` object may contain secrets (e.g. an S3
  secret key) — these must be passed through to the CCS API but never
  echoed back in the tool's `out` payload; `get_connection`/`list_connections`
  must redact any known-sensitive property keys in their responses.
- **Critical, confirmed live on SaaS — the credential leak surface is
  broader than connections.** `get_project`/`list_projects` on a real SaaS
  tenant returned projects whose `entity.storage.properties.credentials`
  block contained **live IBM Cloud API keys and S3 access/secret keys**
  (admin/editor/viewer service credentials for the project's COS bucket),
  generated by the platform at project-creation time and stored directly
  on the project resource. **`get_project`, `list_projects`, and any other
  tool that can return a project document must redact
  `entity.storage.properties.credentials` (and recursively, any nested
  object matching common secret-key field names — `api_key`,
  `secret_access_key`, `access_key_id`, `password`) before returning to the
  caller.** This is not hypothetical: it was observed on real project data
  during spec validation. Do not assume redaction is
  only needed for connection-shaped resources — apply it as a generic
  post-processing pass over every tool's output, not per-tool special
  cases, since more resource types may carry embedded credentials than
  have been discovered so far.

## Environment differences (SaaS vs CPD)

Tool contracts (`in`/`out` shapes) are identical across `saas` and `cpd`
sessions — the environment only affects the base URL and auth mechanics
(`01-auth-and-environments.md`). Any field that is genuinely only meaningful
on one environment (if discovered during implementation) should be
documented per-tool in `02-tools.md` rather than creating environment-
specific tool variants.
