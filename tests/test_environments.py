"""SaaS region/stage resolution — spec/01-auth-and-environments.md."""

from __future__ import annotations

import pytest

from watsonx_platform_mcp.environments import resolve_saas
from watsonx_platform_mcp.errors import PlatformError


@pytest.mark.parametrize(
    "region,expected_host",
    [
        ("us-south", "api.dataplatform.cloud.ibm.com"),
        ("eu-de", "api.eu-de.dataplatform.cloud.ibm.com"),
        ("eu-gb", "api.eu-gb.dataplatform.cloud.ibm.com"),
        ("jp-tok", "api.jp-tok.dataplatform.cloud.ibm.com"),
        ("au-syd", "api.au-syd.dai.cloud.ibm.com"),  # different second-level domain
        ("ca-tor", "api.ca-tor.dai.cloud.ibm.com"),  # different second-level domain
    ],
)
def test_prod_region_table(region, expected_host):
    base_url, iam_url = resolve_saas(region, stage="prod")
    assert base_url == f"https://{expected_host}"
    assert iam_url == "https://iam.cloud.ibm.com/identity/token"


def test_default_region_is_us_south():
    base_url, _ = resolve_saas(None, stage="prod")
    assert base_url == "https://api.dataplatform.cloud.ibm.com"


def test_dev_stage_uses_test_iam_authority():
    """Confirmed live, non-obvious pairing: dev's dataplatform host pairs with
    test's IAM authority, not dev's own."""
    base_url, iam_url = resolve_saas(None, stage="dev")
    assert base_url == "https://api.dataplatform.dev.cloud.ibm.com"
    assert iam_url == "https://iam.test.cloud.ibm.com/identity/token"


def test_test_stage_uses_prod_iam_authority():
    """Confirmed live, non-obvious pairing: test's dataplatform host pairs with
    prod's IAM authority."""
    base_url, iam_url = resolve_saas(None, stage="test")
    assert iam_url == "https://iam.cloud.ibm.com/identity/token"


def test_invalid_region_raises():
    with pytest.raises(PlatformError) as exc_info:
        resolve_saas("mars-north", stage="prod")
    assert exc_info.value.code == "invalid_request"


def test_invalid_stage_raises():
    with pytest.raises(PlatformError) as exc_info:
        resolve_saas("us-south", stage="staging")
    assert exc_info.value.code == "invalid_request"
