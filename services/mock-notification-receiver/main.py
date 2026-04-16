from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import secrets
import ssl
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwcrypto import jwe, jwk, jwt

from configs import settings

logging.basicConfig(level=getattr(logging, str(settings.log_level or "INFO").upper(), logging.INFO))
logger = logging.getLogger("mock_notification_receiver.app")

FHIR_JSON_CONTENT_TYPE = "application/fhir+json"
FHIR_JSON_CONTENT_TYPES = (
    "application/fhir+json",
    "application/json+fhir",
    "application/json",
)
SESSION_COOKIE_DEFAULT_PATH = "/"
SESSION_EXPIRY_SECONDS = 8 * 60 * 60
TASK_EXT_SENDER_BGZ_BASE_URL = "http://example.org/fhir/StructureDefinition/sender-bgz-base"
SENDER_AUTHORIZATION_BASE_HEADER = "X-Authorization-Base"
URA_IDENTIFIER_SYSTEM = "http://fhir.nl/fhir/NamingSystem/ura"
BGZ_SERVER_CAPABILITY_CODE = "http://nictiz.nl/fhir/CapabilityStatement/bgz2017-servercapabilities"
NUTS_OAUTH_CAPABILITY_CODE = "Nuts-OAuth"
UI_HTML_PATH = Path(__file__).with_name("ui.html")
OIDC_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
SENDER_PULL_PATHS = (
    ("workflow_task", "workflow_task"),
    ("patient", "Patient"),
    ("observations_lastn", "Observation/$lastn"),
    ("conditions", "Condition"),
    ("allergies", "AllergyIntolerance"),
    ("medications", "MedicationStatement"),
    ("documents", "DocumentReference"),
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _fhir_json_response(payload: dict[str, Any], *, status_code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    out_headers = dict(headers or {})
    out_headers["Content-Type"] = FHIR_JSON_CONTENT_TYPE
    return JSONResponse(content=payload, status_code=status_code, headers=out_headers)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        return [part for part in raw.replace(",", " ").split() if part]
    raw = str(value).strip()
    return [raw] if raw else []


def _read_path(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for segment in path:
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _first_non_empty(data: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        cur = _read_path(data, path)
        if cur in (None, "", [], {}):
            continue
        return cur
    return None


def _raise_http(http_status_code: int, reason: str, message: str, **extra: Any) -> None:
    detail: dict[str, Any] = {"reason": reason, "message": message}
    for key, value in extra.items():
        if value is not None:
            detail[key] = value
    raise HTTPException(status_code=http_status_code, detail=detail)


def _portal_basic_auth_credentials() -> tuple[str, str]:
    username = str(settings.portal_basic_auth_username or "").strip()
    password = str(settings.portal_basic_auth_password or "")
    return username, password


def _portal_basic_auth_realm() -> str:
    return str(settings.portal_basic_auth_realm or "").strip() or "Mock Receiver Portal"


def _portal_basic_auth_exception(reason: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"reason": reason, "message": message},
        headers={"WWW-Authenticate": f'Basic realm="{_portal_basic_auth_realm()}"'},
    )


def _require_portal_basic_auth(request: Request) -> None:
    expected_username, expected_password = _portal_basic_auth_credentials()
    if not expected_username and not expected_password:
        return
    if not expected_username or not expected_password:
        _raise_http(500, "portal_auth_misconfigured", "Portal-auth is onvolledig geconfigureerd.")

    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("basic "):
        raise _portal_basic_auth_exception(
            "portal_auth_required",
            "Receiver portal authenticatie is vereist.",
        )

    encoded = auth_header.split(" ", 1)[1].strip()
    if not encoded:
        raise _portal_basic_auth_exception(
            "portal_auth_required",
            "Receiver portal authenticatie is vereist.",
        )

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        raise _portal_basic_auth_exception(
            "portal_auth_invalid",
            "Receiver portal authenticatie is ongeldig.",
        )

    provided_username, sep, provided_password = decoded.partition(":")
    if not sep:
        raise _portal_basic_auth_exception(
            "portal_auth_invalid",
            "Receiver portal authenticatie is ongeldig.",
        )

    username_ok = secrets.compare_digest(provided_username, expected_username)
    password_ok = secrets.compare_digest(provided_password, expected_password)
    if not username_ok or not password_ok:
        raise _portal_basic_auth_exception(
            "portal_auth_invalid",
            "Receiver portal authenticatie is ongeldig.",
        )


def _join_url(base: str, path: str) -> str:
    return f"{str(base or '').strip().rstrip('/')}/{str(path or '').lstrip('/')}"


def _normalize_reference(value: Any) -> str:
    ref = str(value or "").strip()
    if not ref or ref.startswith("#"):
        return ""
    parts = [part for part in ref.split("/") if part]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[0] if parts else ""


def _normalize_base(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _url_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return _normalize_base(value)


def _split_ref(ref: str) -> tuple[str, str]:
    normalized = _normalize_reference(ref)
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", ""


def _is_json_content_type(content_type: str) -> bool:
    lowered = str(content_type or "").lower()
    return any(marker in lowered for marker in FHIR_JSON_CONTENT_TYPES)


def _verify_arg(verify_tls: bool, ca_certs_file: str | None) -> bool | str:
    if not verify_tls:
        return False
    if ca_certs_file:
        return ca_certs_file
    return True


def _resolve_service_path(raw_path: str) -> Path:
    path = Path(str(raw_path or "").strip())
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()
    return path


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _code_challenge(code_verifier: str) -> str:
    return _base64url(hashlib.sha256(code_verifier.encode("ascii")).digest())


def _jwt_segment_payload(token: str) -> dict[str, Any]:
    parts = [part for part in str(token or "").split(".") if part]
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _ui_html() -> str:
    try:
        return UI_HTML_PATH.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to load UI HTML from %s", str(UI_HTML_PATH))
        return "<!DOCTYPE html><html><body><h1>Mock Notification Receiver</h1><p>UI kon niet geladen worden.</p></body></html>"


def _bundle_resources(bundle: dict[str, Any], resource_type: str | None = None) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for entry in bundle.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            continue
        if resource_type and str(resource.get("resourceType") or "") != resource_type:
            continue
        resources.append(resource)
    return resources


def _pick_active_endpoint(endpoints: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for endpoint in endpoints:
        if str(endpoint.get("status") or "").strip().lower() == "active" and str(endpoint.get("address") or "").strip():
            return endpoint
    for endpoint in endpoints:
        if str(endpoint.get("address") or "").strip():
            return endpoint
    return endpoints[0] if endpoints else None


def _endpoint_matches_capability(endpoint: dict[str, Any], code: str) -> bool:
    expected = str(code or "").strip()
    if not expected:
        return False
    for payload_type in endpoint.get("payloadType") or []:
        if not isinstance(payload_type, dict):
            continue
        item_code = str(payload_type.get("code") or "").strip()
        item_system = str(payload_type.get("system") or "").strip()
        token = f"{item_system}|{item_code}" if item_system and item_code else item_code
        if item_code == expected or token == expected:
            return True
        for coding in payload_type.get("coding") or []:
            if not isinstance(coding, dict):
                continue
            item_code = str(coding.get("code") or "").strip()
            item_system = str(coding.get("system") or "").strip()
            token = f"{item_system}|{item_code}" if item_system and item_code else item_code
            if item_code == expected or token == expected:
                return True
    return False


def _extract_task_input_value(task: dict[str, Any], code: str) -> Optional[Any]:
    for item in task.get("input") or []:
        if not isinstance(item, dict):
            continue
        coding_list = ((item.get("type") or {}).get("coding") or [])
        if any(isinstance(coding, dict) and str(coding.get("code") or "") == code for coding in coding_list):
            if "valueString" in item:
                return item.get("valueString")
            if "valueBoolean" in item:
                return item.get("valueBoolean")
    return None


def _extract_sender_bgz_base(task: dict[str, Any]) -> Optional[str]:
    for extension in task.get("extension") or []:
        if not isinstance(extension, dict):
            continue
        if str(extension.get("url") or "") == TASK_EXT_SENDER_BGZ_BASE_URL:
            value = str(extension.get("valueUrl") or "").strip()
            if value:
                return value
    return None


def _extract_task_sender_ura(task: dict[str, Any]) -> Optional[str]:
    requester = task.get("requester") or {}
    onbehalfof = requester.get("onBehalfOf") or {}
    identifier = onbehalfof.get("identifier") or {}
    value = str(identifier.get("value") or "").strip()
    return value or None


def _extract_task_owner_ura(task: dict[str, Any]) -> Optional[str]:
    owner = task.get("owner") or {}
    identifier = owner.get("identifier") or {}
    value = str(identifier.get("value") or "").strip()
    return value or None


def _sender_authorization_server_url(sender_oauth_endpoint: str, sender_ura: str) -> str:
    auth_server_base = _normalize_base(sender_oauth_endpoint)
    if not auth_server_base:
        return ""
    remote_subject_id = str(sender_ura or "").strip()
    if not remote_subject_id:
        return auth_server_base
    if "/oauth2/" in urlsplit(auth_server_base).path:
        return auth_server_base
    return f"{auth_server_base}/oauth2/{quote(remote_subject_id, safe='')}"


def _extract_task_patient_bsn(task: dict[str, Any]) -> Optional[str]:
    patient = task.get("for") or {}
    identifier = patient.get("identifier") or {}
    value = str(identifier.get("value") or "").strip()
    return value or None


def _extract_workflow_task_ref(task: dict[str, Any]) -> Optional[str]:
    based_on = task.get("basedOn") or []
    if not isinstance(based_on, list) or not based_on:
        return None
    value = str((based_on[0] or {}).get("reference") or "").strip()
    return value or None


def _extract_workflow_task_identifier(task: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    based_on = task.get("basedOn") or []
    if not isinstance(based_on, list) or not based_on:
        return None, None
    identifier = ((based_on[0] or {}).get("identifier") or {})
    system = str(identifier.get("system") or "").strip() or None
    value = str(identifier.get("value") or "").strip() or None
    return system, value


def _workflow_task_search_path(identifier_system: str, identifier_value: str) -> str:
    query = urlencode({"identifier": f"{identifier_system}|{identifier_value}"})
    return f"Task?{query}"


def _task_summary(task: dict[str, Any], *, received_at: str) -> dict[str, Any]:
    owner_identifier = ((task.get("owner") or {}).get("identifier") or {})
    patient_identifier = ((task.get("for") or {}).get("identifier") or {})
    based_on = task.get("basedOn") or []
    first_based_on = based_on[0] if isinstance(based_on, list) and based_on else {}
    based_on_identifier_system, based_on_identifier_value = _extract_workflow_task_identifier(task)
    return {
        "id": str(task.get("id") or "").strip(),
        "status": str(task.get("status") or "").strip() or None,
        "based_on": based_on_identifier_value or str((first_based_on or {}).get("reference") or "").strip() or None,
        "based_on_identifier_system": based_on_identifier_system,
        "based_on_identifier_value": based_on_identifier_value,
        "based_on_reference": str((first_based_on or {}).get("reference") or "").strip() or None,
        "authorization_base": _extract_task_input_value(task, "authorization-base"),
        "get_workflow_task": _extract_task_input_value(task, "get-workflow-task"),
        "sender_bgz_base": _extract_sender_bgz_base(task),
        "owner_ura": str(owner_identifier.get("value") or "").strip() or None,
        "patient_bsn": str(patient_identifier.get("value") or "").strip() or None,
        "sender_ura": _extract_task_sender_ura(task),
        "received_at": received_at,
    }


def _capability_statement() -> dict[str, Any]:
    public_base = _normalize_base(settings.public_base)
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": _now_iso(),
        "kind": "instance",
        "fhirVersion": "3.0.2",
        "format": ["json"],
        "software": {
            "name": "Mock Notification Receiver",
            "version": "0.2.0",
        },
        "implementation": {
            "description": "Receiver mock with DEZI login and simple operator portal",
            "url": public_base,
        },
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "Task",
                        "interaction": [
                            {"code": "create"},
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                    }
                ],
            }
        ],
    }


@dataclass
class TokenContext:
    raw: dict[str, Any]
    active: bool
    organization_ura: str
    scopes: list[str]
    subject_id: str = ""


def _extract_oauth_subject_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = urlsplit(raw).path
    except Exception:
        path = raw
    parts = [part for part in str(path or "").split("/") if part]
    for index, part in enumerate(parts):
        if part == "oauth2" and index + 1 < len(parts):
            return str(parts[index + 1] or "").strip()
    return ""


def _extract_token_context(data: dict[str, Any]) -> TokenContext:
    return TokenContext(
        raw=dict(data or {}),
        active=bool(data.get("active") is True or _truthy(data.get("active"))),
        organization_ura=str(
            _first_non_empty(
                data,
                ("organization_ura",),
                ("claims", "organization_ura"),
                ("subject_organization_id",),
                ("subject", "properties", "subject_organization_id"),
                ("organization", "ura"),
            )
            or ""
        ).strip(),
        subject_id=_extract_oauth_subject_id(
            _first_non_empty(
                data,
                ("client_id",),
                ("iss",),
            )
        ),
        scopes=_string_list(
            _first_non_empty(
                data,
                ("scope",),
                ("claims", "scope"),
                ("client_qualifications",),
                ("subject", "properties", "client_qualifications"),
            )
        ),
    )


async def _introspect_access_token_payload(token: str) -> dict[str, Any]:
    url = _join_url(settings.nuts_internal_base, "/internal/auth/v2/accesstoken/introspect")
    try:
        response = await app.state.http_client.post(
            url,
            data={"token": token},
            headers={"Accept": "application/json"},
            timeout=settings.introspection_timeout,
        )
    except httpx.HTTPError as exc:
        logger.exception("Notification token introspection failed")
        _raise_http(502, "introspection_failed", "Nuts token introspection faalde.", error=str(exc))

    if response.status_code >= 400:
        _raise_http(
            502,
            "introspection_failed",
            "Nuts token introspection faalde.",
            status_code=response.status_code,
            upstream_body=str(getattr(response, "text", "") or "")[:500] or None,
        )
    try:
        payload = response.json()
    except Exception as exc:
        logger.exception("Notification token introspection did not return JSON")
        _raise_http(502, "introspection_invalid_json", "Nuts token introspection gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(payload, dict):
        _raise_http(502, "introspection_invalid_json", "Nuts token introspection gaf geen JSON object terug.")
    return payload


async def _introspect_token(token: str) -> TokenContext:
    payload = await _introspect_access_token_payload(token)

    ctx = _extract_token_context(payload)
    if not ctx.active:
        _raise_http(401, "inactive_token", "Toegangstoken is niet actief of niet geldig.")
    if not ctx.organization_ura:
        _raise_http(403, "missing_organization_ura", "Introspectie mist organization_ura.")

    required_scope = str(settings.required_incoming_scope or "").strip()
    if required_scope and required_scope not in set(ctx.scopes):
        _raise_http(
            403,
            "scope_not_allowed",
            "De aangeleverde scope voldoet niet aan de vereiste receiver-scope.",
            required_scope=required_scope,
            received_scopes=ctx.scopes,
        )
    return ctx


async def _authorize_notification_request(request: Request, task: dict[str, Any]) -> None:
    if not settings.require_bearer_token:
        return

    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        _raise_http(401, "missing_bearer_token", "Authorization header met Bearer token is verplicht.")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        _raise_http(401, "missing_bearer_token", "Bearer token ontbreekt.")

    token_ctx = await _introspect_token(token)
    _authorize_incoming_notification_policy(token_ctx=token_ctx, task=task)


def _authorize_incoming_notification_policy(*, token_ctx: TokenContext, task: dict[str, Any]) -> None:
    task_sender_ura = _extract_task_sender_ura(task)
    if not task_sender_ura:
        _raise_http(
            400,
            "missing_task_sender_ura",
            "Notification Task mist requester.onBehalfOf.identifier.value (sender URA).",
        )
    if token_ctx.organization_ura != task_sender_ura and token_ctx.subject_id != task_sender_ura:
        _raise_http(
            403,
            "organization_not_authorized",
            "organization_ura uit introspectie matcht niet met de sender URA in de notification Task.",
            token_organization_ura=token_ctx.organization_ura,
            token_subject_id=token_ctx.subject_id or None,
            task_sender_ura=task_sender_ura,
        )
    task_owner_ura = _extract_task_owner_ura(task)
    configured_receiver_ura = str(settings.receiver_organization_ura or "").strip()
    if not task_owner_ura:
        _raise_http(
            400,
            "missing_task_owner_ura",
            "Notification Task mist owner.identifier.value (receiver URA).",
        )
    if configured_receiver_ura and task_owner_ura != configured_receiver_ura:
        _raise_http(
            403,
            "receiver_not_authorized",
            "owner.identifier.value in de notification Task matcht niet met de receiver organisatie.",
            configured_receiver_ura=configured_receiver_ura,
            task_owner_ura=task_owner_ura,
        )


@dataclass
class StoredTask:
    resource: dict[str, Any]
    received_at: str


class TaskStore:
    def __init__(self):
        self._lock = Lock()
        self._tasks: dict[str, StoredTask] = {}
        self._order: list[str] = []

    def save(self, task: dict[str, Any]) -> StoredTask:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            raise ValueError("Task.id ontbreekt")
        stored = StoredTask(resource=copy.deepcopy(task), received_at=_now_iso())
        with self._lock:
            if task_id not in self._tasks:
                self._order.append(task_id)
            self._tasks[task_id] = stored
        return stored

    def get(self, task_id: str) -> Optional[StoredTask]:
        with self._lock:
            stored = self._tasks.get(task_id)
            if stored is None:
                return None
            return StoredTask(resource=copy.deepcopy(stored.resource), received_at=stored.received_at)

    def list(self) -> list[StoredTask]:
        with self._lock:
            return [
                StoredTask(resource=copy.deepcopy(self._tasks[task_id].resource), received_at=self._tasks[task_id].received_at)
                for task_id in self._order
                if task_id in self._tasks
            ]

    def latest(self) -> Optional[StoredTask]:
        with self._lock:
            if not self._order:
                return None
            task_id = self._order[-1]
            stored = self._tasks.get(task_id)
            if stored is None:
                return None
            return StoredTask(resource=copy.deepcopy(stored.resource), received_at=stored.received_at)

    def clear(self) -> int:
        with self._lock:
            count = len(self._tasks)
            self._tasks.clear()
            self._order.clear()
            return count


@dataclass
class UserSession:
    session_id: str
    created_at: str
    pending_state: Optional[str] = None
    pending_code_verifier: Optional[str] = None
    pending_nonce: Optional[str] = None
    pending_task_id: Optional[str] = None
    dezi_logged_in_at: Optional[str] = None
    dezi_identity: dict[str, Any] = field(default_factory=dict)
    dezi_claims: dict[str, Any] = field(default_factory=dict)
    dezi_token_metadata: dict[str, Any] = field(default_factory=dict)
    dezi_id_token: Optional[str] = None
    dezi_userinfo_jwt: Optional[str] = None


class SessionStore:
    def __init__(self):
        self._lock = Lock()
        self._sessions: dict[str, UserSession] = {}

    def create(self) -> UserSession:
        session = UserSession(session_id=uuid.uuid4().hex, created_at=_now_iso())
        return self.save(session)

    def save(self, session: UserSession) -> UserSession:
        with self._lock:
            self._prune_locked()
            self._sessions[session.session_id] = copy.deepcopy(session)
        return copy.deepcopy(session)

    def get(self, session_id: str | None) -> Optional[UserSession]:
        if not session_id:
            return None
        with self._lock:
            self._prune_locked()
            session = self._sessions.get(session_id)
            return copy.deepcopy(session) if session is not None else None

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def _prune_locked(self) -> None:
        now = _now_utc()
        expired: list[str] = []
        for session_id, session in self._sessions.items():
            try:
                created = datetime.fromisoformat(session.created_at)
            except Exception:
                expired.append(session_id)
                continue
            age = now - created
            if age.total_seconds() > SESSION_EXPIRY_SECONDS:
                expired.append(session_id)
        for session_id in expired:
            self._sessions.pop(session_id, None)


def _cookie_secure() -> bool:
    return bool(settings.session_cookie_secure)


def _session_cookie_kwargs() -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": _cookie_secure(),
        "samesite": "lax",
        "path": SESSION_COOKIE_DEFAULT_PATH,
        "max_age": SESSION_EXPIRY_SECONDS,
    }


def _load_session(request: Request, *, create: bool = False) -> tuple[Optional[UserSession], bool]:
    session_id = str(request.cookies.get(settings.session_cookie_name) or "").strip()
    session = app.state.session_store.get(session_id)
    created = False
    if session is None and create:
        session = app.state.session_store.create()
        created = True
    return session, created


def _persist_session(session: UserSession) -> UserSession:
    return app.state.session_store.save(session)


def _set_session_cookie(response: Response, session: UserSession) -> None:
    response.set_cookie(settings.session_cookie_name, session.session_id, **_session_cookie_kwargs())


def _delete_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path=SESSION_COOKIE_DEFAULT_PATH)


def _dezi_client_id() -> str:
    client_id = str(settings.dezi_client_id or "").strip() or str(settings.receiver_organization_ura or "").strip()
    if not client_id:
        _raise_http(500, "dezi_not_configured", "Geen DEZI client-id of receiver organisatie-URA geconfigureerd.")
    return client_id


def _dezi_callback_url() -> str:
    public_root = _normalize_base(settings.public_root)
    callback_path = str(settings.dezi_callback_path or "").strip() or "auth/dezi/callback"
    lowered = callback_path.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return _normalize_base(callback_path)
    if callback_path.startswith("/"):
        return _join_url(_url_origin(public_root), callback_path)
    return _join_url(public_root, callback_path)


async def _get_dezi_material() -> dict[str, Any]:
    cached = getattr(app.state, "dezi_material", None)
    if cached is not None:
        return cached

    cert_path = _resolve_service_path(settings.dezi_certificate_file)
    key_path = _resolve_service_path(settings.dezi_private_key_file)
    try:
        cert_pem = cert_path.read_bytes()
    except Exception as exc:
        _raise_http(500, "dezi_certificate_missing", "DEZI certificaat kon niet gelezen worden.", error=str(exc), path=str(cert_path))
    try:
        key_pem = key_path.read_bytes()
    except Exception as exc:
        _raise_http(500, "dezi_private_key_missing", "DEZI private key kon niet gelezen worden.", error=str(exc), path=str(key_path))

    try:
        cert_der = ssl.PEM_cert_to_DER_cert(cert_pem.decode("ascii"))
    except Exception as exc:
        _raise_http(500, "dezi_certificate_invalid", "DEZI certificaat is geen geldige PEM.", error=str(exc), path=str(cert_path))

    try:
        private_jwk = jwk.JWK.from_pem(key_pem)
    except Exception as exc:
        _raise_http(500, "dezi_private_key_invalid", "DEZI private key is ongeldig.", error=str(exc), path=str(key_path))

    material = {
        "certificate_path": str(cert_path),
        "private_key_path": str(key_path),
        "certificate_thumbprint_sha1": _base64url(hashlib.sha1(cert_der).digest()),
        "private_jwk": private_jwk,
    }
    app.state.dezi_material = material
    return material


async def _get_dezi_oidc_configuration() -> dict[str, Any]:
    cached = getattr(app.state, "dezi_oidc_configuration", None)
    fetched_at = float(getattr(app.state, "dezi_oidc_configuration_fetched_at", 0.0) or 0.0)
    if isinstance(cached, dict) and (time.time() - fetched_at) < 300:
        return cached

    url = str(settings.dezi_well_known_url or "").strip()
    if not url:
        _raise_http(500, "dezi_not_configured", "DEZI well-known configuratie ontbreekt.")
    try:
        response = await app.state.dezi_client.get(url, headers={"Accept": "application/json"}, timeout=settings.dezi_timeout)
    except httpx.HTTPError as exc:
        logger.exception("DEZI well-known lookup failed")
        _raise_http(502, "dezi_well_known_failed", "DEZI well-known configuratie kon niet worden opgehaald.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "dezi_well_known_failed",
            "DEZI well-known configuratie kon niet worden opgehaald.",
            status_code=response.status_code,
            upstream_body=response.text[:500],
        )
    try:
        payload = response.json()
    except Exception as exc:
        _raise_http(502, "dezi_well_known_invalid_json", "DEZI well-known endpoint gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(payload, dict):
        _raise_http(502, "dezi_well_known_invalid_json", "DEZI well-known endpoint gaf geen JSON object terug.")
    required_fields = ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri")
    missing = [field for field in required_fields if not str(payload.get(field) or "").strip()]
    if missing:
        _raise_http(502, "dezi_well_known_incomplete", "DEZI well-known configuratie mist verplichte velden.", missing_fields=missing)

    app.state.dezi_oidc_configuration = payload
    app.state.dezi_oidc_configuration_fetched_at = time.time()
    return payload


async def _get_dezi_jwks(jwks_uri: str) -> jwk.JWKSet:
    cache = getattr(app.state, "dezi_jwks_cache", {})
    cached = cache.get(jwks_uri)
    if isinstance(cached, tuple) and len(cached) == 2 and (time.time() - float(cached[1])) < 300:
        return cached[0]

    try:
        response = await app.state.dezi_client.get(jwks_uri, headers={"Accept": "application/json"}, timeout=settings.dezi_timeout)
    except httpx.HTTPError as exc:
        _raise_http(502, "dezi_jwks_failed", "DEZI JWKS kon niet worden opgehaald.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "dezi_jwks_failed",
            "DEZI JWKS kon niet worden opgehaald.",
            status_code=response.status_code,
            upstream_body=response.text[:500],
        )
    try:
        payload = response.json()
    except Exception as exc:
        _raise_http(502, "dezi_jwks_invalid_json", "DEZI JWKS gaf geen geldige JSON terug.", error=str(exc))
    try:
        key_set = jwk.JWKSet.from_json(json.dumps(payload))
    except Exception as exc:
        _raise_http(502, "dezi_jwks_invalid_json", "DEZI JWKS kon niet worden geparseerd.", error=str(exc))

    cache = dict(cache or {})
    cache[jwks_uri] = (key_set, time.time())
    app.state.dezi_jwks_cache = cache
    return key_set


async def _build_private_key_jwt(token_endpoint: str, oidc_config: dict[str, Any]) -> str:
    material = await _get_dezi_material()
    audience = str(settings.dezi_client_assertion_audience or "").strip()
    if not audience:
        audience = str(token_endpoint or "").strip() or str(oidc_config.get("issuer") or "").strip()
    now = int(time.time())
    claims = {
        "iss": _dezi_client_id(),
        "sub": _dezi_client_id(),
        "aud": audience,
        "iat": now,
        "exp": now + 300,
        "jti": uuid.uuid4().hex,
    }
    header = {
        "alg": "RS256",
        "typ": "JWT",
        "x5t": material["certificate_thumbprint_sha1"],
        "kid": material["certificate_thumbprint_sha1"],
    }
    token = jwt.JWT(header=header, claims=claims)
    token.make_signed_token(material["private_jwk"])
    return token.serialize()


async def _exchange_dezi_code(*, oidc_config: dict[str, Any], code: str, code_verifier: str) -> dict[str, Any]:
    token_endpoint = str(oidc_config.get("token_endpoint") or "").strip()
    if not token_endpoint:
        _raise_http(502, "dezi_well_known_incomplete", "DEZI token endpoint ontbreekt in de configuratie.")
    client_assertion = await _build_private_key_jwt(token_endpoint, oidc_config)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _dezi_callback_url(),
        "client_id": _dezi_client_id(),
        "code_verifier": code_verifier,
        "client_assertion_type": OIDC_CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion,
    }
    try:
        response = await app.state.dezi_client.post(
            token_endpoint,
            data=data,
            headers={"Accept": "application/json"},
            timeout=settings.dezi_timeout,
        )
    except httpx.HTTPError as exc:
        logger.exception("DEZI token exchange failed")
        _raise_http(502, "dezi_token_exchange_failed", "DEZI token exchange faalde.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "dezi_token_exchange_failed",
            "DEZI token exchange faalde.",
            status_code=response.status_code,
            upstream_body=response.text[:1000],
        )
    try:
        payload = response.json()
    except Exception as exc:
        _raise_http(502, "dezi_token_exchange_invalid_json", "DEZI token endpoint gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(payload, dict):
        _raise_http(502, "dezi_token_exchange_invalid_json", "DEZI token endpoint gaf geen JSON object terug.")
    if not str(payload.get("access_token") or "").strip():
        _raise_http(502, "dezi_token_exchange_invalid_json", "DEZI token endpoint gaf geen access_token terug.")
    return payload


async def _fetch_dezi_userinfo(*, oidc_config: dict[str, Any], access_token: str) -> str:
    userinfo_endpoint = str(oidc_config.get("userinfo_endpoint") or "").strip()
    if not userinfo_endpoint:
        _raise_http(502, "dezi_well_known_incomplete", "DEZI userinfo endpoint ontbreekt in de configuratie.")
    try:
        response = await app.state.dezi_client.get(
            userinfo_endpoint,
            headers={
                "Accept": "application/json, application/jwt, text/plain",
                "Authorization": f"Bearer {access_token}",
            },
            timeout=settings.dezi_timeout,
        )
    except httpx.HTTPError as exc:
        logger.exception("DEZI userinfo request failed")
        _raise_http(502, "dezi_userinfo_failed", "DEZI userinfo ophalen faalde.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "dezi_userinfo_failed",
            "DEZI userinfo ophalen faalde.",
            status_code=response.status_code,
            upstream_body=response.text[:1000],
        )
    raw = response.text.strip()
    if not raw:
        _raise_http(502, "dezi_userinfo_empty", "DEZI userinfo gaf een lege response terug.")
    return raw


def _validate_token_claims(claims: dict[str, Any], *, issuer: str | None, audience: str | None, nonce: str | None = None) -> None:
    now = int(time.time())
    if issuer:
        token_issuer = str(claims.get("iss") or "").strip()
        if token_issuer and token_issuer != issuer:
            _raise_http(502, "dezi_token_invalid", "DEZI token issuer is ongeldig.", expected_issuer=issuer, received_issuer=token_issuer)
    if audience:
        aud_value = claims.get("aud")
        audiences = aud_value if isinstance(aud_value, list) else [aud_value]
        audiences = [str(item).strip() for item in audiences if str(item or "").strip()]
        if audiences and audience not in audiences:
            _raise_http(502, "dezi_token_invalid", "DEZI token audience is ongeldig.", expected_audience=audience, received_audiences=audiences)
    if nonce:
        token_nonce = str(claims.get("nonce") or "").strip()
        if token_nonce and token_nonce != nonce:
            _raise_http(502, "dezi_token_invalid", "DEZI token nonce is ongeldig.", expected_nonce=nonce, received_nonce=token_nonce)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and int(exp) < now:
        _raise_http(502, "dezi_token_expired", "DEZI token is verlopen.")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and int(nbf) > now + 30:
        _raise_http(502, "dezi_token_not_yet_valid", "DEZI token is nog niet geldig.")


async def _verify_signed_jwt(token: str, *, oidc_config: dict[str, Any], nonce: str | None = None) -> dict[str, Any]:
    jwks_uri = str(oidc_config.get("jwks_uri") or "").strip()
    key_set = await _get_dezi_jwks(jwks_uri)
    try:
        signed = jwt.JWT(jwt=token, key=key_set)
    except Exception as exc:
        _raise_http(502, "dezi_signature_invalid", "DEZI signature validatie faalde.", error=str(exc))
    try:
        claims = json.loads(signed.claims)
    except Exception as exc:
        _raise_http(502, "dezi_claims_invalid", "DEZI claims konden niet worden geparseerd.", error=str(exc))
    if not isinstance(claims, dict):
        _raise_http(502, "dezi_claims_invalid", "DEZI claims zijn geen JSON object.")
    _validate_token_claims(
        claims,
        issuer=str(oidc_config.get("issuer") or "").strip() or None,
        audience=_dezi_client_id(),
        nonce=nonce,
    )
    return claims


async def _decrypt_dezi_userinfo(raw_userinfo: str, *, oidc_config: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
    stripped = str(raw_userinfo or "").strip()
    if not stripped:
        _raise_http(502, "dezi_userinfo_empty", "DEZI userinfo is leeg.")

    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except Exception as exc:
            _raise_http(502, "dezi_userinfo_invalid_json", "DEZI userinfo JSON kon niet worden geparseerd.", error=str(exc))
        if not isinstance(payload, dict):
            _raise_http(502, "dezi_userinfo_invalid_json", "DEZI userinfo JSON is geen object.")
        return None, payload

    if stripped.count(".") == 4:
        material = await _get_dezi_material()
        envelope = jwe.JWE()
        try:
            envelope.deserialize(stripped)
            envelope.decrypt(material["private_jwk"])
        except Exception as exc:
            _raise_http(502, "dezi_userinfo_decrypt_failed", "DEZI userinfo decryptie faalde.", error=str(exc))
        inner = envelope.payload.decode("utf-8")
    else:
        inner = stripped

    if inner.count(".") == 2:
        claims = await _verify_signed_jwt(inner, oidc_config=oidc_config)
        return inner, claims

    try:
        payload = json.loads(inner)
    except Exception as exc:
        _raise_http(502, "dezi_userinfo_invalid_json", "DEZI userinfo kon niet worden geparseerd.", error=str(exc))
    if not isinstance(payload, dict):
        _raise_http(502, "dezi_userinfo_invalid_json", "DEZI userinfo payload is geen object.")
    return None, payload


def _dezi_roles_from_claims(claims: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for relation in claims.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        roles.extend(_string_list(relation.get("roles")))
    seen: set[str] = set()
    result: list[str] = []
    for role in roles:
        if role in seen:
            continue
        seen.add(role)
        result.append(role)
    return result


def _dezi_identity_from_claims(claims: dict[str, Any]) -> dict[str, Any]:
    relations = claims.get("relations") or []
    selected_relation: dict[str, Any] | None = None
    receiver_ura = str(settings.receiver_organization_ura or "").strip()
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if str(relation.get("ura") or "").strip() == receiver_ura:
            selected_relation = relation
            break
    if selected_relation is None and relations:
        first = relations[0]
        if isinstance(first, dict):
            selected_relation = first

    initials = str(claims.get("initials") or "").strip()
    surname_prefix = str(claims.get("surname_prefix") or "").strip()
    surname = str(claims.get("surname") or "").strip()
    display_parts = [part for part in (initials, surname_prefix, surname) if part]
    display_name = " ".join(display_parts).strip() or None
    employee_identifier = str(
        claims.get("Dezi_id")
        or claims.get("dezi_id")
        or claims.get("uzi_id")
        or claims.get("uziNumber")
        or ""
    ).strip() or None

    return {
        "display_name": display_name,
        "employee_identifier": employee_identifier,
        "initials": initials or None,
        "surname_prefix": surname_prefix or None,
        "surname": surname or None,
        "organization_ura": str((selected_relation or {}).get("ura") or "").strip() or None,
        "organization_name": str((selected_relation or {}).get("entity_name") or "").strip() or None,
        "roles": _dezi_roles_from_claims(claims),
        "relations": relations if isinstance(relations, list) else [],
    }


def _session_token_metadata(token_payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("token_type", "scope", "expires_in", "refresh_expires_in"):
        value = token_payload.get(key)
        if value not in (None, "", []):
            out[key] = value
    return out


def _choose_dezi_id_token(token_payload: dict[str, Any], userinfo_jwt: str | None) -> Optional[str]:
    raw_id_token = str(token_payload.get("id_token") or "").strip()
    if raw_id_token:
        return raw_id_token
    if userinfo_jwt:
        return userinfo_jwt
    return None


async def _perform_dezi_login(session: UserSession, *, code: str) -> UserSession:
    oidc_config = await _get_dezi_oidc_configuration()
    token_payload = await _exchange_dezi_code(
        oidc_config=oidc_config,
        code=code,
        code_verifier=str(session.pending_code_verifier or ""),
    )
    userinfo_raw = await _fetch_dezi_userinfo(oidc_config=oidc_config, access_token=str(token_payload.get("access_token") or ""))
    userinfo_jwt, userinfo_claims = await _decrypt_dezi_userinfo(userinfo_raw, oidc_config=oidc_config)

    session.dezi_id_token = _choose_dezi_id_token(token_payload, userinfo_jwt)
    if not session.dezi_id_token:
        _raise_http(502, "dezi_id_token_missing", "DEZI login leverde geen bruikbare id_token op.")

    if str(token_payload.get("id_token") or "").strip():
        token_claims = _jwt_segment_payload(str(token_payload.get("id_token") or ""))
        if token_claims:
            _validate_token_claims(
                token_claims,
                issuer=str(oidc_config.get("issuer") or "").strip() or None,
                audience=_dezi_client_id(),
                nonce=str(session.pending_nonce or "").strip() or None,
            )

    session.dezi_logged_in_at = _now_iso()
    session.dezi_identity = _dezi_identity_from_claims(userinfo_claims)
    session.dezi_claims = copy.deepcopy(userinfo_claims)
    session.dezi_token_metadata = _session_token_metadata(token_payload)
    session.dezi_userinfo_jwt = userinfo_jwt
    session.pending_state = None
    session.pending_code_verifier = None
    session.pending_nonce = None
    return _persist_session(session)


async def _discover_sender_endpoints(sender_ura: str) -> dict[str, Any]:
    ura = str(sender_ura or "").strip()
    if not ura:
        _raise_http(400, "missing_sender_ura", "Notification Task mist de sender URA.")
    org_url = _join_url(settings.directory_fhir_base, "Organization")
    try:
        response = await app.state.http_client.get(
            org_url,
            params={"identifier": f"{URA_IDENTIFIER_SYSTEM}|{ura}", "_count": "5"},
            headers={"Accept": "application/fhir+json"},
        )
    except httpx.HTTPError as exc:
        _raise_http(502, "sender_directory_lookup_failed", "Directory lookup voor de sender organisatie faalde.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "sender_directory_lookup_failed",
            "Directory lookup voor de sender organisatie faalde.",
            status_code=response.status_code,
            upstream_body=response.text[:500],
        )
    try:
        bundle = response.json()
    except Exception as exc:
        _raise_http(502, "sender_directory_lookup_invalid_json", "Directory lookup gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(bundle, dict):
        _raise_http(502, "sender_directory_lookup_invalid_json", "Directory lookup gaf geen JSON object terug.")

    organizations = _bundle_resources(bundle, "Organization")
    if not organizations:
        _raise_http(404, "sender_not_found_in_directory", "Geen sender organisatie gevonden in de directory.", sender_ura=ura)
    organization = organizations[0]
    endpoint_ids: list[str] = []
    for endpoint_ref in organization.get("endpoint") or []:
        reference = endpoint_ref.get("reference") if isinstance(endpoint_ref, dict) else endpoint_ref
        _, endpoint_id = _split_ref(str(reference or ""))
        if endpoint_id:
            endpoint_ids.append(endpoint_id)
    if not endpoint_ids:
        _raise_http(404, "sender_endpoints_missing", "Sender organisatie heeft geen Endpoint referenties in de directory.", sender_ura=ura)

    endpoint_url = _join_url(settings.directory_fhir_base, "Endpoint")
    try:
        endpoint_response = await app.state.http_client.get(
            endpoint_url,
            params={"_id": ",".join(endpoint_ids), "_count": "200"},
            headers={"Accept": "application/fhir+json"},
        )
    except httpx.HTTPError as exc:
        _raise_http(502, "sender_directory_lookup_failed", "Directory lookup voor sender endpoints faalde.", error=str(exc))
    if endpoint_response.status_code >= 400:
        _raise_http(
            502,
            "sender_directory_lookup_failed",
            "Directory lookup voor sender endpoints faalde.",
            status_code=endpoint_response.status_code,
            upstream_body=endpoint_response.text[:500],
        )
    try:
        endpoint_bundle = endpoint_response.json()
    except Exception as exc:
        _raise_http(502, "sender_directory_lookup_invalid_json", "Sender endpoint lookup gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(endpoint_bundle, dict):
        _raise_http(502, "sender_directory_lookup_invalid_json", "Sender endpoint lookup gaf geen JSON object terug.")
    endpoints = _bundle_resources(endpoint_bundle, "Endpoint")

    oauth_endpoint = _pick_active_endpoint([ep for ep in endpoints if _endpoint_matches_capability(ep, NUTS_OAUTH_CAPABILITY_CODE)])
    bgz_endpoint = _pick_active_endpoint([ep for ep in endpoints if _endpoint_matches_capability(ep, BGZ_SERVER_CAPABILITY_CODE)])

    return {
        "organization": copy.deepcopy(organization),
        "oauth_endpoint": copy.deepcopy(oauth_endpoint) if oauth_endpoint else None,
        "bgz_endpoint": copy.deepcopy(bgz_endpoint) if bgz_endpoint else None,
    }


def _extract_employee_identifier_from_introspection(data: dict[str, Any]) -> str:
    return str(
        _first_non_empty(
            data,
            ("employee_identifier",),
            ("Dezi_id",),
            ("dezi_id",),
            ("uzi_id",),
            ("uziNumber",),
            ("username",),
            ("identifier",),
            ("claims", "employee_identifier"),
            ("claims", "Dezi_id"),
            ("claims", "dezi_id"),
            ("claims", "uzi_id"),
            ("claims", "uziNumber"),
            ("claims", "username"),
            ("claims", "identifier"),
            ("subject", "properties", "subject_id"),
            ("subject", "properties", "Dezi_id"),
            ("subject", "properties", "dezi_id"),
            ("subject", "properties", "uzi_id"),
            ("subject", "properties", "uziNumber"),
            ("subject", "properties", "identifier"),
            ("subject", "properties", "username"),
            ("employee", "identifier"),
        )
        or ""
    ).strip()


def _extract_employee_roles_from_introspection(data: dict[str, Any]) -> list[str]:
    roles = _string_list(
        _first_non_empty(
            data,
            ("employee_roles",),
            ("roles",),
            ("roleName",),
            ("role_name",),
            ("user_role",),
            ("claims", "employee_roles"),
            ("claims", "roles"),
            ("claims", "roleName"),
            ("claims", "role_name"),
            ("subject", "properties", "subject_role"),
            ("subject", "properties", "employee_roles"),
            ("subject", "properties", "roles"),
            ("subject", "properties", "roleName"),
            ("subject", "properties", "role_name"),
            ("employee", "roleName"),
        )
    )
    if not roles:
        for relation in data.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            roles.extend(_string_list(relation.get("roles")))
    seen: set[str] = set()
    unique_roles: list[str] = []
    for role in roles:
        if role in seen:
            continue
        seen.add(role)
        unique_roles.append(role)
    return unique_roles


def _sender_access_token_summary(token_payload: dict[str, Any], introspection: dict[str, Any], source: str) -> dict[str, Any]:
    employee_identifier = _extract_employee_identifier_from_introspection(introspection)
    employee_roles = _extract_employee_roles_from_introspection(introspection)
    return {
        "source": source,
        "token_type": str(token_payload.get("token_type") or "").strip() or None,
        "scope": str(token_payload.get("scope") or "").strip() or None,
        "expires_in": token_payload.get("expires_in"),
        "organization_ura": str(
            _first_non_empty(
                introspection,
                ("organization_ura",),
                ("claims", "organization_ura"),
                ("subject_organization_id",),
                ("subject", "properties", "subject_organization_id"),
                ("organization", "ura"),
            )
            or ""
        ).strip()
        or None,
        "subject_id": _extract_oauth_subject_id(_first_non_empty(introspection, ("client_id",), ("iss",))) or None,
        "employee_identifier": employee_identifier or None,
        "employee_identifier_present": bool(employee_identifier),
        "employee_roles": employee_roles,
        "employee_roles_present": bool(employee_roles),
        "introspection_raw": copy.deepcopy(introspection),
    }


def _build_sender_additional_credentials(session: UserSession) -> list[dict[str, Any]]:
    identity = session.dezi_identity if isinstance(session.dezi_identity, dict) else {}
    employee_identifier = str(identity.get("employee_identifier") or "").strip()
    roles = _string_list(identity.get("roles"))
    display_name = str(identity.get("display_name") or "").strip()
    if not employee_identifier:
        return []

    if not roles:
        roles = [""]

    credentials: list[dict[str, Any]] = []
    for role in roles:
        subject: dict[str, Any] = {"identifier": employee_identifier}
        if display_name:
            subject["name"] = display_name
        role_value = str(role or "").strip()
        if role_value:
            subject["roleName"] = role_value
        credentials.append(
            {
                "@context": [
                    "https://www.w3.org/2018/credentials/v1",
                    "https://nuts.nl/credentials/v1",
                ],
                "type": ["VerifiableCredential", "NutsEmployeeCredential"],
                "credentialSubject": subject,
            }
        )
    return credentials


async def _select_sender_access_token(
    *,
    task: dict[str, Any],
    sender_oauth_endpoint: str,
    session: UserSession,
) -> tuple[dict[str, Any], str, dict[str, Any], list[dict[str, Any]]]:
    additional_credentials = _build_sender_additional_credentials(session)
    if not additional_credentials:
        _raise_http(401, "dezi_login_required", "Log eerst in via DEZI voordat je sender data kunt ophalen.")

    chosen_token_payload: dict[str, Any] | None = None
    chosen_access_token = ""
    chosen_summary: dict[str, Any] | None = None
    chosen_score: tuple[int, int, int] | None = None
    evaluated: list[dict[str, Any]] = []

    token_payload = await _request_sender_access_token(
        task=task,
        sender_oauth_endpoint=sender_oauth_endpoint,
        additional_credentials=additional_credentials,
    )
    access_token = str(token_payload.get("access_token") or "").strip()
    introspection = await _introspect_access_token_payload(access_token)
    summary = _sender_access_token_summary(token_payload, introspection, "credentials")
    evaluated.append(summary)
    score = (
        1 if summary["employee_identifier_present"] and summary["employee_roles_present"] else 0,
        1 if summary["employee_identifier_present"] else 0,
        1 if summary["employee_roles_present"] else 0,
    )
    if chosen_summary is None or score > (chosen_score or (0, 0, 0)):
        chosen_token_payload = token_payload
        chosen_access_token = access_token
        chosen_summary = summary
        chosen_score = score

    assert chosen_token_payload is not None
    assert chosen_summary is not None
    return chosen_token_payload, chosen_access_token, chosen_summary, evaluated


async def _request_sender_access_token(
    *,
    task: dict[str, Any],
    sender_oauth_endpoint: str,
    additional_credentials: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    subject_id = (
        str(settings.receiver_nuts_subject_id or "").strip()
        or str(_extract_task_owner_ura(task) or "").strip()
        or str(settings.receiver_organization_ura or "").strip()
        or _dezi_client_id()
    )
    if not subject_id:
        _raise_http(500, "misconfigured", "Geen receiver Nuts subject-id beschikbaar voor de sender tokenaanvraag.")

    request_url = _join_url(settings.nuts_internal_base, f"/internal/auth/v2/{subject_id}/request-service-access-token")
    payload: dict[str, Any] = {
        "authorization_server": sender_oauth_endpoint,
        "token_type": "Bearer",
    }
    scope = str(settings.sender_data_scope or "").strip()
    if not scope:
        _raise_http(500, "misconfigured", "Geen sender scope geconfigureerd voor de sender tokenaanvraag.")
    payload["scope"] = scope
    if additional_credentials:
        payload["credentials"] = copy.deepcopy(additional_credentials)
    try:
        response = await app.state.http_client.post(
            request_url,
            json=payload,
            headers={"Accept": "application/json"},
            timeout=settings.sender_token_timeout,
        )
    except httpx.HTTPError as exc:
        _raise_http(502, "sender_access_token_request_failed", "Het aanvragen van een sender access token via Nuts is mislukt.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "sender_access_token_request_failed",
            "De Nuts node gaf een fout terug bij het aanvragen van een sender access token.",
            status_code=response.status_code,
            upstream_body=response.text[:1000],
        )
    try:
        body = response.json()
    except Exception as exc:
        _raise_http(502, "sender_access_token_invalid_json", "De Nuts node gaf geen geldige JSON terug voor de sender tokenaanvraag.", error=str(exc))
    if not isinstance(body, dict):
        _raise_http(502, "sender_access_token_invalid_json", "De Nuts node gaf geen JSON object terug voor de sender tokenaanvraag.")
    if not str(body.get("access_token") or "").strip():
        _raise_http(502, "sender_access_token_invalid_json", "De Nuts node gaf geen access_token terug.")
    return body


async def _fetch_sender_path(
    sender_bgz_base: str,
    sender_access_token: str,
    relative_path: str,
    *,
    authorization_base: str | None = None,
) -> dict[str, Any]:
    url = _join_url(sender_bgz_base, relative_path)
    headers = {
        "Accept": "application/fhir+json, application/json",
        "Authorization": f"Bearer {sender_access_token}",
    }
    auth_base = str(authorization_base or "").strip()
    if auth_base:
        headers[SENDER_AUTHORIZATION_BASE_HEADER] = auth_base
    try:
        response = await app.state.http_client.get(
            url,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "url": url,
            "error": str(exc),
        }

    result: dict[str, Any] = {
        "ok": response.status_code < 400,
        "url": url,
        "status_code": response.status_code,
        "content_type": str(response.headers.get("content-type") or "").strip() or None,
    }
    if _is_json_content_type(response.headers.get("content-type") or ""):
        try:
            result["body"] = response.json()
            return result
        except Exception:
            pass
    result["body"] = response.text[:4000]
    return result


async def _pull_sender_data(*, task: dict[str, Any], session: UserSession) -> dict[str, Any]:
    if not str(session.dezi_id_token or "").strip() and not str(session.dezi_userinfo_jwt or "").strip():
        _raise_http(401, "dezi_login_required", "Log eerst in via DEZI voordat je sender data kunt ophalen.")
    if not str(((session.dezi_identity or {}).get("employee_identifier")) or "").strip():
        _raise_http(401, "dezi_login_required", "DEZI sessie mist employee_identifier voor de sender tokenaanvraag.")

    sender_ura = _extract_task_sender_ura(task)
    sender_base_from_task = _extract_sender_bgz_base(task)
    workflow_task_ref = _extract_workflow_task_ref(task)
    workflow_task_identifier_system, workflow_task_identifier_value = _extract_workflow_task_identifier(task)
    authorization_base = str(_extract_task_input_value(task, "authorization-base") or "").strip()

    discovery = await _discover_sender_endpoints(str(sender_ura or ""))
    sender_oauth_endpoint = str(((discovery.get("oauth_endpoint") or {}).get("address")) or "").strip()
    if not sender_oauth_endpoint:
        _raise_http(404, "sender_oauth_endpoint_missing", "Geen Nuts-OAuth endpoint gevonden voor de sender in de directory.", sender_ura=sender_ura)
    sender_auth_server_url = _sender_authorization_server_url(sender_oauth_endpoint, str(sender_ura or ""))

    discovered_bgz_base = str(((discovery.get("bgz_endpoint") or {}).get("address")) or "").strip() or None
    sender_bgz_base = str(discovered_bgz_base or "").strip() or str(sender_base_from_task or "").strip()
    if not sender_bgz_base:
        _raise_http(404, "sender_bgz_base_missing", "Geen sender BgZ endpoint gevonden in de notification Task of directory.", sender_ura=sender_ura)

    token_payload, sender_access_token, selected_token_summary, evaluated_sender_tokens = await _select_sender_access_token(
        task=task,
        sender_oauth_endpoint=sender_auth_server_url,
        session=session,
    )

    named_paths: list[tuple[str, str]] = []
    workflow_task_id = ""
    workflow_task_search_path = ""
    if workflow_task_identifier_system and workflow_task_identifier_value:
        workflow_task_search_path = _workflow_task_search_path(workflow_task_identifier_system, workflow_task_identifier_value)
        named_paths.append(("workflow_task", workflow_task_search_path))
    elif workflow_task_ref:
        _, workflow_task_id = _split_ref(workflow_task_ref)
        if workflow_task_id:
            named_paths.append(("workflow_task", f"Task/{workflow_task_id}"))
    for key, path in SENDER_PULL_PATHS:
        if key == "workflow_task":
            continue
        named_paths.append((key, path))

    fetched = await asyncio.gather(
        *[
            _fetch_sender_path(
                sender_bgz_base,
                sender_access_token,
                path,
                authorization_base=authorization_base,
            )
            for _, path in named_paths
        ],
        return_exceptions=False,
    )
    pulls = {name: result for (name, _), result in zip(named_paths, fetched)}

    return {
        "task_id": str(task.get("id") or "").strip(),
        "notification_summary": _task_summary(task, received_at=""),
        "sender": {
            "sender_ura": sender_ura,
            "authorization_base": authorization_base or None,
            "sender_bgz_base": sender_bgz_base,
            "sender_bgz_base_from_task": sender_base_from_task,
            "sender_bgz_base_from_directory": discovered_bgz_base,
            "sender_oauth_endpoint": sender_oauth_endpoint,
            "workflow_task_ref": workflow_task_ref,
            "workflow_task_identifier_system": workflow_task_identifier_system,
            "workflow_task_identifier_value": workflow_task_identifier_value,
            "workflow_task_search_path": workflow_task_search_path or None,
            "workflow_task_id": workflow_task_id or None,
        },
        "dezi_identity": copy.deepcopy(session.dezi_identity),
        "sender_access_token": {
            "token_type": token_payload.get("token_type"),
            "scope": token_payload.get("scope"),
            "expires_in": token_payload.get("expires_in"),
            "received": True,
            "attestation_source": selected_token_summary.get("source"),
            "selected_introspection": selected_token_summary,
            "candidate_evaluation": evaluated_sender_tokens,
        },
        "pulls": pulls,
    }


def _portal_state(request: Request) -> dict[str, Any]:
    session, _ = _load_session(request, create=False)
    tasks = app.state.task_store.list()
    return {
        "service": {
            "public_root": settings.public_root,
            "public_base": settings.public_base,
            "dezi_client_id": _dezi_client_id(),
            "dezi_callback_url": _dezi_callback_url(),
        },
        "dezi": {
            "logged_in": bool(session and str(session.dezi_id_token or "").strip()),
            "logged_in_at": (session.dezi_logged_in_at if session else None),
            "identity": copy.deepcopy(session.dezi_identity) if session else {},
            "claims": copy.deepcopy(session.dezi_claims) if session else {},
            "token_metadata": copy.deepcopy(session.dezi_token_metadata) if session else {},
            "id_token": (str(session.dezi_id_token or "").strip() if session else None) or None,
            "userinfo_jwt": (str(session.dezi_userinfo_jwt or "").strip() if session else None) or None,
        },
        "tasks": [
            {
                "received_at": stored.received_at,
                "summary": _task_summary(stored.resource, received_at=stored.received_at),
                "resource": stored.resource,
            }
            for stored in tasks
        ],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.sender_token_timeout),
        verify=_verify_arg(settings.outbound_verify_tls, settings.outbound_ca_certs_file),
        follow_redirects=False,
    )
    app.state.dezi_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.dezi_timeout),
        verify=_verify_arg(settings.dezi_verify_tls, settings.dezi_ca_certs_file),
        follow_redirects=True,
    )
    app.state.task_store = TaskStore()
    app.state.session_store = SessionStore()
    app.state.dezi_material = None
    app.state.dezi_oidc_configuration = None
    app.state.dezi_oidc_configuration_fetched_at = 0.0
    app.state.dezi_jwks_cache = {}
    app.state.ui_html = _ui_html()
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.dezi_client.aclose()


app = FastAPI(title="Mock Notification Receiver", lifespan=lifespan)
app.state.task_store = TaskStore()
app.state.session_store = SessionStore()


@app.get("/")
async def portal(request: Request) -> Response:
    _require_portal_basic_auth(request)
    return HTMLResponse(content=str(getattr(app.state, "ui_html", "") or _ui_html()))


@app.get("/health")
async def health() -> dict[str, Any]:
    tasks = app.state.task_store.list()
    return {
        "ok": True,
        "service": "mock-notification-receiver",
        "public_root": settings.public_root,
        "public_base": settings.public_base,
        "require_bearer_token": bool(settings.require_bearer_token),
        "required_scope": str(settings.required_incoming_scope or "").strip() or None,
        "stored_tasks": len(tasks),
    }


@app.get("/ui/state")
async def ui_state(request: Request) -> dict[str, Any]:
    _require_portal_basic_auth(request)
    return _portal_state(request)


@app.get("/auth/dezi/login")
async def dezi_login(request: Request, task_id: str | None = None) -> Response:
    _require_portal_basic_auth(request)
    oidc_config = await _get_dezi_oidc_configuration()
    session, _created = _load_session(request, create=True)
    assert session is not None
    session.pending_state = secrets.token_urlsafe(32)
    session.pending_code_verifier = secrets.token_urlsafe(64)
    session.pending_nonce = secrets.token_urlsafe(32)
    session.pending_task_id = str(task_id or "").strip() or None
    session = _persist_session(session)

    authorize_params = {
        "response_type": "code",
        "client_id": _dezi_client_id(),
        "redirect_uri": _dezi_callback_url(),
        "scope": str(settings.dezi_scope or "").strip() or "openid",
        "state": session.pending_state,
        "nonce": session.pending_nonce,
        "code_challenge": _code_challenge(session.pending_code_verifier),
        "code_challenge_method": "S256",
    }
    location = f"{str(oidc_config.get('authorization_endpoint') or '').strip()}?{urlencode(authorize_params)}"
    response = RedirectResponse(url=location, status_code=302)
    _set_session_cookie(response, session)
    return response


@app.get("/dezi")
@app.get("/auth/dezi/callback")
async def dezi_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> Response:
    session, _created = _load_session(request, create=False)
    if session is None:
        _raise_http(400, "session_missing", "DEZI callback mist een geldige sessie.")
    if error:
        _raise_http(400, "dezi_login_failed", "DEZI login gaf een fout terug.", error=error)
    if not code:
        _raise_http(400, "code_missing", "DEZI callback mist de authorisatiecode.")
    if not state:
        _raise_http(400, "state_missing", "DEZI callback mist de state parameter.")
    if str(state) != str(session.pending_state or ""):
        _raise_http(400, "state_mismatch", "DEZI callback state komt niet overeen met de sessie.")
    if not str(session.pending_code_verifier or "").strip():
        _raise_http(400, "session_missing_code_verifier", "DEZI sessie mist de PKCE code_verifier.")

    session = await _perform_dezi_login(session, code=code)
    task_id = str(session.pending_task_id or "").strip()
    session.pending_task_id = None
    session = _persist_session(session)

    redirect_target = f"{_normalize_base(settings.public_root)}/"
    if task_id:
        redirect_target = f"{redirect_target}?{urlencode({'task_id': task_id})}"
    response = RedirectResponse(url=redirect_target, status_code=302)
    _set_session_cookie(response, session)
    return response


@app.post("/auth/dezi/logout")
async def dezi_logout(request: Request) -> Response:
    _require_portal_basic_auth(request)
    session_id = str(request.cookies.get(settings.session_cookie_name) or "").strip()
    app.state.session_store.delete(session_id)
    response = JSONResponse({"ok": True})
    _delete_session_cookie(response)
    return response


@app.get("/fhir/metadata")
async def metadata() -> Response:
    return _fhir_json_response(_capability_statement())


@app.get("/fhir/Task")
async def list_tasks(request: Request) -> Response:
    _require_portal_basic_auth(request)
    entries = []
    for stored in app.state.task_store.list():
        entries.append({"resource": stored.resource})
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }
    return _fhir_json_response(bundle)


@app.post("/fhir/Task")
async def create_task(payload: dict[str, Any], request: Request) -> Response:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="FHIR Task body moet een JSON object zijn.")
    resource_type = str(payload.get("resourceType") or "").strip()
    if resource_type and resource_type != "Task":
        raise HTTPException(status_code=400, detail="Alleen FHIR Task resources zijn toegestaan.")

    task = copy.deepcopy(payload)
    task["resourceType"] = "Task"
    await _authorize_notification_request(request, task)
    task_id = str(task.get("id") or "").strip() or str(uuid.uuid4())
    task["id"] = task_id
    if not str(task.get("status") or "").strip():
        task["status"] = settings.default_task_status

    meta = task.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    meta["lastUpdated"] = _now_iso()
    task["meta"] = meta

    stored = app.state.task_store.save(task)
    headers = {
        "Location": f"{_normalize_base(settings.public_base)}/Task/{task_id}",
        "Content-Location": f"{_normalize_base(settings.public_base)}/Task/{task_id}",
    }
    return _fhir_json_response(stored.resource, status_code=201, headers=headers)


@app.get("/fhir/Task/{task_id}")
async def read_task(task_id: str, request: Request) -> Response:
    _require_portal_basic_auth(request)
    stored = app.state.task_store.get(task_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Task niet gevonden.")
    return _fhir_json_response(stored.resource)


@app.get("/debug/tasks")
async def debug_tasks(request: Request) -> dict[str, Any]:
    _require_portal_basic_auth(request)
    tasks = app.state.task_store.list()
    return {
        "total": len(tasks),
        "tasks": [
            {
                "received_at": stored.received_at,
                "summary": _task_summary(stored.resource, received_at=stored.received_at),
                "resource": stored.resource,
            }
            for stored in tasks
        ],
    }


@app.get("/debug/tasks/latest")
async def debug_latest_task(request: Request) -> dict[str, Any]:
    _require_portal_basic_auth(request)
    stored = app.state.task_store.latest()
    if stored is None:
        raise HTTPException(status_code=404, detail="Nog geen Task ontvangen.")
    return {
        "received_at": stored.received_at,
        "summary": _task_summary(stored.resource, received_at=stored.received_at),
        "resource": stored.resource,
    }


@app.get("/debug/tasks/latest-summary")
async def debug_latest_summary(request: Request) -> dict[str, Any]:
    _require_portal_basic_auth(request)
    stored = app.state.task_store.latest()
    if stored is None:
        raise HTTPException(status_code=404, detail="Nog geen Task ontvangen.")
    return _task_summary(stored.resource, received_at=stored.received_at)


@app.delete("/debug/tasks")
async def reset_tasks(request: Request) -> dict[str, Any]:
    _require_portal_basic_auth(request)
    deleted = app.state.task_store.clear()
    return {"ok": True, "deleted": deleted}


@app.post("/ui/tasks/{task_id}/pull")
async def ui_pull_task(task_id: str, request: Request) -> dict[str, Any]:
    _require_portal_basic_auth(request)
    session, _created = _load_session(request, create=False)
    if session is None or not str(session.dezi_id_token or "").strip():
        _raise_http(401, "dezi_login_required", "Log eerst in via DEZI voordat je sender data kunt ophalen.")
    stored = app.state.task_store.get(task_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Task niet gevonden.")
    return await _pull_sender_data(task=stored.resource, session=session)


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
