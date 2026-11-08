# Tool Catalog

Conventions used throughout: `project_id` is always the CCS project GUID.
All `list_*` tools follow the pagination convention in
[`03-conventions.md`](03-conventions.md) (`limit`, `cursor` in →
`items`, `next_cursor` out). All tools operate against the *current*
authenticated session (see [`01-auth-and-environments.md`](01-auth-and-environments.md));
none take credentials directly.

Underlying endpoint paths are given as the SaaS `/v2/...` shapes documented
at the CCS API docs; CPD exposes the equivalent paths against the cluster's
base URL. Exact field names should be cross-checked against the live docs
during implementation — this catalog is the contract for *this server's*
tools, not a transcription of the raw API.

**Read vs write path caveat**: read-only `wraps` lines below (`list_*`/
`get_*`) were verified live against a CPD cluster, and so were
`create_asset`, `create_connection`, `create_job`, and asset/job delete —
all confirmed to share the same `/v2/...` path as their reads.
`create_project` is the **only** confirmed exception, routing through
`/transactional/v2/projects` instead — see
[`04-environment-differences.md`](04-environment-differences.md) for the
full picture, including the still-unresolved `storage` provisioning
question that blocked testing `create_project` end-to-end.
`update_project`/`delete_project`/`update_connection`/`update_job` remain
untested and should be treated as provisional.

## 1. Auth

### `authenticate`
Establish (or replace) the session's target and credentials.

- **in**: `environment` (`saas`|`cpd`), `region_or_url` (SaaS region code, or
  CPD cluster base URL), `apikey` (optional), `username` (required for
  `cpd`), `password` (optional, CPD fallback only), `prefer_zen_api_key`
  (bool, default `true`, CPD only)
- **out**: `environment`, `base_url`, `auth_method` (`iam`|`zen_api_key`|`cpd_bearer`),
  `expires_at` (null for `zen_api_key`)
- **wraps**: `POST /identity/token` (IAM) or `POST /icp4d-api/v1/authorize` (CPD bearer); ZenApiKey needs no call

## 2–3. Projects (discovery, creation)

### `list_projects`
- **in**: `name_contains` (optional), `type` (optional array — server-side
  filter on project context; allowable values `cpd`, `wx`, `wca`, `dpx`,
  `wxbi`; sent as a comma-separated `type` query param), `limit`, `cursor`
- **out**: `items[]` of `{id, name, description, created_at, updated_at, type, creator, public}`, `next_cursor`
- **wraps**: `GET /v2/projects?type=cpd,wx,...`
- **verified against a live CPD cluster**: `type` is a real, server-side
  filter **on CPD too** (not SaaS-only as originally assumed) — confirmed
  three ways: `?type=wx` against CPD-only project data returned
  `total_results: 0`; an invalid value returned `400` with
  `"reason": "Failed to parse request parameters: type query parameter value is invalid: bogus-garbage-value. Expected one of: cpd, wx, wca, dpx, wxbi."`;
  and the valid-value list matches exactly. Native response envelope is
  `{total_results, bookmark, resources: [{metadata: {guid, url, created_at, updated_at}, entity: {name, type, generator, public, creator, creator_iam_id, storage: {type, guid}, scope, catalog}}]}`
  — this server's `list_projects`/`get_project` flatten `metadata`+`entity`
  into the single normalized shape shown above (`id` ← `metadata.guid`).
  No `description` or `tags` field was present on real project entities —
  `description` is optional/often absent; **`tags` do not exist on the
  project entity at all** (see note under `create_project` below).
  Pagination is the opaque `bookmark` string, not offset/limit — matches the
  `cursor` abstraction already planned in `03-conventions.md`.

### `create_project`
- **in**: `name` (required), `description`, `generator` (optional,
  free-text product-origin marker — real values seen include `CPDCTL-CLI`,
  `wxd-portal-projects`), `public` (bool, optional), `type` (optional,
  e.g. `cpd` — same enum `list_projects` filters on), `storage` (required —
  object, environment-specific shape, **confirmed live on both
  environments, but they behave very differently**:
  - **`cpd`: this server should auto-fill**
    `{"type": "assetfiles", "guid": <uuid4 minted client-side>}` whenever
    the caller omits `storage` — the platform ignores the supplied guid
    and provisions its own, so no discovery/lookup step is needed.
  - **`saas`: cannot be auto-filled — must be supplied explicitly by the
    caller**, and needs a *real* pre-existing COS service instance:
    `{"type": "bmcos_object_storage", "guid": "<COS instance guid>", "resource_crn": "<real COS instance CRN>", "properties": {"bucket_name": "<fresh unique name>", "bucket_region": ..., "endpoint_url": ...}}`.
    The platform calls IBM Cloud's resource controller live using
    `resource_crn` to mint fresh credentials for the new project — see
    [`04-environment-differences.md`](04-environment-differences.md) for
    the full error-message trail that confirmed this.)
- **out**: `{id, name, created_at}` — **confirmed live on CPD**: success
  response is `{"location": "/v2/projects/{id}"}`, a relative URL — `id`
  must be parsed from the trailing path segment, not read from a `guid`/`id`
  field. `get_project` right after create does return the usual
  `metadata.guid` shape, so `create_project`'s response is genuinely
  different from every other tool's create response in this catalog; keep
  the defensive `location`/`metadata.guid`/`guid`/`id` extraction anyway
  for other environments/resource types.
- **wraps**: `POST /transactional/v2/projects` — **confirmed live**, not
  `POST /v2/projects` as originally assumed (see caveat above)
- **note (verified)**: dropped the originally-planned `tags[]` field —
  live project entities have no `tags` attribute at all. `cpdctl` has a
  separate `project tags` command family, meaning tags (if supported here)
  are managed via a distinct sub-resource, not a field on the project
  itself. Out of scope for v1 (not one of the 13 capabilities); add a
  dedicated `list_project_tags`/`add_project_tag`/etc. group later if
  needed, once that sub-resource's endpoint is confirmed.

## 4. Assets (discovery)

### `list_assets`
- **in**: `project_id` (required), `asset_type` (optional filter, e.g.
  `data_asset`, `script`, or a product-defined type string), `name_contains`,
  `limit`, `cursor`
- **out**: `items[]` of `{asset_id, name, asset_type, created_at}`, `next_cursor`
- **wraps**: `POST /v2/asset_types/asset/search?project_id=...` (verified
  reachable on CPD; native envelope is `{total_rows, results: []}` — a
  *different* shape from projects' `{total_results, bookmark, resources}`,
  confirming the pagination-normalization design in `03-conventions.md` is
  necessary, not just defensive. Empty-result response didn't surface a
  bookmark/cursor field — needs re-checking against a non-empty result set
  during implementation to confirm the exact cursor field name for this
  endpoint)

### `get_asset`
- **in**: `project_id`, `asset_id`
- **out**: full asset metadata document (attachments, attributes, relationships)
- **wraps**: `GET /v2/assets/{asset_id}?project_id=...`

## 5. Assets (creation)

### `create_asset`
Generic asset creation — the `asset_type` and `attributes` payload are
opaque pass-through so product layers can define their own asset shapes
without a change to this server.

- **in**: `project_id` (required), `name` (required), `asset_type`
  (required, e.g. `data_asset`, `script`, or a product-defined type),
  `description`, `attributes` (object, opaque per-type payload)
- **out**: `{asset_id, name, asset_type, created_at}`
- **wraps**: `POST /v2/assets?project_id=...` — **confirmed live** (not
  `/transactional/v2/assets`, which is `404` — the transactional-prefix
  pattern seen for `create_project` is *not* a general write-path
  convention, see `04-environment-differences.md`)
- **verified request/response envelope**: request body is
  `{"metadata": {"name": ..., "asset_type": ..., "origin_country": "us"}, "entity": {}}`
  (`attributes` in this tool's normalized input maps into the type-specific
  content of `entity`, empty for a bare `data_asset`). Response (`201`) is
  `{"metadata": {...full asset record incl. asset_id, project_id, catalog_id, created_at, owner_id, rov, usage...}, "entity": {}, "href": "/v2/assets/{id}?project_id=...", "asset_id": "..."}`
  — top-level `asset_id` is present directly, no defensive extraction
  needed for this one (unlike `create_project`). Delete confirmed too:
  `DELETE /v2/assets/{id}?project_id=...` → `204 No Content`.
- **data_asset entity shape (from a real example request, not independently
  re-verified live)**: `entity` for `asset_type: "data_asset"` is
  `{"data_asset": {"dataset": false, "mime_type": "text/x-script.python"}}`
  — my own live test used an empty `entity: {}`, which also succeeded but
  skips setting `mime_type`. `upload_asset_file` should populate this
  nested shape, inferring `mime_type` from the file extension.
- **type-specific convenience endpoint, seen but not independently
  verified**: the same example request posts to `POST /v2/data_assets?project_id=...`
  (not `/v2/assets`) for data asset creation. This suggests a pattern of
  `/v2/{type}s` convenience endpoints alongside the generic `/v2/assets`
  (matches `/v2/jobs` and `/v2/connections`, both separately confirmed to
  work for their respective `asset_type`s). Both `/v2/assets` (generic) and
  `/v2/data_assets` (type-specific) may work for data assets — worth
  settling on one during implementation rather than assuming equivalence.

## 6. Assets (file upload)

### `upload_asset_file`
Uploads file content as a data asset in one call (e.g. a Python script or a
CSV). **Live-verified on CPD 2026-11-08** (`cpd-cpd-instance.apps.wxdev.centralus.aroapp.io`,
throwaway project, since deleted). SaaS: same sequence assumed, **not tested**.

- **in**: `project_id` (required), `name` (required; plain file name — see
  below), `file_path` (local path) or `content_base64`, `mime_type` (optional,
  inferred from extension if omitted), `description`
- **out**: `{asset_id, attachment_id, name}`
- **wraps** (file first, then reference it):
  1. `PUT /v2/asset_files/data_asset/<name>?project_id=...` —
     `multipart/form-data`, one part named `file` = `(name, bytes, mime)`, plus
     the session's normal auth header. `201 {"status": "Asset created: The asset
     was successfully uploaded."}`. A raw-bytes PUT without auth is rejected
     ("Invalid content type").
  2. `POST /v2/assets?project_id=...` with
     `{"metadata": {"name", "asset_type": "data_asset", "origin_country": "us"},
     "entity": {"data_asset": {"dataset": false, "mime_type": <mime>}}}`
  3. `POST /v2/assets/{id}/attachments?project_id=...` with
     `{"asset_type": "data_asset", "name": "referenced", "description": "",
     "object_key": "/data_asset/<name>", "object_key_is_read_only": false,
     "is_partitioned": false}` → attachment has `transfer_complete: true`,
     `is_referenced: true`, `size` == file size; `GET /v2/assets/{id}` shows
     exactly one attachment. **No `/complete` call is needed.**
- **Why this order**: `object_key` in an attachment body means "reference an
  existing object", so sending it before any bytes exist fails with
  `NGPDL4108E Object does not exist key='/data_asset/<name>'` (the original
  bug). CPD's attachment response has no absolute/signed URL either — only a
  relative `url1`.
- **Do NOT use the "managed attachment" form** (attachment without
  `object_key`, `PUT` to `url1`, `/complete`): the bytes upload, but the Spark
  engine then resolves the job's application to `/project_data/assets/null`
  and fails with `application_file_not_found`.
- **Rollback**: if step 3 (or reading the attachment id) fails, the data asset
  is deleted (`DELETE /v2/assets/{id}`, best-effort; the original error is what
  surfaces). A failed step 1 creates nothing. The uploaded object itself is not
  removed on rollback.
- **`name` is sanitized** because it becomes part of the object key and URL
  path: no `/`, `\`, `?`, `#`, `%`, control characters, leading/trailing
  spaces, `.`/`..`, max 200 chars → `invalid_request`.
- **Same name uploaded twice** (live-observed): no error. The file at
  `/data_asset/<name>` is **overwritten**, and a second, separate data asset
  with the same name is created referencing the same object key. Both assets
  therefore point at the latest bytes; callers wanting isolation must use
  unique names.
- **End-to-end acceptance (live, CPD)**: upload a `.py` → `create_job`
  (`job_type="spark_application"`, `configuration` =
  `{"upload_id": <fresh uuid4>, "application": {"type": "data_asset",
  "asset_id": <asset id>}, "compute": {"instance_id": "1792053576647076",
  "engine_id": "spark503", "engine_name": "spark-5"}, "spark_version": "4.0",
  "spark_conf": {"spark.eventLog.enabled": "true"}, "spark_env": {},
  "arguments": []}`) → `create_job_run` → `get_job_run` reached `Completed`
  with `entity.job_run.runtime.return_code == "0"` and
  `runtime.application_details.application ==
  "/project_data/assets//data_asset/<name>"`. `upload_id` is required (any new
  UUID works); job names must be unique per project.

## 7. Connections

### `list_connection_types`
- **in**: `name_contains` (optional)
- **out**: `items[]` of `{datasource_type_id, name, label, required_properties[]}`
- **wraps**: `GET /v2/datasource_types`

### `create_connection`
- **in**: `project_id` (required), `name` (required), `datasource_type`
  (required, e.g. `s3`/`cos`/`bluemixcloudobjectstorage`), `properties`
  (object, opaque per-datasource-type — bucket, endpoint, credentials, etc.),
  `description`
- **out**: `{connection_id, name, datasource_type}`
- **wraps**: `POST /v2/connections?project_id=...` — **confirmed live**
  (`/transactional/v2/connections` is `404`, same finding as `create_asset`)
- **verified**: an invalid `datasource_type` value returns a real,
  specific validation error (`400`, `{"errors":[{"code":"invalid_payload","message":"A data source of the specified type [x] does not exist."...}]}`),
  confirming the endpoint actually validates against `list_connection_types`'
  catalog rather than accepting anything. `datasource_type` accepted a
  `datasource_types` asset guid in testing (not just the short name like
  `informix`) — worth confirming both forms work, or standardizing this
  tool's input on one. Missing `properties` also returns a real, specific
  error naming exactly which properties are required for that type (e.g.
  informix: `"One of the properties [host, password, port, server, username] is required."`) —
  this server could surface that error message as-is to guide the caller
  rather than needing its own per-type property validation.
- **Important, verified live: creation actively tests the credentials
  against the live external system, and fails if they don't work.** Trying
  a COS (`bluemixcloudobjectstorage`) connection with well-formed but fake
  `access_key`/`secret_key` returned a **non-standard HTTP status `460`**:
  ```json
  {"errors":[{"code":"invalid_data","message":"The connection could not be established: CDICO0103E: [IBM Cloud Object Storage] Invalid username or password: ... InvalidAccessKeyId ...","more_info":"Check the connection properties and ensure that the resource is accessible."}]}
  ```
  This means `create_connection` **cannot be validated, or used, with
  placeholder/fake credentials** — every call needs genuinely reachable
  external-system credentials, and this server's error handling must treat
  `460` as a distinct, non-retryable "credential/connectivity rejected by
  target system" case (see `03-conventions.md`; it doesn't fall into any
  standard HTTP status-code bucket, so it needs explicit handling, not a
  generic 4xx/5xx range check).
- **`auth_method` allowed values are connector-specific** — confirmed for
  `bluemixcloudobjectstorage`: `accesskey_secretkey`, `credentials_file`,
  `instanceid_apikey`, `instanceid_apikey_accesskey_secretkey`,
  `credentials` (a value of `basic_credentials`, seen in an unverified
  example against what may be a differently-named S3 connector type, was
  rejected here with a `400` listing this exact allowed-value list). Other
  real property fields confirmed/seen: `bucket`, `url`, `proxy`,
  `origin_country` (top-level, not under `properties`), `flags` (top-level
  array, purpose unconfirmed).

### `list_connections` / `get_connection` / `update_connection` / `delete_connection`
Standard CRUD, all scoped by `project_id` + `connection_id`.
- **wraps**: `GET|PATCH|DELETE /v2/connections/{id}?project_id=...`, `GET /v2/connections?project_id=...`
- **`update_connection`/`delete_connection` remain untested** — since
  `create_connection` needs real reachable credentials to succeed (see
  above), no connection could be created on this cluster to update/delete
  against. Validate these once real external-system credentials are
  available to test with.
- **verified**: `list_connections`' native envelope is `{resources: [], first: {href}, total_count}` —
  a *third* distinct shape (HAL-style `href` pagination links, no `bookmark`
  or `total_rows`), on top of the two seen for projects and assets. Reinforces
  that this server must normalize per-resource-type in its own client code
  rather than assuming one pagination style platform-wide.

## 8. Manage projects

### `get_project`
- **in**: `project_id`
- **out**: full project document
- **wraps**: `GET /v2/projects/{id}`

### `update_project`
- **in**: `project_id`, `name`, `description` (any subset, patch semantics —
  `tags[]` dropped, see `create_project`'s note)
- **out**: updated project document
- **wraps**: `PATCH /v2/projects/{id}` — **confirmed live**: body is a
  **flat, partial JSON object** merged directly (e.g. `{"description": "..."}`)
  — not wrapped in `entity`, and not a JSON Patch array. Contrast with
  `update_job` below, which needs a JSON Patch array — **PATCH body format
  is resource-type-specific on this platform, not a shared convention**;
  see `03-conventions.md`.

### `delete_project`
- **in**: `project_id`, `confirm` (must be `true` — destructive)
- **out**: `{deleted: true}`
- **wraps**: `DELETE /v2/projects/{id}` — **confirmed live**: `204`,
  immediate (a follow-up `GET` returned `404` right away, no soft-delete
  lag observed), and cascades to the project's contained assets/jobs (not
  independently verified whether it cascades to connections or members).

## 9. Manage project access

### `list_project_members`
- **in**: `project_id`, `limit`, `cursor`
- **out**: `items[]` of `{user_id_or_iam_id, role, type}`, `next_cursor`
- **wraps**: `GET /v2/projects/{id}/members`

### `add_project_member`
- **in**: `project_id`, `identity` (IAM id / username / service-id), `role` (`admin`|`editor`|`viewer`)
- **out**: `{added: true, identity, role}`
- **wraps**: `POST /v2/projects/{id}/members`

### `update_project_member`
- **in**: `project_id`, `identity`, `role`
- **out**: updated member entry
- **wraps**: `PATCH /v2/projects/{id}/members/{member_id}`

### `remove_project_member`
- **in**: `project_id`, `identity`, `confirm` (must be `true`)
- **out**: `{removed: true}`
- **wraps**: `DELETE /v2/projects/{id}/members/{member_id}`

## Structural note, verified live: **jobs are CCS assets** (`asset_type: "job"`)

Creating a job actually creates an asset — the response envelope is
byte-for-byte the same shape as `create_asset`'s (`metadata.asset_type:
"job"`, top-level `asset_id`), and the job-specific payload lives nested
under `entity.job {...}`. This means `job_id` *is* an `asset_id`, and
`delete_job` was confirmed to work via the plain asset delete endpoint
(`DELETE /v2/assets/{job_id}?project_id=...` → `204`) — there may or may not
also be a dedicated `/v2/jobs/{id}` DELETE; only the asset-delete path was
tested. This is useful context for implementation (job tools can likely
share code with asset tools) but doesn't change the tool *contracts* below.

## 10. Create job (any type)

### `create_job`
Generic job creation — `job_type` and `configuration` are opaque pass-through
(same extension principle as `create_asset`), so notebook/script/pipeline/
DataStage/product-specific job types all flow through one tool.

- **in**: `project_id` (required), `name` (required), `asset_ref` (id of the
  asset the job runs — script, notebook, pipeline, etc.), `job_type`
  (required, e.g. `notebook`, `script`, product-defined), `configuration`
  (object, opaque per-type: env vars, CPU/memory, parameters),
  `schedule` (optional — **shape corrected below**), `description`
- **out**: `{job_id, name, job_type, created_at}`
- **wraps**: `POST /v2/jobs?project_id=...` — **confirmed live**
  (`/transactional/v2/jobs` is `404`, consistent with `create_asset`/
  `create_connection`)
- **verified request/response envelope**: request body is
  `{"job": {"name": ..., "asset_ref": ..., "configuration": {}}}` — note
  the `{"job": {...}}` wrapper. `configuration: {}` normalizes server-side
  to `{"env_type": "", "env_variables": []}` for this simple job shape —
  these are real, named fields, not fully opaque. Response entity also
  included several clearly-deprecated-but-present fields
  (`last_run_initiator`, `last_run_time`, etc., literally valued
  `"deprecated field"` in this cluster's build) — ignore them in this
  server's normalized output.
- **Two distinct job "shapes" exist — resolves the earlier `asset_ref`/
  `asset_ref_type` "mutually exclusive" confusion.** There isn't one job
  schema; there are (at least) two:
  1. **Simple, project-asset-backed jobs** (notebook/script/pipeline):
     `asset_ref` alone (a project asset id). `asset_ref_type` defaults to
     `"data_asset"` server-side if omitted — this is the shape tested live
     above.
  2. **Rich, product-specific jobs** (e.g. Spark applications): use
     `asset_ref_type` **alone** (e.g. `"spark_application"`), with the
     actual execution target and all its parameters nested inside
     `configuration` instead of a top-level `asset_ref`. Real example
     (from an example request, not independently re-verified live on this
     cluster):
     ```json
     {"job": {"asset_ref_type": "spark_application", "name": "...", "description": "",
       "configuration": {
         "upload_id": "...",
         "application": {"type": "data_asset", "asset_id": "..."},
         "compute": {"instance_name": "...", "instance_id": "...", "engine_id": "...", "engine_name": "..."},
         "spark_version": "3.4",
         "spark_conf": {"spark.driver.cores": 1, "spark.driver.memory": "1G", "spark.executor.cores": 1, "spark.executor.memory": "1G", "ae.spark.executor.count": 1},
         "spark_env": {}, "arguments": ["..."]
       },
       "notification_types": {"success": true, "warning": true, "failure": true}
     }}
     ```
     `application.type` varies (`data_asset` with `asset_id`; `local` with
     a `path` under `spark_jobs/uploads/{upload_id}/application/...`;
     `connection` with `connection_id`) depending on where the Spark code
     lives. `compute` binds to a `watsonx.data`/Spark service instance
     (`instance_id` matches the `service_instances` API's ids seen
     earlier). `notification_types` (three booleans) is a richer
     alternative to the simple shape's single `enable_notifications` bool.
  This is a real-world confirmation that the opaque `configuration`
  pass-through design (`00-overview.md`'s extension model) is exactly
  right: this server should not try to model `configuration`'s internal
  shape at all — it varies per `asset_ref_type`/product and this server
  has no need to understand it, only forward it.
- **`schedule` is a plain cron-expression string, not an object —
  confirmed live.** Sending `"schedule": {...object...}` at creation fails
  (`400 /job/schedule must be string`); sending `"schedule": "0 9 * * *"`
  succeeds (`201`) and the server computes `future_scheduled_runs` (a list
  of upcoming ISO timestamps) automatically. `schedule_id` /
  `schedule_creator_id` remain separate, initially-empty fields on the job
  entity — **not yet fully understood**: after a job with a `schedule` was
  updated (any update, not necessarily to the schedule itself), both got
  populated, but two different attempts to actually *change* the `schedule`
  value via `PATCH` (see `update_job` below) did not visibly change it.
  Treat `create_job`'s `schedule` input as confirmed (plain cron string);
  treat *changing* an existing job's schedule as still open.

## 11. Edit job (including schedule)

### `update_job`
- **in**: `project_id`, `job_id`, `name`, `configuration`, `schedule`
  (plain cron string, per the correction above) — any subset, patch
  semantics
- **out**: updated job document
- **wraps**: `PATCH /v2/jobs/{id}?project_id=...` — **confirmed live, but
  with an important format difference from `update_project`**: the body
  must be a **JSON Patch (RFC 6902) array**, e.g.
  `[{"op": "replace", "path": "/job/schedule", "value": "0 20 * * *"}]` —
  a flat JSON object (`update_project`'s format) is rejected
  (`400 ... must be array`). **Unresolved**: two different `path` values
  were tried for changing `schedule` (`/job/schedule` and `/schedule`, both
  against the same job) — both returned `200` but neither visibly changed
  the stored `schedule` value on a follow-up `GET` (though `schedule_id`
  did get populated as a side effect the first time). The JSON-Patch
  *format* is confirmed; the correct `path` for actually mutating
  `schedule` is not. Needs another live pass — possibly the path needs to
  target a field this exploration didn't try, or schedule mutation needs a
  dedicated endpoint distinct from the general job PATCH.

## 12. Manage jobs and job runs

### `list_jobs`
- **in**: `project_id`, `name_contains`, `limit`, `cursor`
- **out**: `items[]` of `{job_id, name, job_type, schedule_enabled}`, `next_cursor`
- **wraps**: `GET /v2/jobs?project_id=...`

### `get_job`
- **in**: `project_id`, `job_id`
- **out**: full job document
- **wraps**: `GET /v2/jobs/{id}?project_id=...`

### `delete_job`
- **in**: `project_id`, `job_id`, `confirm` (must be `true`)
- **out**: `{deleted: true}`
- **wraps**: `DELETE /v2/jobs/{id}?project_id=...` — **confirmed live**
  (`204`), both via this dedicated path and via the generic
  `DELETE /v2/assets/{job_id}?project_id=...` (jobs are assets — either
  works; use the dedicated `/v2/jobs/{id}` form for clarity)

### `list_job_runs`
- **in**: `project_id`, `job_id`, `limit`, `cursor`
- **out**: `items[]` of `{run_id, state, created_at, duration}`, `next_cursor`
- **wraps**: `GET /v2/jobs/{id}/runs?project_id=...`

### `get_job_run`
- **in**: `project_id`, `job_id`, `run_id`
- **out**: full run document (state, logs pointer, results pointer)
- **wraps**: `GET /v2/jobs/{id}/runs/{run_id}?project_id=...`
- **live-verified 2026-11-08 (CPD)**: the Spark exit code is at
  `entity.job_run.runtime.return_code` (string, `"0"` on success) and the
  resolved script at `entity.job_run.runtime.application_details.application`.
  Run logs are a separate tool, `get_job_run_logs`.

### `get_job_run_logs`
- **in**: `project_id`, `job_id`, `run_id`, `max_lines` (default 200; `0` =
  all), `tail` (default true: keep the last `max_lines` lines)
- **out**: `{lines[], total_lines, returned_lines, truncated}`; read-only
- **wraps**: `GET /v2/jobs/{id}/runs/{run_id}/logs?project_id=...` →
  `{"results": ["<line>", ...]}` (200), then — best effort — the driver
  stdout/stderr files listed under
  `GET /v2/asset_files/spark_jobs/{id}/{run_id}/logs/` (`*-stdout`, `*-stderr`;
  fetched with `GET /v2/asset_files/<path>?project_id=...`), appended after a
  `=== <file> ===` marker with lines already in the first response skipped.
- **live-verified 2026-11-08 (CPD)**: for a *failed* script
  (`print("before failure"); raise ValueError("LIVE_BOOM")`) the `/logs`
  endpoint returned only the platform summary ("Application is in FAILED state
  ..."); the script's print output and Python traceback appeared only in the
  `spark-driver-*-stdout` file. Hence the merge above; with it the tool returns
  the traceback ending `ValueError: LIVE_BOOM`. On a successful run the
  `print` output (`LIVE_OK 5`) comes from the same file. The master/worker
  `.out` files in the listing are deliberately not included. Not tested on SaaS.
- Output is capped by default so it doesn't flood an LLM context; use the
  `tail` default for tracebacks.

### `cancel_job_run`
- **in**: `project_id`, `job_id`, `run_id`
- **out**: `{cancelled: true, state}`
- **wraps**: `POST /v2/jobs/{id}/runs/{run_id}/cancel?project_id=...` —
  **confirmed** (`POST`, not `DELETE`) via a real example request. Note there is
  also a *separate* `DELETE /v2/jobs/{id}/runs/{run_id}?project_id=...`
  endpoint, distinct from cancel — presumably deletes the run record
  entirely rather than stopping an in-progress run. Not one of the 13
  capabilities as its own tool, but worth knowing it exists as a distinct
  operation from `cancel_job_run` if a `delete_job_run`-style tool is added
  later.

### `delete_asset`
- **in**: `project_id`, `asset_id`, `confirm` (must be true)
- **out**: `{deleted: true}`
- **wraps**: `DELETE /v2/assets/{id}?project_id=...` — live-verified on CPD
  2026-11-08. For a referenced data asset only the record goes; the uploaded
  object under `/data_asset/<name>` remains.

## 13. Trigger job runs

### `create_job_run`
- **in**: `project_id`, `job_id`, `configuration` (optional per-run override,
  e.g. runtime parameters for this run only)
- **out**: `{run_id, state}` — returns immediately, does not block until
  completion (see async convention in `03-conventions.md`)
- **wraps**: `POST /v2/jobs/{id}/runs?project_id=...` — **body wrapper
  confirmed** via a real example request: `{"job_run": {"name": "test run"}}`
  (i.e. `{"job_run": {...}}`, not `{"run": {...}}` as might otherwise be
  assumed — matches the `{"job": {...}}` wrapper pattern used for jobs
  themselves).

### `run_spark_script` (helper)
- **in**: `project_id`, `script` (PySpark text), `instance_id`, `engine_id`,
  `engine_name` (all required — compute is never defaulted), optional
  `name_prefix`, `spark_version` (default "4.0"), `arguments`, `spark_conf`,
  `inject_wxd_credentials` (default true)
- **out**: `{asset_id, job_id, run_id, state, script_name}` — returns
  immediately; poll `get_job_run`, read `get_job_run_logs`.
- **does**: `upload_asset_file` (unique name `<prefix>-<8 hex>.py`) →
  `create_job` (`spark_application`, fresh `upload_id`, same unique name, so
  no job-name clashes) → `create_job_run`. Failure after upload deletes the
  job (if created) and asset. On success they are kept (logs live under the
  job); clean up with `delete_job` / `delete_asset`.
- **live-verified 2026-11-08 (CPD)**: script printed `HELPER_OK 42`, run
  `Completed`, return code 0, output found via `get_job_run_logs`.

### Spark credential injection (`create_job.inject_wxd_credentials`)
Catalog access from a platform job requires `spark_conf`
`spark.hadoop.wxd.apiKey = "ZenApiKey " + base64(username:apikey)`. With
`inject_wxd_credentials=true` (spark_application only; also used by
`run_spark_script`) the server builds it from the session's own CPD
username + API key and adds it to a copy of `configuration.spark_conf`, so the
value never passes through the caller. SaaS / no-username sessions get
`invalid_request`. Redaction masks the key `spark.hadoop.wxd.apiKey` and any
string value starting `ZenApiKey `; live-verified that `get_job_run` and
`get_job` show `***REDACTED***`. Limit: the credential is still stored in the
job config on the platform, visible to anyone with job read access there.
Catalog access itself (a script querying a catalog) was not exercised live.

---

## Tool count summary

~26 tools across 8 groups (auth, projects, assets, connections, project
management, access, jobs, job runs). This is more than a minimal "one per
numbered capability" count because several capabilities (manage
projects/jobs/access) decompose into standard CRUD — kept as separate tools
per the "curated fixed tools" decision, rather than collapsing into fewer
tools with an `action` enum parameter, so each tool has a narrow, clear
contract for the calling model.
