# configs.py
import json
import logging
import os
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("sender_bgz_gateway.app")


class Settings(BaseSettings):
    is_production: bool = Field(False, validation_alias="BGZ_GATEWAY_IS_PRODUCTION")
    host: str = Field("0.0.0.0", validation_alias="BGZ_GATEWAY_HOST")
    port: int = Field(8001, validation_alias="BGZ_GATEWAY_PORT")
    upstream_fhir_base: str = Field(
        "http://hapi-notifiedpull-stu3:8082/fhir",
        validation_alias="BGZ_GATEWAY_UPSTREAM_FHIR_BASE",
    )
    nuts_internal_base: str = Field(
        "http://nuts-node:8083",
        validation_alias="BGZ_GATEWAY_NUTS_INTERNAL_BASE",
    )
    patient_identifier_system: str = Field(
        "http://fhir.nl/fhir/NamingSystem/bsn",
        validation_alias="BGZ_GATEWAY_PATIENT_IDENTIFIER_SYSTEM",
    )
    medical_role_valueset_url: str = Field(
        "https://decor.nictiz.nl/pub/eoverdracht/e-overdracht-html-20120928T120000/vs-2.16.840.1.113883.2.4.15.111.html",
        validation_alias="BGZ_GATEWAY_MEDICAL_ROLE_VALUESET_URL",
    )
    medical_role_codes: Optional[str] = Field(None, validation_alias="BGZ_GATEWAY_MEDICAL_ROLE_CODES")
    required_scopes: Optional[str] = Field(None, validation_alias="BGZ_GATEWAY_REQUIRED_SCOPES")
    upstream_timeout: float = Field(20.0, validation_alias="BGZ_GATEWAY_UPSTREAM_TIMEOUT")
    introspection_timeout: float = Field(10.0, validation_alias="BGZ_GATEWAY_INTROSPECTION_TIMEOUT")
    verify_tls: bool = Field(True, validation_alias="BGZ_GATEWAY_VERIFY_TLS")
    ca_certs_file: Optional[str] = Field(None, validation_alias="BGZ_GATEWAY_CA_CERTS_FILE")
    log_level: str = Field("INFO", validation_alias="BGZ_GATEWAY_LOG_LEVEL")
    log_sensitive_data: Optional[bool] = Field(None, validation_alias="BGZ_GATEWAY_LOG_SENSITIVE_DATA")
    log_preview_chars: int = Field(2000, validation_alias="BGZ_GATEWAY_LOG_PREVIEW_CHARS")
    model_config = SettingsConfigDict(case_sensitive=False)


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _load_dotenv_once() -> None:
    try:
        from dotenv import dotenv_values

        dotenv_path = (Path(__file__).parent / ".env").resolve()
        if not dotenv_path.exists():
            logger.info("[sender-bgz-gateway] .env not found file=%s", str(dotenv_path))
            return

        file_values = dotenv_values(str(dotenv_path))
        override = not _is_truthy(os.getenv("BGZ_GATEWAY_IS_PRODUCTION") or file_values.get("BGZ_GATEWAY_IS_PRODUCTION"))
        for key, value in file_values.items():
            if value is None:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
        logger.info("[sender-bgz-gateway] .env loaded file=%s override=%s", str(dotenv_path), "on" if override else "off")
    except Exception:
        logger.exception("[sender-bgz-gateway] .env load failed")


def _parse_csv_list(value: Any, *, default: List[str]) -> List[str]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    raw = str(value).strip()
    if not raw:
        return list(default)
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
    return [v.strip() for v in raw.split(",") if v.strip()]


_load_dotenv_once()
settings = Settings()
if settings.log_sensitive_data is None:
    settings.log_sensitive_data = not settings.is_production
MEDICAL_ROLE_CODES = _parse_csv_list(settings.medical_role_codes, default=[])
REQUIRED_SCOPES = _parse_csv_list(settings.required_scopes, default=[])


__all__ = ["settings", "MEDICAL_ROLE_CODES", "REQUIRED_SCOPES"]
