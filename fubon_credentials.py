"""Load Fubon credentials without putting secrets in source control."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


API_TARGET = "FUBON_API_WUDE"
CERT_TARGET = "FUBON_CERT_WUDE"


class CredentialLike(Protocol):
    username: str | None
    password: str | None


@dataclass(frozen=True)
class FubonCredentials:
    personal_id: str
    api_key: str
    cert_path: Path
    cert_password: str


def _fixed_cert_path(environ: Mapping[str, str]) -> Path:
    configured = environ.get("FUBON_CERT_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()

    local_app_data = environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise RuntimeError("找不到 LOCALAPPDATA，無法定位本機富邦憑證")
    return Path(local_app_data) / "WudeAI" / "cert" / "fubon_cert.p12"


def load_fubon_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    get_credential: Callable[[str, str | None], CredentialLike | None] | None = None,
) -> FubonCredentials:
    """Read Fubon secrets from env or Windows Credential Manager.

    Environment variables remain supported for backwards compatibility. On the
    owner's Windows computer, Windows Credential Manager is the preferred path.
    Secret values are never printed or written to disk by this module.
    """

    env = os.environ if environ is None else environ
    cert_path = _fixed_cert_path(env)

    personal_id = env.get("FUBON_ID", "").strip()
    api_key = env.get("FUBON_API_KEY", "").strip()
    cert_password = env.get("FUBON_CERT_PASSWORD", "")

    if not (personal_id and api_key):
        if get_credential is None:
            try:
                import keyring
            except ImportError as exc:
                raise RuntimeError("缺少 keyring；請先執行 setup_fubon_windows.ps1") from exc
            get_credential = keyring.get_credential

        api_credential = get_credential(API_TARGET, None)
        cert_credential = get_credential(CERT_TARGET, None)
        if api_credential and api_credential.password:
            api_key = api_credential.password
        if cert_credential:
            personal_id = (cert_credential.username or "").strip()
            cert_password = cert_credential.password or ""

    missing = []
    if not personal_id:
        missing.append(CERT_TARGET)
    if not api_key:
        missing.append(API_TARGET)
    if missing:
        raise RuntimeError("Windows 認證尚未設定完整：" + "、".join(missing))
    if not cert_path.is_file():
        raise RuntimeError(f"找不到富邦憑證：{cert_path}")

    return FubonCredentials(
        personal_id=personal_id,
        api_key=api_key,
        cert_path=cert_path,
        cert_password=cert_password,
    )
