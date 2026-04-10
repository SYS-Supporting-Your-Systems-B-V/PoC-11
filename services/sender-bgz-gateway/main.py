from __future__ import annotations

import copy
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from configs import MEDICAL_ROLE_CODES, REQUIRED_SCOPES, settings

logging.basicConfig(level=getattr(logging, str(settings.log_level or "INFO").upper(), logging.INFO))
logger = logging.getLogger("sender_bgz_gateway.app")

FHIR_JSON_CONTENT_TYPES = (
    "application/fhir+json",
    "application/json+fhir",
    "application/json",
)
UPSTREAM_RESPONSE_HEADERS = {
    "content-type",
    "etag",
    "last-modified",
    "location",
    "content-location",
}
UPSTREAM_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "if-match",
    "if-none-match",
    "if-modified-since",
    "if-unmodified-since",
    "prefer",
}
ALLOWED_DATA_RESOURCES = {
    "Patient",
    "Condition",
    "AllergyIntolerance",
    "MedicationStatement",
    "Observation",
    "DocumentReference",
    "Binary",
}
AUTHORIZATION_BASE_HEADER = "X-Authorization-Base"
TERMINAL_TASK_STATUSES = {
    "cancelled",
    "canceled",
    "closed",
    "complete",
    "completed",
    "entered-in-error",
    "failed",
    "rejected",
}


def _normalize_fhir_base(base: str) -> str:
    return str(base or "").strip().rstrip("/")


def _join_url(base: str, path: str) -> str:
    return f"{_normalize_fhir_base(base)}/{str(path or '').lstrip('/')}"


def _verify_arg() -> bool | str:
    if not settings.verify_tls:
        return False
    if settings.ca_certs_file:
        return settings.ca_certs_file
    return True


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_path(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for segment in path:
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _first_non_empty(data: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _read_path(data, path)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
    raw = raw.replace(",", " ")
    return [item.strip() for item in raw.split() if item.strip()]


def _normalize_reference(value: Any) -> str:
    ref = str(value or "").strip()
    if not ref or ref.startswith("#"):
        return ""
    parts = [part for part in ref.split("/") if part]
    if not parts:
        return ""
    if "_history" in parts:
        idx = parts.index("_history")
        if idx >= 2:
            return f"{parts[idx - 2]}/{parts[idx - 1]}"
    if "://" in ref and len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    if len(parts) >= 2 and parts[0] in {"http:", "https:"}:
        return f"{parts[-2]}/{parts[-1]}"
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[0]


def _resource_ref(resource: dict[str, Any]) -> str:
    resource_type = str(resource.get("resourceType") or "").strip()
    resource_id = str(resource.get("id") or "").strip()
    if not resource_type or not resource_id:
        return ""
    return f"{resource_type}/{resource_id}"


def _has_identifier(resource: dict[str, Any], *, system: str, value: str) -> bool:
    for ident in resource.get("identifier") or []:
        if not isinstance(ident, dict):
            continue
        if str(ident.get("system") or "") == system and str(ident.get("value") or "") == value:
            return True
    return False


def _iter_bundle_entries(bundle: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for entry in bundle.get("entry") or []:
        if isinstance(entry, dict):
            yield entry


def _bundle_resources(bundle: dict[str, Any], resource_type: str | None = None) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for entry in _iter_bundle_entries(bundle):
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            continue
        if resource_type and str(resource.get("resourceType") or "") != resource_type:
            continue
        resources.append(resource)
    return resources


def _is_json_response(response: httpx.Response) -> bool:
    content_type = str(response.headers.get("content-type") or "").lower()
    return any(marker in content_type for marker in FHIR_JSON_CONTENT_TYPES)


def _proxy_headers_from_request(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in UPSTREAM_REQUEST_HEADERS:
            headers[key] = value
    headers.setdefault("Accept", "application/fhir+json")
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in response.headers.items():
        if key.lower() in UPSTREAM_RESPONSE_HEADERS:
            headers[key] = value
    return headers


def _raise_http(status_code: int, reason: str, message: str, **extra: Any) -> None:
    detail: dict[str, Any] = {"reason": reason, "message": message}
    for key, value in extra.items():
        if value is not None:
            detail[key] = value
    raise HTTPException(status_code=status_code, detail=detail)


@dataclass
class TokenContext:
    raw: dict[str, Any]
    active: bool
    organization_ura: str
    employee_identifier: str
    employee_roles: list[str]
    scopes: list[str]
    authorization_base: str
    subject_id: str = ""


@dataclass
class WorkflowAuthorization:
    token: TokenContext
    task: dict[str, Any]
    patient_bsn: str
    patient_resource: Optional[dict[str, Any]] = None

    @property
    def task_id(self) -> str:
        return str(self.task.get("id") or "").strip()

    @property
    def patient_id(self) -> str:
        if not isinstance(self.patient_resource, dict):
            return ""
        return str(self.patient_resource.get("id") or "").strip()


def _extract_token_context(data: dict[str, Any]) -> TokenContext:
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
    scopes = _string_list(
        _first_non_empty(
            data,
            ("scope",),
            ("claims", "scope"),
            ("client_qualifications",),
            ("subject", "properties", "client_qualifications"),
        )
    )
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
        employee_identifier=str(
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
        ).strip(),
        employee_roles=roles,
        scopes=scopes,
        authorization_base=str(
            _first_non_empty(
                data,
                ("authorization-base",),
                ("authorization_base",),
                ("claims", "authorization-base"),
                ("claims", "authorization_base"),
                ("subject", "properties", "authorization-base"),
                ("subject", "properties", "authorization_base"),
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
    )


def _extract_task_owner_ura(task: dict[str, Any]) -> str:
    owner = task.get("owner") or {}
    identifier = owner.get("identifier") or {}
    return str(identifier.get("value") or "").strip()


def _extract_task_patient_bsn(task: dict[str, Any]) -> str:
    patient = task.get("for") or {}
    identifier = patient.get("identifier") or {}
    system = str(identifier.get("system") or "").strip()
    value = str(identifier.get("value") or "").strip()
    if system and system != settings.patient_identifier_system:
        logger.warning("Unexpected patient identifier system on workflow task: %s", system)
    return value


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


def _request_authorization_base(request: Request) -> str:
    return str(request.headers.get(AUTHORIZATION_BASE_HEADER) or "").strip()


def _request_task_identifier(request: Request) -> tuple[str, str]:
    raw = str(request.query_params.get("identifier") or "").strip()
    if not raw:
        _raise_http(400, "missing_task_identifier", "Task search vereist een identifier query parameter.")
    system, separator, value = raw.partition("|")
    system = system.strip()
    value = value.strip()
    if not separator or not system or not value:
        _raise_http(
            400,
            "invalid_task_identifier",
            "Task identifier moet de vorm <system>|<value> hebben.",
            received_identifier=raw,
        )
    return system, value


def _task_has_authorization_base(task: dict[str, Any], authorization_base: str) -> bool:
    for ident in task.get("identifier") or []:
        if not isinstance(ident, dict):
            continue
        if (
            str(ident.get("system") or "") == settings.authorization_base_system
            and str(ident.get("value") or "") == authorization_base
        ):
            return True
    for item in task.get("input") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type") or {}
        coding_list = item_type.get("coding") or []
        for coding in coding_list:
            if not isinstance(coding, dict):
                continue
            if str(coding.get("code") or "") == "authorization-base":
                return str(item.get("valueString") or "") == authorization_base
    return False


def _task_is_active(task: dict[str, Any]) -> bool:
    status = str(task.get("status") or "").strip().lower()
    if not status:
        return False
    return status not in TERMINAL_TASK_STATUSES


def _resource_matches_patient(resource: dict[str, Any], *, patient_id: str, patient_bsn: str) -> bool:
    resource_type = str(resource.get("resourceType") or "").strip()
    if resource_type == "Patient":
        return _resource_ref(resource) == f"Patient/{patient_id}" or _has_identifier(
            resource,
            system=settings.patient_identifier_system,
            value=patient_bsn,
        )

    ref_candidates: list[str] = []
    if resource_type in {"Condition", "MedicationStatement", "Observation", "DocumentReference"}:
        subject = resource.get("subject") or {}
        if isinstance(subject, dict):
            ref_candidates.append(_normalize_reference(subject.get("reference")))
    if resource_type in {"Condition", "AllergyIntolerance", "MedicationStatement", "Observation"}:
        patient = resource.get("patient") or {}
        if isinstance(patient, dict):
            ref_candidates.append(_normalize_reference(patient.get("reference")))
    return any(ref in {patient_id, f"Patient/{patient_id}"} for ref in ref_candidates if ref)


def _collect_references(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "reference" and isinstance(item, str):
                ref = _normalize_reference(item)
                if ref:
                    refs.add(ref)
                continue
            if key == "url" and isinstance(item, str):
                ref = _normalize_reference(item)
                if ref.startswith("Binary/"):
                    refs.add(ref)
                continue
            refs.update(_collect_references(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_references(item))
    return refs


def _filter_bundle_to_patient(bundle: dict[str, Any], *, primary_type: str, patient_id: str, patient_bsn: str) -> dict[str, Any]:
    if str(bundle.get("resourceType") or "") != "Bundle":
        return bundle

    kept_primary: list[dict[str, Any]] = []
    include_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in _iter_bundle_entries(bundle):
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            continue
        search_mode = str(((entry.get("search") or {}).get("mode") or "")).strip().lower()
        is_primary = str(resource.get("resourceType") or "") == primary_type and search_mode != "include"
        if is_primary and _resource_matches_patient(resource, patient_id=patient_id, patient_bsn=patient_bsn):
            kept_primary.append(copy.deepcopy(entry))
        elif not is_primary:
            include_candidates.append((copy.deepcopy(entry), resource))

    allowed_refs = set()
    for entry in kept_primary:
        resource = entry.get("resource") or {}
        resource_ref = _resource_ref(resource)
        if resource_ref:
            allowed_refs.add(resource_ref)
        allowed_refs.update(_collect_references(resource))

    kept_entries = list(kept_primary)
    pending = list(include_candidates)
    while pending:
        next_pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
        changed = False
        for entry, resource in pending:
            resource_ref = _resource_ref(resource)
            if resource_ref and resource_ref in allowed_refs:
                kept_entries.append(entry)
                allowed_refs.update(_collect_references(resource))
                changed = True
            else:
                next_pending.append((entry, resource))
        if not changed:
            break
        pending = next_pending

    filtered = copy.deepcopy(bundle)
    filtered["entry"] = kept_entries
    filtered["total"] = len(kept_primary)
    return filtered


def _ensure_task_update_payload(existing_task: dict[str, Any], incoming_task: dict[str, Any], authorization_base: str) -> dict[str, Any]:
    updated = copy.deepcopy(existing_task)
    allowed_mutable_fields = {"status", "businessStatus", "statusReason", "note", "output", "restriction"}
    for field in allowed_mutable_fields:
        if field in incoming_task:
            updated[field] = copy.deepcopy(incoming_task[field])
    updated["resourceType"] = "Task"
    updated["id"] = str(existing_task.get("id") or "")

    identifiers = []
    for ident in updated.get("identifier") or []:
        if not isinstance(ident, dict):
            continue
        if str(ident.get("system") or "") == settings.authorization_base_system:
            continue
        identifiers.append(copy.deepcopy(ident))
    identifiers.append(
        {
            "system": settings.authorization_base_system,
            "value": authorization_base,
        }
    )
    updated["identifier"] = identifiers

    inputs = []
    auth_input_present = False
    for item in updated.get("input") or []:
        if not isinstance(item, dict):
            continue
        coding_list = ((item.get("type") or {}).get("coding") or [])
        is_auth_base = any(
            isinstance(coding, dict) and str(coding.get("code") or "") == "authorization-base"
            for coding in coding_list
        )
        next_item = copy.deepcopy(item)
        if is_auth_base:
            next_item["valueString"] = authorization_base
            auth_input_present = True
        inputs.append(next_item)
    if not auth_input_present:
        inputs.append(
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://fhir.nl/fhir/NamingSystem/TaskParameter",
                            "code": "authorization-base",
                        }
                    ]
                },
                "valueString": authorization_base,
            }
        )
    updated["input"] = inputs
    return updated


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.upstream_timeout),
        verify=_verify_arg(),
        follow_redirects=False,
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="Sender BgZ Gateway", lifespan=lifespan)


async def _upstream_get_json(path: str, *, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    url = _join_url(settings.upstream_fhir_base, path)
    try:
        response = await app.state.http_client.get(
            url,
            params=params,
            headers={"Accept": "application/fhir+json"},
        )
    except httpx.HTTPError as exc:
        logger.exception("Upstream lookup failed url=%s", url)
        _raise_http(502, "upstream_lookup_failed", "Interne FHIR lookup naar SYS HAPI faalde.", error=str(exc))
    if response.status_code >= 400:
        _raise_http(
            502,
            "upstream_lookup_failed",
            "Interne FHIR lookup naar SYS HAPI faalde.",
            status_code=response.status_code,
            upstream_body=response.text[:500],
        )
    try:
        payload = response.json()
    except Exception as exc:
        logger.exception("Upstream lookup did not return JSON url=%s", url)
        _raise_http(502, "upstream_lookup_invalid_json", "Interne FHIR lookup gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(payload, dict):
        _raise_http(502, "upstream_lookup_invalid_json", "Interne FHIR lookup gaf geen JSON object terug.")
    return payload


async def _introspect_token(token: str) -> TokenContext:
    url = _join_url(settings.nuts_internal_base, "/internal/auth/v2/accesstoken/introspect")
    try:
        response = await app.state.http_client.post(
            url,
            data={"token": token},
            headers={"Accept": "application/json"},
            timeout=settings.introspection_timeout,
        )
    except httpx.HTTPError as exc:
        logger.exception("Token introspection failed")
        _raise_http(502, "introspection_failed", "Nuts token introspection faalde.", error=str(exc))

    if response.status_code >= 400:
        _raise_http(
            502,
            "introspection_failed",
            "Nuts token introspection faalde.",
            status_code=response.status_code,
            upstream_body=response.text[:500],
        )
    try:
        payload = response.json()
    except Exception as exc:
        logger.exception("Token introspection did not return JSON")
        _raise_http(502, "introspection_invalid_json", "Nuts token introspection gaf geen geldige JSON terug.", error=str(exc))
    if not isinstance(payload, dict):
        _raise_http(502, "introspection_invalid_json", "Nuts token introspection gaf geen JSON object terug.")
    ctx = _extract_token_context(payload)
    if not ctx.active:
        _raise_http(401, "inactive_token", "Toegangstoken is niet actief of niet geldig.")
    if not ctx.organization_ura:
        _raise_http(403, "missing_organization_ura", "Introspectie mist organization_ura.")
    return ctx


async def _authorize_request(request: Request, *, require_professional: bool, require_active_task: bool) -> WorkflowAuthorization:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        _raise_http(401, "missing_bearer_token", "Authorization header met Bearer token is verplicht.")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        _raise_http(401, "missing_bearer_token", "Bearer token ontbreekt.")

    token_ctx = await _introspect_token(token)
    request_authorization_base = _request_authorization_base(request)
    if token_ctx.authorization_base and request_authorization_base and request_authorization_base != token_ctx.authorization_base:
        _raise_http(
            403,
            "authorization_base_mismatch",
            "Authorization-base header matcht niet met de claim uit de token introspectie.",
            token_authorization_base=token_ctx.authorization_base,
            request_authorization_base=request_authorization_base,
        )
    if not token_ctx.authorization_base:
        if not request_authorization_base:
            _raise_http(403, "missing_authorization_base", "Introspectie mist authorization-base claim.")
        token_ctx.authorization_base = request_authorization_base
    if require_professional:
        if not token_ctx.employee_identifier:
            _raise_http(403, "missing_employee_identifier", "Introspectie mist employee_identifier.")
        if not token_ctx.employee_roles:
            _raise_http(403, "missing_employee_roles", "Introspectie mist employee_roles.")
        if MEDICAL_ROLE_CODES and not set(token_ctx.employee_roles).intersection(MEDICAL_ROLE_CODES):
            _raise_http(
                403,
                "medical_role_not_allowed",
                "De aangeleverde employee_roles voldoen niet aan de toegestane medische rollen.",
                allowed_roles=MEDICAL_ROLE_CODES,
                received_roles=token_ctx.employee_roles,
            )
        if REQUIRED_SCOPES and not set(token_ctx.scopes).intersection(REQUIRED_SCOPES):
            _raise_http(
                403,
                "scope_not_allowed",
                "De aangeleverde scope voldoet niet aan de toegestane scopes.",
                allowed_scopes=REQUIRED_SCOPES,
                received_scopes=token_ctx.scopes,
            )

    bundle = await _upstream_get_json(
        "Task",
        params=[
            ("identifier", f"{settings.authorization_base_system}|{token_ctx.authorization_base}"),
            ("_count", "5"),
        ],
    )
    tasks = [
        task
        for task in _bundle_resources(bundle, "Task")
        if _task_has_authorization_base(task, token_ctx.authorization_base)
    ]
    if not tasks:
        _raise_http(403, "workflow_task_not_found", "Geen workflow task gevonden voor authorization-base.")
    if len(tasks) > 1:
        _raise_http(409, "workflow_task_not_unique", "Meerdere workflow tasks gevonden voor authorization-base.")
    task = tasks[0]
    task_owner_ura = _extract_task_owner_ura(task)
    if not task_owner_ura or task_owner_ura not in {token_ctx.organization_ura, token_ctx.subject_id}:
        _raise_http(
            403,
            "organization_not_authorized",
            "organization_ura uit introspectie matcht niet met de workflow task owner.",
            token_organization_ura=token_ctx.organization_ura,
            token_subject_id=token_ctx.subject_id or None,
            task_owner_ura=task_owner_ura or None,
        )
    if require_active_task and not _task_is_active(task):
        _raise_http(403, "workflow_task_not_active", "Workflow task is niet actief voor deze update.")
    patient_bsn = _extract_task_patient_bsn(task)
    if not patient_bsn:
        _raise_http(500, "workflow_task_missing_patient", "Workflow task bevat geen patiëntidentificatie.")
    return WorkflowAuthorization(token=token_ctx, task=task, patient_bsn=patient_bsn)


async def _ensure_patient_loaded(authz: WorkflowAuthorization) -> WorkflowAuthorization:
    if authz.patient_resource is not None:
        return authz
    bundle = await _upstream_get_json(
        "Patient",
        params=[
            ("identifier", f"{settings.patient_identifier_system}|{authz.patient_bsn}"),
            ("_count", "5"),
        ],
    )
    patients = [
        patient
        for patient in _bundle_resources(bundle, "Patient")
        if _has_identifier(
            patient,
            system=settings.patient_identifier_system,
            value=authz.patient_bsn,
        )
    ]
    if not patients:
        _raise_http(404, "authorized_patient_not_found", "Geautoriseerde patiënt is niet gevonden op de interne FHIR server.")
    if len(patients) > 1:
        _raise_http(409, "authorized_patient_not_unique", "Meerdere patiënten gevonden voor de geautoriseerde BSN.")
    authz.patient_resource = patients[0]
    return authz


async def _binary_allowed(authz: WorkflowAuthorization, binary_id: str) -> bool:
    authz = await _ensure_patient_loaded(authz)
    bundle = await _upstream_get_json(
        "DocumentReference",
        params=[("patient", authz.patient_id), ("_count", "200")],
    )
    target_ref = f"Binary/{binary_id}"
    for docref in _bundle_resources(bundle, "DocumentReference"):
        if not _resource_matches_patient(docref, patient_id=authz.patient_id, patient_bsn=authz.patient_bsn):
            continue
        for content in docref.get("content") or []:
            if not isinstance(content, dict):
                continue
            attachment = content.get("attachment") or {}
            if not isinstance(attachment, dict):
                continue
            for key in ("url", "reference"):
                ref = _normalize_reference(attachment.get(key))
                if ref == target_ref:
                    return True
    return False


def _patient_scoped_params(resource_type: str, request: Request, patient_id: str, patient_bsn: str) -> list[tuple[str, str]]:
    items = list(request.query_params.multi_items())
    if resource_type == "Patient":
        items = [(key, value) for key, value in items if key != "identifier"]
        items.append(("identifier", f"{settings.patient_identifier_system}|{patient_bsn}"))
        return items

    items = [(key, value) for key, value in items if key not in {"patient", "subject"}]
    items.append(("patient", patient_id))
    return items


def _json_response(payload: dict[str, Any], *, status_code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    out_headers = dict(headers or {})
    out_headers["Content-Type"] = "application/fhir+json"
    return JSONResponse(content=payload, status_code=status_code, headers=out_headers)


def _pass_through_response(response: httpx.Response) -> Response:
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=_response_headers(response),
        media_type=None,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "sender-bgz-gateway",
        "medical_role_valueset_url": settings.medical_role_valueset_url,
        "medical_role_allowlist_configured": bool(MEDICAL_ROLE_CODES),
    }


@app.get("/fhir/metadata")
async def metadata(request: Request) -> Response:
    url = _join_url(settings.upstream_fhir_base, "metadata")
    response = await app.state.http_client.get(
        url,
        headers=_proxy_headers_from_request(request),
        params=list(request.query_params.multi_items()),
    )
    return _pass_through_response(response)


@app.get("/fhir/Task/{task_id}")
async def read_workflow_task(task_id: str, request: Request) -> Response:
    authz = await _authorize_request(request, require_professional=False, require_active_task=False)
    if task_id != authz.task_id:
        _raise_http(403, "task_id_not_authorized", "Opgevraagde Task id hoort niet bij de geautoriseerde workflow task.")
    url = _join_url(settings.upstream_fhir_base, f"Task/{task_id}")
    response = await app.state.http_client.get(url, headers=_proxy_headers_from_request(request))
    if response.status_code >= 400:
        return _pass_through_response(response)
    if not _is_json_response(response):
        return _pass_through_response(response)
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("id") or "") != task_id:
        _raise_http(502, "upstream_task_invalid", "Interne FHIR server gaf een ongeldige Task terug.")
    if not _task_has_authorization_base(payload, authz.token.authorization_base):
        _raise_http(403, "task_authorization_mismatch", "Opgevraagde Task hoort niet bij authorization-base.")
    return _json_response(payload, headers=_response_headers(response))


@app.get("/fhir/Task")
async def search_workflow_task(request: Request) -> Response:
    identifier_system, identifier_value = _request_task_identifier(request)
    authz = await _authorize_request(request, require_professional=False, require_active_task=False)
    if not _has_identifier(authz.task, system=identifier_system, value=identifier_value):
        return _json_response({"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []})
    return _json_response(
        {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 1,
            "entry": [{"resource": authz.task}],
        }
    )


@app.put("/fhir/Task/{task_id}")
async def update_workflow_task(task_id: str, request: Request) -> Response:
    authz = await _authorize_request(request, require_professional=False, require_active_task=True)
    if task_id != authz.task_id:
        _raise_http(403, "task_id_not_authorized", "Opgevraagde Task id hoort niet bij de geautoriseerde workflow task.")
    try:
        incoming = await request.json()
    except Exception as exc:
        _raise_http(400, "invalid_json", "Task update body is geen geldige JSON.", error=str(exc))
    if not isinstance(incoming, dict):
        _raise_http(400, "invalid_json", "Task update body moet een JSON object zijn.")
    if str(incoming.get("resourceType") or "Task") != "Task":
        _raise_http(400, "invalid_task_resource", "Alleen FHIR Task resources zijn toegestaan.")
    if incoming.get("id") and str(incoming.get("id") or "") != task_id:
        _raise_http(400, "task_id_mismatch", "Task id in body matcht niet met de URL.")

    payload = _ensure_task_update_payload(authz.task, incoming, authz.token.authorization_base)
    url = _join_url(settings.upstream_fhir_base, f"Task/{task_id}")
    headers = _proxy_headers_from_request(request)
    headers["Content-Type"] = "application/fhir+json"
    response = await app.state.http_client.put(url, json=payload, headers=headers)
    if response.status_code >= 400 or not _is_json_response(response):
        return _pass_through_response(response)
    body = response.json()
    if not isinstance(body, dict):
        _raise_http(502, "upstream_task_invalid", "Interne FHIR server gaf een ongeldige Task terug.")
    return _json_response(body, status_code=response.status_code, headers=_response_headers(response))


@app.get("/fhir/Observation/$lastn")
async def observation_lastn(request: Request) -> Response:
    authz = await _authorize_request(request, require_professional=True, require_active_task=False)
    authz = await _ensure_patient_loaded(authz)
    params = _patient_scoped_params("Observation", request, authz.patient_id, authz.patient_bsn)
    url = _join_url(settings.upstream_fhir_base, "Observation/$lastn")
    response = await app.state.http_client.get(url, params=params, headers=_proxy_headers_from_request(request))
    if response.status_code >= 400 or not _is_json_response(response):
        return _pass_through_response(response)
    payload = response.json()
    if isinstance(payload, dict):
        payload = _filter_bundle_to_patient(payload, primary_type="Observation", patient_id=authz.patient_id, patient_bsn=authz.patient_bsn)
        return _json_response(payload, headers=_response_headers(response))
    return _pass_through_response(response)


@app.get("/fhir/{resource_type}")
async def search_resource(resource_type: str, request: Request) -> Response:
    if resource_type == "Task":
        _raise_http(405, "task_search_not_supported", "Gebruik Task/{id} voor de geautoriseerde workflow task.")
    if resource_type not in ALLOWED_DATA_RESOURCES or resource_type == "Binary":
        _raise_http(404, "resource_not_supported", "Deze FHIR resource wordt niet door de sender gateway ondersteund.")

    authz = await _authorize_request(request, require_professional=True, require_active_task=False)
    authz = await _ensure_patient_loaded(authz)
    params = _patient_scoped_params(resource_type, request, authz.patient_id, authz.patient_bsn)
    url = _join_url(settings.upstream_fhir_base, resource_type)
    response = await app.state.http_client.get(url, params=params, headers=_proxy_headers_from_request(request))
    if response.status_code >= 400 or not _is_json_response(response):
        return _pass_through_response(response)

    payload = response.json()
    if isinstance(payload, dict):
        payload = _filter_bundle_to_patient(payload, primary_type=resource_type, patient_id=authz.patient_id, patient_bsn=authz.patient_bsn)
        return _json_response(payload, headers=_response_headers(response))
    return _pass_through_response(response)


@app.get("/fhir/{resource_type}/{resource_id}")
async def read_resource(resource_type: str, resource_id: str, request: Request) -> Response:
    if resource_type == "Task":
        return await read_workflow_task(resource_id, request)
    if resource_type not in ALLOWED_DATA_RESOURCES:
        _raise_http(404, "resource_not_supported", "Deze FHIR resource wordt niet door de sender gateway ondersteund.")

    authz = await _authorize_request(request, require_professional=True, require_active_task=False)
    authz = await _ensure_patient_loaded(authz)

    if resource_type == "Binary":
        if not await _binary_allowed(authz, resource_id):
            _raise_http(403, "binary_not_authorized", "Binary resource hoort niet bij de geautoriseerde patiëntcontext.")
        url = _join_url(settings.upstream_fhir_base, f"Binary/{resource_id}")
        response = await app.state.http_client.get(url, headers=_proxy_headers_from_request(request))
        return _pass_through_response(response)

    url = _join_url(settings.upstream_fhir_base, f"{resource_type}/{resource_id}")
    response = await app.state.http_client.get(url, headers=_proxy_headers_from_request(request))
    if response.status_code >= 400:
        return _pass_through_response(response)
    if not _is_json_response(response):
        return _pass_through_response(response)

    payload = response.json()
    if not isinstance(payload, dict):
        _raise_http(502, "upstream_resource_invalid", "Interne FHIR server gaf geen geldige resource JSON terug.")
    if not _resource_matches_patient(payload, patient_id=authz.patient_id, patient_bsn=authz.patient_bsn):
        _raise_http(403, "resource_not_authorized", "FHIR resource hoort niet bij de geautoriseerde patiëntcontext.")
    return _json_response(payload, headers=_response_headers(response))


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
