"""SaaS region/stage host resolution — see spec/01-auth-and-environments.md.

CPD has no table to resolve: the caller always supplies the full base URL
directly, since it's an arbitrary customer-specific route with no derivable
pattern.
"""

from __future__ import annotations

from .errors import PlatformError

DEFAULT_REGION = "us-south"
DEFAULT_STAGE = "prod"

# Confirmed table, spec/01-auth-and-environments.md. Note au-syd/ca-tor use a
# different second-level domain (dai.) than the rest (dataplatform.).
SAAS_PROD_REGION_HOSTS = {
    "us-south": "api.dataplatform.cloud.ibm.com",
    "eu-de": "api.eu-de.dataplatform.cloud.ibm.com",
    "eu-gb": "api.eu-gb.dataplatform.cloud.ibm.com",
    "jp-tok": "api.jp-tok.dataplatform.cloud.ibm.com",
    "au-syd": "api.au-syd.dai.cloud.ibm.com",
    "ca-tor": "api.ca-tor.dai.cloud.ibm.com",
}

# Non-production stages are not regionalized in the spec — only a single
# dataplatform host per stage is documented.
#   - dev: host independently confirmed live.
#   - test: host is INFERRED from the dev pattern, NOT independently verified
#     live. See spec/00-overview.md "Open assumptions to confirm during
#     implementation" and spec/01-auth-and-environments.md.
STAGE_DATAPLATFORM_HOSTS = {
    "dev": "api.dataplatform.dev.cloud.ibm.com",
    # TODO(spec/01-auth-and-environments.md, unverified): confirm this host live.
    "test": "api.dataplatform.test.cloud.ibm.com",
}

# The dev/test IAM-authority pairing is confirmed non-obvious: dev's
# dataplatform host pairs with test's IAM authority, and test's dataplatform
# host pairs with prod's IAM authority. Given directly by the environment
# operator, not derived — see spec/01-auth-and-environments.md.
STAGE_IAM_TOKEN_URLS = {
    "prod": "https://iam.cloud.ibm.com/identity/token",
    "dev": "https://iam.test.cloud.ibm.com/identity/token",
    "test": "https://iam.cloud.ibm.com/identity/token",
}


def resolve_saas(region: str | None, stage: str = DEFAULT_STAGE) -> tuple[str, str]:
    """Resolve a SaaS (region, stage) pair to (base_url, iam_token_url).

    `stage` is a third environment-resolution axis documented in
    spec/01-auth-and-environments.md but not present in `authenticate`'s
    literal 02-tools.md signature (which predates that finding) — this server
    adds it as an optional `authenticate` input, defaulting to "prod" so
    existing (region-only) callers are unaffected.
    """
    stage = stage or DEFAULT_STAGE
    if stage not in STAGE_IAM_TOKEN_URLS:
        raise PlatformError(
            "invalid_request",
            f"Unknown SaaS stage '{stage}'. Expected one of: {', '.join(STAGE_IAM_TOKEN_URLS)}.",
            False,
        )

    if stage == "prod":
        region = region or DEFAULT_REGION
        if region not in SAAS_PROD_REGION_HOSTS:
            raise PlatformError(
                "invalid_request",
                f"Unknown SaaS region '{region}'. Expected one of: {', '.join(SAAS_PROD_REGION_HOSTS)}.",
                False,
            )
        host = SAAS_PROD_REGION_HOSTS[region]
    else:
        # dev/test are not regionalized in the spec — a single fixed host per stage.
        host = STAGE_DATAPLATFORM_HOSTS[stage]

    iam_token_url = STAGE_IAM_TOKEN_URLS[stage]
    return f"https://{host}", iam_token_url
