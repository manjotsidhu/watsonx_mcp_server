# Overview

## Background

IBM watsonx runs on a shared substrate called **Common Core Services (CCS)**:
Projects, Assets, Connections, Jobs, Environments, and access/member management.
Every product built on watsonx (watsonx.ai, watsonx.data, watsonx.governance,
DataStage, Pipelines, Notebooks, ML/Deployments, MDM, ...) is a *layer on top*
of CCS — same REST APIs, but each product defines its own custom **asset
types**, **job configurations**, and **environment/spec** shapes and stores
them through the generic CCS endpoints. `cpdctl` (IBM's Go CLI) mirrors this
exactly: it has one `asset`/`connection`/`job`/`project` command family, and
separate `wx-ai`, `wx-data`, `ml`, `datastage`, `mdm`, `pipeline` families that
are thin, product-specific wrappers around the same underlying primitives.

The platform ships in two deployment forms that expose (mostly) the same API
surface with different auth and base URLs:

- **SaaS** — multi-region, IBM Cloud IAM-authenticated (watsonx.ai, watsonx.data
  as-a-service, etc.)
- **Software / CPD** — on-prem or private-cloud, cluster-local auth

## Purpose of this MCP server

Give products, agents, and AI assistants a single, product-agnostic MCP server
for the CCS layer, so they can:

- authenticate against either a SaaS tenant or a CPD cluster
- discover and manage projects
- discover, create, and upload assets in a project
- create and manage connections to external data sources
- create, edit, schedule, and run jobs of any type
- manage project access

This is **infrastructure for other things to be built on**, not a
product-specific tool. It should never need to know what a "prompt template"
or a "Spark engine" or a "DataStage flow" is — those are product concerns
layered on top, the same way `wx-ai` and `wx-data` are layered on top of
`asset`/`job`/`connection` in `cpdctl`.

## In scope (v1)

Mapped to the 13 capabilities from the original request:

| # | Capability | Tool(s) |
|---|---|---|
| 1 | Authenticate (apikey, +username for CPD) | `authenticate` |
| 2 | Discover projects (optionally filtered by product context, SaaS only) | `list_projects` |
| 3 | Create project | `create_project` |
| 4 | Discover assets in a project | `list_assets`, `get_asset` |
| 5 | Create assets in a project | `create_asset` |
| 6 | Upload asset via file (e.g. a Python script) | `upload_asset_file` |
| 7 | Create connection (S3, IBM COS, etc.) | `create_connection`, `list_connection_types` |
| 8 | Manage projects | `get_project`, `update_project`, `delete_project` |
| 9 | Manage access to a project | `list_project_members`, `add_project_member`, `update_project_member`, `remove_project_member` |
| 10 | Create job (any type) | `create_job` |
| 11 | Edit job (incl. schedule) | `update_job` |
| 12 | Manage jobs and job runs | `list_jobs`, `get_job`, `delete_job`, `list_job_runs`, `get_job_run`, `cancel_job_run` |
| 13 | Trigger job runs | `create_job_run` |

Full contracts for every tool are in [`02-tools.md`](02-tools.md).

## Out of scope (v1) — explicitly deferred to product-specific layers

- **Model lifecycle / deployments** (`ml` in cpdctl): training, deployment
  spaces, scoring/inference — belongs to a watsonx.ai(-serving)-specific
  MCP server.
- **watsonx.ai generative AI operations**: prompt templates, foundation model
  chat/generate/embeddings, fine-tuning — a separate,
  product-specific MCP server; that is exactly the kind of layer this spec
  deliberately stays below.
- **watsonx.data lakehouse operations**: buckets, engines, table maintenance —
  product-specific.
- **Catalogs and Spaces**: catalogs (asset sharing/governance) and deployment
  spaces are adjacent CCS-family resources but are not in the original 13
  capabilities; add later as their own tool group if needed, following the
  same pattern as Projects.
- **DataStage / Pipelines / Notebooks / MDM specifics**: these define their
  own asset/job configuration shapes on top of the generic `create_asset` /
  `create_job` tools. This server must not special-case them; a product team
  building e.g. a DataStage MCP server should be able to compose these CCS
  tools directly.
- **Search** (`/v3/search`) — useful later, not in the original 13, add as its
  own tool group.

## Extension model

`create_asset` and `create_job` accept an opaque `asset_type` /
`job_type`+`configuration` payload rather than a fixed enum of known types.
The CCS MCP server validates only the generic envelope (project id, name,
required top-level fields); it does not understand or validate
product-specific configuration schemas. This is what lets a future
watsonx.ai-specific or DataStage-specific MCP server (or an LLM operating
directly) build custom asset/job types through this same generic layer,
without this server ever needing a new release to support a new product.

## Architecture summary

- **Language**: Python, official MCP SDK.
- **Transport (v1)**: stdio, single local process — matches how Claude
  Code/Desktop normally runs an MCP server.
- **Transport (future)**: the same session/auth model must work unmodified
  over a remote multi-tenant HTTP transport. This is achieved by never using
  process-global state for credentials or target — see
  [`01-auth-and-environments.md`](01-auth-and-environments.md). This is a
  design constraint on v1, not a v1 feature: no remote transport, deployment
  story, or multi-tenant isolation testing is being built now.
- **HTTP client**: talks directly to the CCS REST APIs (SaaS IAM-authenticated
  or CPD cluster-authenticated) — no dependency on `cpdctl` itself, though its
  command reference is a useful cross-check for request/response shapes.

## Suggested build order (not a hard phase gate — all 13 capabilities are in scope for v1)

1. Auth/session (`authenticate`) + environment resolution (SaaS region / CPD URL)
2. Projects: `list_projects`, `get_project`, `create_project`
3. Assets: `list_assets`, `get_asset`, `create_asset`, `upload_asset_file`
4. Connections: `list_connection_types`, `create_connection`, CRUD
5. Project management + access: `update_project`, `delete_project`, member tools
6. Jobs: `create_job`, `update_job`, job/job-run CRUD, `create_job_run`

This order front-loads the pieces every later capability depends on
(authenticated session → a project to act in → assets/connections to
reference from a job).

## Resolved (previously open) assumptions

- **Hosts**: SaaS uses a fixed, confirmed table of 6 IBM-set regional hosts
  (`us-south`/Dallas default; `eu-de`, `eu-gb`, `jp-tok`, `au-syd`, `ca-tor`
  — note `au-syd`/`ca-tor` use a different second-level domain, `dai.` not
  `dataplatform.`); CPD uses an arbitrary customer-specific route with no
  derivable pattern (must always be supplied in full by the caller).
  Relative API paths are identical across both. See
  [`01-auth-and-environments.md`](01-auth-and-environments.md).
- **Project context filter**: confirmed as a real server-side filter —
  `GET /v2/projects?type=<comma-separated>`, allowable values `cpd`, `wx`,
  `wca`, `dpx`, `wxbi` — enforced with a `400` on invalid values. **Verified
  live against a CPD cluster that it works on CPD too**, not SaaS-only as
  originally reported; corrected in `list_projects` (`02-tools.md`).
- **CPD auth shapes**: verified live — `/v1/authorize` needs the field name
  `api_key` (not `apikey`) for a platform API key, returns
  `{_messageCode_, message, token}` on success (`401` with a different shape
  on failure), and the token is valid for **12 hours**. ZenApiKey needs no
  call at all and worked immediately against `GET /v2/projects`. See
  [`01-auth-and-environments.md`](01-auth-and-environments.md).
- **Pagination/error envelope diversity**: verified live that projects,
  asset search, and connections each use a *different* native pagination
  shape, and that at least two different native error shapes exist
  (standard API errors vs. `/v1/authorize` failures) — see
  [`03-conventions.md`](03-conventions.md). This confirms the
  per-resource-type normalization design was necessary, not speculative
  defensiveness.
- **Project entity has no `tags` field** — dropped from `create_project`'s
  planned inputs; see the note under that tool in `02-tools.md`.
- **Write path**: `/transactional/v2/...` is **not** a general write-path
  convention — confirmed `404` for assets/connections/jobs. Only project
  *creation* uses it; every other create/delete tested shares its read
  path. See [`04-environment-differences.md`](04-environment-differences.md).
- **Jobs are CCS assets** (`asset_type: "job"`) — `job_id` is an `asset_id`,
  confirmed by both the create response shape and by deleting a job via the
  plain asset-delete endpoint. See `02-tools.md`.
- **`create_job`'s `schedule` shape was wrong, now corrected**: `schedule`
  is a **plain cron-expression string** (e.g. `"0 9 * * *"`), not an
  object — confirmed live (an object is rejected with `400 .../schedule must be string`).
  The server auto-computes `future_scheduled_runs` from it.
- **Jobs have (at least) two distinct configuration shapes**: simple
  project-asset-backed jobs (`asset_ref` alone) vs. rich product-specific
  jobs like Spark applications (`asset_ref_type` alone + a detailed,
  product-specific `configuration` object) — resolving the earlier
  "mutually exclusive" confusion, and a strong real-world confirmation that
  `create_job`'s opaque `configuration` pass-through design was the right
  call. See `02-tools.md`.
- **Job run lifecycle endpoints confirmed** (from a real example request):
  `create_job_run` wraps `{"job_run": {...}}` (not `{"run": {...}}`);
  `cancel_job_run` is confirmed `POST .../cancel` (not `DELETE`, resolving
  that earlier TBD); a separate `DELETE .../runs/{run_id}` also exists,
  distinct from cancel; and `GET .../runs/{run_id}/logs` is a real endpoint
  worth exposing via `get_job_run`'s output later.
- **Five distinct native error envelope shapes** now confirmed (four from
  before, plus connection creation's non-standard `HTTP 460` for a failed
  live credential test) — see `03-conventions.md`.
- **PATCH body format is resource-type-specific, confirmed live**:
  `update_project` takes a flat partial object; `update_job` requires a
  JSON Patch (RFC 6902) array. No shared PATCH convention exists platform-
  wide — see `03-conventions.md`.
- **`create_connection` actively live-tests credentials against the
  external system** and fails with `460` if they don't work — it cannot be
  exercised with placeholder credentials, which is *why* `update_connection`/
  `delete_connection` remain unvalidated (nothing could be created to test
  against). This is now a well-understood constraint, not a mystery.
- **`create_project`'s Software/CPD `storage` blocker is resolved**: the
  client-supplied `guid` is arbitrary/ignored — the platform provisions its
  own `assetfiles` storage regardless of what's sent. This server should
  mint a `uuid4()` for `storage.guid` automatically on `cpd` sessions when
  the caller doesn't supply one. `create_project` was validated end-to-end
  live (create → get → delete, `201`/`200`/`204`), and so was
  `update_project` (flat-object PATCH) and `delete_project` (immediate,
  `204`, cascades to contained assets/jobs). See
  [`04-environment-differences.md`](04-environment-differences.md).
- **`create_project`'s success response shape**: `{"location": "/v2/projects/{id}"}`
  — confirmed live, and different from every other tool's create response
  in this catalog (which return the id directly or under `metadata.guid`).
- **Asset-search pagination cursor confirmed**: the search endpoint returns
  a `next: {query, limit, bookmark, skip, include_total_rows}` object —
  the *entire* object must be echoed back as the next request's body to
  page forward, not just a single bookmark token. Confirmed working across
  two pages of real data.
- **`data_asset` entity shape and the attachment-creation body** partially
  confirmed via a real example request (`entity.data_asset: {dataset, mime_type}`;
  attachment body includes `object_key`, `object_key_is_read_only`,
  `is_partitioned`) — see `upload_asset_file` in `02-tools.md`. The
  signed-URL-PUT-then-complete steps of that flow are still unconfirmed.
- A `/v2/data_assets` type-specific convenience endpoint (parallel to
  `/v2/assets`) was seen in an example request — not independently verified
  whether it's required, optional, or equivalent to the generic path.
- **SaaS IAM auth confirmed live**: `{access_token, refresh_token: "not_supported", token_type, expires_in: 3600, expiration, scope}`
  — same "no refresh token, just re-exchange the apikey" pattern as CPD.
- **SaaS COS `storage` shape for `create_project` — resolved, confirmed
  live**: needs a top-level `resource_crn` (a *real* COS service instance
  CRN — the platform calls IBM Cloud's resource controller live to mint
  credentials against it) plus a shared `guid` and a fresh unique
  `properties.bucket_name`. Unlike CPD, **there is no client-side shortcut**
  — a real, accessible COS instance CRN is required, confirmed by a `500`
  citing a genuine resource-controller `404` for a guessed CRN. See
  `04-environment-differences.md`.
- **Critical security finding, confirmed live**: SaaS `get_project`/
  `list_projects` can return **live external credentials** (COS
  admin/editor/viewer API keys and S3 access/secret keys) embedded in
  `entity.storage.properties.credentials`. Redaction (`03-conventions.md`)
  must cover every tool that can return a project document, not just
  connection-shaped resources as originally scoped.

## Open assumptions to confirm during implementation

- **Changing an existing job's `schedule` via `update_job`** — the JSON
  Patch *format* is confirmed, but two different `path` values tried
  against `/job/schedule` didn't visibly change the stored value. Needs
  another live pass.
- `update_connection`, `delete_connection` — blocked on needing real,
  reachable external-system credentials to create something to test
  against in the first place (see `create_connection`'s live-test finding
  above); not a spec gap, just needs real creds when available.
- The exact `upload_asset_file` sequence after attachment creation (signed
  URL for the PUT? a "complete" call?) — only the attachment-creation step
  itself has a confirmed real shape so far.
- Whether `/v2/data_assets` (vs. generic `/v2/assets`) is required,
  preferred, or just an equivalent alternative for data asset creation.
- Whether `create_project`'s SaaS `storage.resource_crn` is meant to come
  from this server's own `create_connection` tool, or is assumed to be
  pre-existing IBM Cloud account infrastructure outside this server's
  scope — not confirmed live (ran out of test-credential time before
  reaching a real COS CRN to complete the full create flow).

## Validated against a live CPD cluster and a live non-production SaaS tenant

Five passes so far. **CPD, read/list**: `authenticate` (both ZenApiKey and
`/v1/authorize`), `list_projects` (incl. the `type` filter), `get_project`,
`list_assets` (incl. pagination across real data), `list_jobs`,
`list_connections`, `list_connection_types`. **CPD, create/update/delete**:
`create_asset` + delete, `create_job` (both the simple and scheduled
variants) + delete, `update_job` (format confirmed, schedule-mutation
semantics still open), `create_project` end-to-end (create → get → update →
delete), and `create_connection` (confirmed to actively test credentials
live; validation-error path only, since no real external-system
credentials were available). **SaaS (non-production tenant)**: IAM token exchange,
`list_projects` (incl. `type` filter), `get_project` (which is also where
the live-credential-leak finding came from), and `create_project`'s
`storage` validation chain (three attempts, each producing a more specific
error, converging on the real `resource_crn` requirement) — blocked short
of a full success only by not having a real COS instance CRN to finish
with. A separate set of example requests from another CPD cluster (not
run during these passes) filled in job-run lifecycle endpoints, the
Spark-style job configuration shape, and the data-asset/attachment
creation shapes — flagged throughout as "from an example request" wherever
not independently re-verified live.
