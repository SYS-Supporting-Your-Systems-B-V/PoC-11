import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("mock_notification_receiver.app")


class Settings(BaseSettings):
    host: str = Field("0.0.0.0", validation_alias="MOCK_RECEIVER_HOST")
    port: int = Field(8002, validation_alias="MOCK_RECEIVER_PORT")
    public_root: str = Field(
        "https://mach2.disyepd.com/receiver-mock",
        validation_alias="MOCK_RECEIVER_PUBLIC_ROOT",
    )
    public_base: str = Field(
        "https://mach2.disyepd.com/receiver-mock/fhir",
        validation_alias="MOCK_RECEIVER_PUBLIC_BASE",
    )
    default_task_status: str = Field("requested", validation_alias="MOCK_RECEIVER_DEFAULT_TASK_STATUS")
    require_bearer_token: bool = Field(True, validation_alias="MOCK_RECEIVER_REQUIRE_BEARER_TOKEN")
    nuts_internal_base: str = Field("http://nuts-node:8083", validation_alias="MOCK_RECEIVER_NUTS_INTERNAL_BASE")
    introspection_timeout: float = Field(10.0, validation_alias="MOCK_RECEIVER_INTROSPECTION_TIMEOUT")
    required_incoming_scope: str = Field("eOverdracht-receiver", validation_alias="MOCK_RECEIVER_REQUIRED_INCOMING_SCOPE")
    outbound_verify_tls: bool = Field(False, validation_alias="MOCK_RECEIVER_OUTBOUND_VERIFY_TLS")
    outbound_ca_certs_file: Optional[str] = Field(None, validation_alias="MOCK_RECEIVER_OUTBOUND_CA_CERTS_FILE")
    directory_fhir_base: str = Field(
        "http://hapi-update-client:8081/fhir",
        validation_alias="MOCK_RECEIVER_DIRECTORY_FHIR_BASE",
    )
    receiver_organization_ura: str = Field("87654321", validation_alias="MOCK_RECEIVER_ORGANIZATION_URA")
    receiver_nuts_subject_id: Optional[str] = Field(None, validation_alias="MOCK_RECEIVER_NUTS_SUBJECT_ID")
    sender_data_scope: str = Field("eOverdracht-sender", validation_alias="MOCK_RECEIVER_SENDER_DATA_SCOPE")
    sender_token_timeout: float = Field(10.0, validation_alias="MOCK_RECEIVER_SENDER_TOKEN_TIMEOUT")
    session_cookie_name: str = Field("mock_receiver_session", validation_alias="MOCK_RECEIVER_SESSION_COOKIE_NAME")
    session_cookie_secure: bool = Field(True, validation_alias="MOCK_RECEIVER_SESSION_COOKIE_SECURE")
    dezi_well_known_url: str = Field(
        "https://acceptatie.auth.dezi.nl/.well-known/openid-configuration",
        validation_alias="MOCK_RECEIVER_DEZI_WELL_KNOWN_URL",
    )
    dezi_client_id: Optional[str] = Field(None, validation_alias="MOCK_RECEIVER_DEZI_CLIENT_ID")
    dezi_scope: str = Field("openid", validation_alias="MOCK_RECEIVER_DEZI_SCOPE")
    dezi_callback_path: str = Field("auth/dezi/callback", validation_alias="MOCK_RECEIVER_DEZI_CALLBACK_PATH")
    dezi_timeout: float = Field(20.0, validation_alias="MOCK_RECEIVER_DEZI_TIMEOUT")
    dezi_verify_tls: bool = Field(True, validation_alias="MOCK_RECEIVER_DEZI_VERIFY_TLS")
    dezi_ca_certs_file: Optional[str] = Field(None, validation_alias="MOCK_RECEIVER_DEZI_CA_CERTS_FILE")
    dezi_client_assertion_audience: Optional[str] = Field(
        None,
        validation_alias="MOCK_RECEIVER_DEZI_CLIENT_ASSERTION_AUDIENCE",
    )
    dezi_certificate_file: str = Field(
        "certificates/certificaat_SYS_DEZI.crt",
        validation_alias="MOCK_RECEIVER_DEZI_CERTIFICATE_FILE",
    )
    dezi_private_key_file: str = Field(
        "certificates/sleutel_SYS_DEZI.key",
        validation_alias="MOCK_RECEIVER_DEZI_PRIVATE_KEY_FILE",
    )
    log_level: str = Field("INFO", validation_alias="MOCK_RECEIVER_LOG_LEVEL")
    model_config = SettingsConfigDict(case_sensitive=False)


def _load_dotenv_once() -> None:
    try:
        from dotenv import dotenv_values

        dotenv_path = (Path(__file__).parent / ".env").resolve()
        if not dotenv_path.exists():
            logger.info("[mock-notification-receiver] .env not found file=%s", str(dotenv_path))
            return

        for key, value in dotenv_values(str(dotenv_path)).items():
            if value is not None and key not in os.environ:
                os.environ[key] = value
        logger.info("[mock-notification-receiver] .env loaded file=%s", str(dotenv_path))
    except Exception:
        logger.exception("[mock-notification-receiver] .env load failed")


_load_dotenv_once()
settings = Settings()


__all__ = ["settings"]
