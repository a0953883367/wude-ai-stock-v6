import pytest

from github_oidc_auth import validate_claims


def valid_claims():
    return {
        "repository": "a0953883367/wude-ai-stock-v6",
        "ref": "refs/heads/main",
        "workflow_ref": (
            "a0953883367/wude-ai-stock-v6/"
            ".github/workflows/stock-briefing.yml@refs/heads/main"
        ),
        "event_name": "schedule",
    }


def test_oidc_claims_allow_only_the_stock_report_workflow():
    assert validate_claims(valid_claims())["event_name"] == "schedule"


@pytest.mark.parametrize("field,value", [
    ("repository", "attacker/repo"),
    ("ref", "refs/heads/feature"),
    ("event_name", "pull_request"),
])
def test_oidc_claims_reject_untrusted_context(field, value):
    claims = valid_claims()
    claims[field] = value
    with pytest.raises(PermissionError):
        validate_claims(claims)
