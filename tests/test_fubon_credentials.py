from pathlib import Path
from types import SimpleNamespace

import pytest

from fubon_credentials import load_fubon_credentials


def test_loads_windows_credentials_from_keyring(tmp_path):
    cert = tmp_path / "WudeAI" / "cert" / "fubon_cert.p12"
    cert.parent.mkdir(parents=True)
    cert.write_bytes(b"test certificate placeholder")
    values = {
        "FUBON_API_WUDE": SimpleNamespace(username="label", password="api-secret"),
        "FUBON_CERT_WUDE": SimpleNamespace(username="owner-id", password="12345678"),
    }

    result = load_fubon_credentials(
        environ={"LOCALAPPDATA": str(tmp_path)},
        get_credential=lambda target, _username: values.get(target),
    )

    assert result.personal_id == "owner-id"
    assert result.api_key == "api-secret"
    assert result.cert_path == cert
    assert result.cert_password == "12345678"


def test_missing_credential_error_never_contains_secret(tmp_path):
    cert = tmp_path / "WudeAI" / "cert" / "fubon_cert.p12"
    cert.parent.mkdir(parents=True)
    cert.write_bytes(b"placeholder")
    secret = "do-not-print-this-secret"

    with pytest.raises(RuntimeError) as exc:
        load_fubon_credentials(
            environ={"LOCALAPPDATA": str(tmp_path)},
            get_credential=lambda *_args: None,
        )

    assert secret not in str(exc.value)
    assert "FUBON_API_WUDE" in str(exc.value)


def test_environment_fallback_remains_supported(tmp_path):
    cert = tmp_path / "cert.p12"
    cert.write_bytes(b"placeholder")
    result = load_fubon_credentials(
        environ={
            "FUBON_ID": "owner-id",
            "FUBON_API_KEY": "api-secret",
            "FUBON_CERT_PATH": str(cert),
            "FUBON_CERT_PASSWORD": "12345678",
        },
        get_credential=lambda *_args: None,
    )
    assert result.cert_path == Path(cert)
