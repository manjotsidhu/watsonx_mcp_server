# Environment Differences (Software/CPD vs SaaS)

Tool *contracts* (`in`/`out` shapes) are meant to stay identical across
environments per `03-conventions.md` — but the actual **payload values**
some fields need often differ by environment, because the two deployment
forms provision underlying infrastructure differently. This file collects
those differences as they're confirmed, so `02-tools.md` can stay a clean
contract reference instead of a pile of environment caveats.

## `/transactional/v2/...` is project-specific, not a general write-path rule (RESOLVED)

Originally flagged as needing verification across every mutating endpoint.
**Now resolved, live**: `/transactional/v2/assets`, `/transactional/v2/connections`,
and `/transactional/v2/jobs` all return `404 Not Found` — only
`/transactional/v2/projects` is real. `create_asset`, `create_connection`,
and `create_job` all succeed (or return a meaningful validation error, not
404) against the plain `/v2/...` path, identical to their read paths.

So: **projects are the outlier**, not the rule. Reads and writes share one
path for assets, connections, and jobs; only projects route creation
through `/transactional/v2/projects` while reads stay on `/v2/projects`.
`update_project`/`delete_project` haven't been tested — worth checking
whether they too need the `/transactional/v2/` prefix, or only `POST`
(create) does.

## Project storage provisioning on Software/CPD — RESOLVED: the guid is arbitrary

Originally looked like a blocker (see git history / earlier note): `storage`
is strictly required (`400 Missing required properties for project: storage`
when omitted), and the `guid` isn't discoverable through the generic
service-instance provisioning API (`GET`/`POST /zen-data/v3/service_instances`
— no `assetfiles` addon type exists there; `POST` with `addon_type:
"assetfiles"` returns `412 fetch_addon_error`).

**Confirmed live**: the `guid` you send is not actually used — the platform
auto-provisions its own `assetfiles` storage and returns *its own* guid,
ignoring the client-supplied value entirely.

```
request:  {"storage": {"type": "assetfiles", "guid": "11111111-1111-4111-8111-111111111111"}}   (freshly minted, not looked up anywhere)
response: 201 {"location": "/v2/projects/22222222-2222-4222-8222-222222222222"}
GET on that project shows: "storage": {"type": "assetfiles", "guid": "33333333-3333-4333-8333-333333333333"}   (different guid — server-assigned)
```

So on CPD, `create_project`'s `storage` for the default PVC-backed case is
simply `{"type": "assetfiles", "guid": <any freshly generated UUID>}` — no
prior lookup or provisioning call needed. This server should generate a
`uuid4()` client-side for this field whenever the caller doesn't supply
their own `storage` object on a `cpd` session, rather than requiring the
calling model to know or discover anything.

**Also confirmed while testing this**: `create_project`'s success response
is `{"location": "/v2/projects/{id}"}` — a relative URL, not `metadata.guid`
or a top-level `id`/`guid`. This is the *first* of the four defensively-
handled shapes (`location`, `metadata.guid`, `guid`, `id` — see the
response-shape note below) actually observed live; the id must be parsed
from the trailing path segment.

## Project creation: `storage` payload differs by environment

`create_project`'s `storage` field is intentionally opaque pass-through
(see `00-overview.md`'s extension model) — but callers (including an LLM
calling this tool) need to know what shape to actually put in it per
environment, since the API won't auto-provision the backing storage:

### Software / CPD

```json
{"type": "assetfiles", "guid": "44444444-4444-4444-8444-444444444444"}
```

`assetfiles` storage is **PVC-backed** and the `guid` must reference an
**already-existing** storage volume/instance on the cluster — this call does
not create one. Confirmed from the same reference test suite (hardcoded guid reused
across test runs).

**Open question**: what endpoint discovers the right `guid` to use (a
per-cluster default storage instance? a per-user one? enumerable via some
`GET /zen-data/...` or `GET /v2/storage_instances`-style call)? Not yet
confirmed — needs either a live lookup call once we know the endpoint, or a
question back to whoever operates a target cluster. This matters for the
MCP tool design: if there's a discoverable default, `create_project` should
probably resolve it automatically when the caller omits `storage` on a
`cpd` session, rather than requiring every caller to already know a guid.

### SaaS — RESOLVED shape, confirmed live against a real non-production SaaS tenant

Needs a **Cloud Object Storage (COS) service instance** that must already
exist — confirmed by reading real projects' `entity.storage` and by
provoking two different, informative errors while attempting creation:

```json
"storage": {
  "type": "bmcos_object_storage",
  "guid": "<COS service instance guid>",
  "resource_crn": "crn:v1:<stage>:public:cloud-object-storage:global:a/<account_id>:<instance_guid>::",
  "properties": {
    "bucket_name": "<new, unique bucket name for this project>",
    "bucket_region": "us-south",
    "endpoint_url": "https://s3.<region>.cloud-object-storage.<domain>"
  }
}
```

Confirmed facts, in the order discovered:
1. **The `guid` is real and *shared* across many projects** — unlike CPD,
   where the client-supplied guid is arbitrary and ignored. Multiple real
   projects on the same tenant had identical `storage.guid` values,
   confirming it references one pre-existing COS instance reused by many
   projects, not a per-project record.
2. **`resource_crn` is required at the top level of `storage`** (not
   inside `properties` — a first attempt with it under `properties` was
   rejected with `400 Missing required properties for bmcos storage: resource_crn`
   even though the value was present, just at the wrong nesting level).
3. **`resource_crn` must be a real COS service instance CRN the caller's
   account can resolve** — a syntactically-plausible but not-quite-real
   CRN produced `500 Unable to create Administrator service credentials for your Cloud Object Storage instance`,
   with a nested cause showing IBM Cloud's resource controller returned a
   genuine `404 ... An attempt to get the service instance failed` for
   that CRN. **This confirms `create_project` on SaaS actively calls out to
   IBM Cloud's resource controller in real time** to mint fresh
   Administrator/Editor/Viewer service credentials scoped to that COS
   instance, for the new project — this is a live provisioning step, not a
   formality, and it will fail hard if the CRN doesn't resolve to a real,
   accessible COS instance.
4. **Existing projects' `storage.properties` include a `credentials` block**
   with real, live IBM Cloud API keys and S3 access/secret keys for
   admin/editor/viewer roles, generated by the platform at project-creation
   time. **This is a critical, load-bearing finding for tool design, not
   just a curiosity: `get_project`/`list_projects` on SaaS can return live
   external credentials embedded in the response.** This server's
   redaction rules (`03-conventions.md`) must strip
   `entity.storage.properties.credentials` (and any similarly-shaped
   nested credential block) from every project read, not just from
   `create_connection`'s output as originally scoped — the credential leak
   surface on this platform is broader than just connections.

**Implication for tool design, updated**: `create_project` on SaaS
genuinely cannot be a "just works" call without a real, pre-existing COS
instance CRN + a fresh unique bucket name — there's no client-side
shortcut analogous to CPD's arbitrary-guid trick. This server should
require `storage` explicitly on SaaS sessions (no auto-fill, unlike CPD)
and document to the calling model that it needs a real COS instance CRN
from the caller's IBM Cloud account. Whether that COS instance is meant to
be created via this server's own `create_connection` tool, or is assumed
to already exist as IBM Cloud account infrastructure outside this server's
scope entirely, is still an open design question — but the connection
between `create_connection` and `create_project`'s storage wasn't
confirmed live (no time to test COS bucket auto-creation vs. requiring a
pre-existing bucket).

## Other confirmed `create_project` payload fields

From the same reference test suite — beyond what the earlier live read-only testing
showed on the `entity`:

- `public` (boolean) — settable at creation, not just present in output.
- `type` (string, e.g. `"cpd"`) — **settable at creation**, and is the same
  field `list_projects`'s `type` filter matches against (ties the two
  together — see `02-tools.md`). This server's `create_project` should
  expose `type` as an explicit input, defaulting sensibly per environment
  (`cpd` on a `cpd` session; TBD what SaaS defaults to / whether it's
  required there).
- `generator` — confirmed free-text, real observed values include
  `CPDCTL-CLI` and `wxd-portal-projects`. Purely descriptive/attribution, not an enum CCS validates
  against (inference — not contradicted by anything seen, but not proven
  either).

## Response shape on create is not fully pinned down

A reference test suite defensively checks, in order, for:
1. A `location` field (a URL string; extract the trailing path segment as the id)
2. `metadata.guid`
3. top-level `guid`
4. top-level `id`

and accepts either `201 Created` or `200 OK` as success. This server's
`create_*` tools should do the same defensive extraction rather than
assuming one shape — `metadata.guid` is confirmed as *a* valid shape (it's
what `GET`/`list` return, per the read-only pass), but evidently create
responses aren't guaranteed to look identical to read responses, and may
differ again between CPD and SaaS or across resource types. Treat this as
a general pattern for every `create_*` tool's response parsing, not just
`create_project`.
