# tests/test_bgz_endpoints.py

import base64
import importlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_app_module():
    """Importeer de FastAPI app-module.

    In verschillende PoC iteraties heet de module soms `main`, `app` of `mcsd.app`.
    """

    candidates = ("main", "app", "mcsd.app")
    last_err: Optional[Exception] = None
    for modname in candidates:
        try:
            return importlib.import_module(modname)
        except ModuleNotFoundError as exc:
            last_err = exc
            continue
    raise ModuleNotFoundError(
        "Kon de app-module niet importeren. Verwacht één van: main, app, mcsd.app"
    ) from last_err


def _data_file(appmod, filename: str) -> Path:
    """Vind een data bestand (zoals notification-task.json) op dezelfde plek als de app."""
    base = Path(appmod.__file__).resolve().parent
    return base / "data" / filename


def _import_iti90_app_module():
    service_root = Path(__file__).resolve().parents[1]
    sys.modules.pop("configs", None)
    sys.modules.pop("main", None)
    sys.path.insert(0, str(service_root))
    try:
        return importlib.import_module("main")
    finally:
        if sys.path and sys.path[0] == str(service_root):
            sys.path.pop(0)


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_dt(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"Datetime ontbreekt/ongeldig: {value!r}")
    s = value.strip()
    # Python's fromisoformat accepteert niet altijd 'Z'
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _assert_urn_uuid(value: str) -> None:
    assert isinstance(value, str)
    assert value.startswith("urn:uuid:"), value
    uuid.UUID(value.split("urn:uuid:", 1)[1])


def _find_task_extension(task: Dict[str, Any], url: str) -> Optional[Dict[str, Any]]:
    for ext in (task or {}).get("extension") or []:
        if (ext or {}).get("url") == url:
            return ext
    return None


def _assert_workflow_task_target_extensions(
    appmod,
    task: Dict[str, Any],
    *,
    expected_healthcareservice_identifier: Optional[Dict[str, Any]],
    expected_location_identifier: Optional[Dict[str, Any]],
) -> None:
    hs_ext = _find_task_extension(task, appmod.TASK_EXT_TASK_STU3_HEALTHCARESERVICE_URL)
    loc_ext = _find_task_extension(task, appmod.TASK_EXT_TASK_STU3_LOCATION_URL)

    if expected_healthcareservice_identifier is None:
        assert hs_ext is None
    else:
        assert hs_ext is not None
        assert hs_ext.get("valueIdentifier") == expected_healthcareservice_identifier

    if expected_location_identifier is None:
        assert loc_ext is None
    else:
        assert loc_ext is not None
        assert loc_ext.get("valueIdentifier") == expected_location_identifier


def _find_task_identifier(task: Dict[str, Any], system: str) -> Optional[Dict[str, Any]]:
    for identifier in (task or {}).get("identifier") or []:
        if (identifier or {}).get("system") == system:
            return identifier
    return None


def _find_task_input(task: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    for inp in (task or {}).get("input") or []:
        coding = (((inp or {}).get("type") or {}).get("coding") or [])
        if not isinstance(coding, list):
            continue
        for item in coding:
            if (item or {}).get("code") == code:
                return inp
    return None


def assert_task_matches_notification_template(
    *,
    task: Dict[str, Any],
    template: Dict[str, Any],
    sender_ura: str,
    sender_uzi_sys: str,
    receiver_ura: str,
    expected_workflow_task_identifier_value: Optional[str],
    expected_authorization_base: Optional[str] = None,
) -> None:
    """Asserties voor de gegenereerde BgZ Task op basis van notification-task.json.

    Doel:
    - de notification Task moet exact de minimale Step 2 shape houden
    - dynamische velden worden op vorm/inhoud gevalideerd
    """

    # --- Ongewijzigd t.o.v. template ---
    for key in ("resourceType", "status", "intent", "code"):
        assert task.get(key) == template.get(key), f"Veld '{key}' wijkt af van template"

    assert set(task.keys()) == {"resourceType", "basedOn", "status", "intent", "code", "requester", "owner", "input"}

    # --- basedOn: Workflow Task identifier ---
    assert isinstance(task.get("basedOn"), list) and task["basedOn"], "basedOn ontbreekt"
    assert isinstance(task["basedOn"][0], dict), "basedOn[0] moet een dict zijn"
    based_on_identifier = (task["basedOn"][0].get("identifier") or {})
    assert based_on_identifier["system"] == template["basedOn"][0]["identifier"]["system"]
    based_on_identifier_value = str(based_on_identifier.get("value") or "").strip()
    assert based_on_identifier_value, "basedOn[0].identifier.value ontbreekt"
    if expected_workflow_task_identifier_value:
        assert based_on_identifier_value == expected_workflow_task_identifier_value
    else:
        _assert_urn_uuid(based_on_identifier_value)
    assert "reference" not in task["basedOn"][0]

    # --- Sender (requester.onBehalfOf) ---
    assert task["requester"]["agent"]["identifier"]["system"] == template["requester"]["agent"]["identifier"]["system"]
    assert task["requester"]["agent"]["identifier"]["value"] == sender_uzi_sys
    assert "display" not in (task["requester"]["agent"] or {})
    assert task["requester"]["onBehalfOf"]["identifier"]["system"] == template["requester"]["onBehalfOf"]["identifier"]["system"]
    assert task["requester"]["onBehalfOf"]["identifier"]["value"] == sender_ura
    assert "display" not in (task["requester"]["onBehalfOf"] or {})

    # --- Receiver ---
    assert task["owner"]["identifier"]["system"] == template["owner"]["identifier"]["system"]
    assert task["owner"]["identifier"]["value"] == receiver_ura
    assert "reference" not in (task.get("owner") or {})
    assert "display" not in (task.get("owner") or {})

    # --- Task.input ---
    template_inputs = template.get("input") or []
    task_inputs = task.get("input") or []
    assert isinstance(task_inputs, list) and len(task_inputs) == len(template_inputs), "input-lijst wijkt af"

    def _task_input_by_code(inputs: List[Dict[str, Any]], code: str) -> Optional[Dict[str, Any]]:
        for inp in inputs:
            coding = (((inp or {}).get("type") or {}).get("coding") or [])
            if not isinstance(coding, list):
                continue
            for c in coding:
                if (c or {}).get("code") == code:
                    return inp
        return None

    auth_input = _task_input_by_code(task_inputs, "authorization-base")
    assert auth_input is not None, "input authorization-base ontbreekt"
    auth_value = (auth_input or {}).get("valueString")
    assert isinstance(auth_value, str) and auth_value.strip(), "authorization-base valueString ontbreekt"
    if expected_authorization_base:
        assert auth_value == expected_authorization_base
    else:
        assert auth_value != "DYNAMIC:authorization_base", "authorization-base placeholder is niet vervangen"
        decoded = base64.b64decode(auth_value).decode()
        uuid.UUID(decoded)

    for key in ("id", "meta", "groupIdentifier", "identifier", "description", "restriction", "for", "authoredOn", "extension", "location"):
        assert key not in task, f"Veld '{key}' hoort niet aanwezig te zijn"


@dataclass
class DummyResponse:
    status_code: int
    json_data: Optional[Dict[str, Any]] = None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return dict(self.json_data or {})


class FakeHttpClient:
    """Minimal async client stub.

    Belangrijk: FastAPI lifespan sluit app.state.http_client met aclose().
    """

    def __init__(self):
        self.put_calls: List[Dict[str, Any]] = []
        self.post_calls: List[Dict[str, Any]] = []
        self.get_calls: List[Dict[str, Any]] = []

        self._next_put_response: DummyResponse = DummyResponse(200)
        self._next_post_response: DummyResponse = DummyResponse(201, {"resourceType": "Task", "id": "task-1", "status": "requested"})
        self._next_get_response: DummyResponse = DummyResponse(200, {})
        self._put_responses: List[DummyResponse] = []
        self._post_responses: List[DummyResponse] = []
        self._get_responses: List[DummyResponse] = []

    def queue_put_response(self, resp: DummyResponse) -> None:
        self._next_put_response = resp
        self._put_responses.append(resp)

    def queue_post_response(self, resp: DummyResponse) -> None:
        self._next_post_response = resp
        self._post_responses.append(resp)

    def queue_get_response(self, resp: DummyResponse) -> None:
        self._next_get_response = resp
        self._get_responses.append(resp)

    async def put(self, url: str, *, json: Any = None, headers: Optional[Dict[str, str]] = None):
        self.put_calls.append({"url": url, "json": json, "headers": headers or {}})
        if self._put_responses:
            return self._put_responses.pop(0)
        return self._next_put_response

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ):
        self.post_calls.append({"url": url, "json": json, "data": data, "headers": headers or {}, "timeout": timeout})
        if self._post_responses:
            return self._post_responses.pop(0)
        return self._next_post_response

    async def get(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ):
        self.get_calls.append({"url": url, "headers": headers or {}, "timeout": timeout, "params": params or {}})
        return self._next_get_response

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def appmod(monkeypatch):
    mod = _import_iti90_app_module()

    # Forceer test-config zodat tests onafhankelijk zijn van de CI/env.
    monkeypatch.setattr(mod.settings, "api_key", "test-api-key", raising=False)
    monkeypatch.setattr(mod.settings, "sender_ura", "12345678", raising=False)
    monkeypatch.setattr(mod.settings, "sender_name", "Huisartsenpraktijk De Vries", raising=False)
    monkeypatch.setattr(mod.settings, "sender_uzi_sys", "urn:oid:2.16.528.1.1007.3.2.1234567", raising=False)
    monkeypatch.setattr(mod.settings, "sender_system_name", "SYS EPD POC9", raising=False)
    monkeypatch.setattr(mod.settings, "sender_bgz_base", None, raising=False)
    monkeypatch.setattr(mod.settings, "sender_bgz_public_base", None, raising=False)
    monkeypatch.setattr(mod.settings, "sender_bgz_storage_base", "http://sender-storage.example/fhir", raising=False)
    monkeypatch.setattr(mod.settings, "receiver_notification_scope", "bgz-receiver", raising=False)
    monkeypatch.setattr(mod.settings, "mtls_cert_file", None, raising=False)
    monkeypatch.setattr(mod.settings, "mtls_key_file", None, raising=False)
    monkeypatch.setattr(mod.settings, "is_production", False, raising=False)
    monkeypatch.setattr(mod.settings, "allow_task_preview_in_production", True, raising=False)
    monkeypatch.setattr(mod.settings, "verify_tls", False, raising=False)
    monkeypatch.setattr(mod, "HTTPX_VERIFY", False, raising=False)

    return mod


@pytest.fixture()
def client(appmod):
    with TestClient(appmod.app, base_url="http://localhost") as c:
        yield c


def _auth_headers(appmod) -> Dict[str, str]:
    return {"X-API-Key": str(appmod.settings.api_key)}


def _capability_mapping_stub(
    *,
    target: str,
    notification_base: str = "https://receiver.example/fhir",
    endpoint_id: str = "ep-1",
    organization_ref: str = "Organization/org-owner",
    organization_display: str = "Ziekenhuis Oost",
    target_identifier: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    endpoint = {
        "id": endpoint_id,
        "address": f"{notification_base}/Task",
        "source": "target",
        "sourceRef": target,
    }
    return {
        "supported": True,
        "missing": [],
        "decision": "A",
        "decision_explanation": "Teststub met geldige notification-capability.",
        "notification": {
            "address": endpoint["address"],
            "base": notification_base,
            "endpoint_id": endpoint_id,
            "valid_http_base": True,
            "source": "target",
        },
        "bgz_fhir_server": {},
        "mapping": {
            "twiin_ta_notification": {
                "label": "Twiin TA notificatie",
                "code": "Twiin-TA-notification",
                "tokens": ["Twiin-TA-notification"],
                "required": True,
                "candidates": {
                    "target_count": 1,
                    "organization_count": 0,
                    "target": [endpoint],
                    "organization": [],
                },
                "chosen": endpoint,
            }
        },
        "organization": {"reference": organization_ref, "display": organization_display},
        "target": {
            "reference": target,
            "identifier": [target_identifier] if isinstance(target_identifier, dict) else [],
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bgz_load_data_puts_all_resources_and_updates_sender_ura(appmod, client, monkeypatch):
    template = _load_json(_data_file(appmod, "bgz-sample-bundle.json"))

    fake = FakeHttpClient()
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    r = client.post(
        "/bgz/load-data",
        params={"hapi_base": "https://receiver.example/fhir/Task/", "sender_ura": "99999999"},
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["target"] == "https://receiver.example/fhir"

    # Verwacht: 1 PUT per resource in de bundle.
    expected_resources = [
        (e.get("resource") or {}).get("resourceType")
        for e in (template.get("entry") or [])
        if (e.get("resource") or {}).get("resourceType") and (e.get("resource") or {}).get("id")
    ]
    assert body["resources_created"] == len(expected_resources)
    assert len(fake.put_calls) == len(expected_resources)

    # Specifiek: sender URA in Organization/organization-sender moet zijn aangepast.
    org_call = next(
        c
        for c in fake.put_calls
        if c["url"].endswith("/Organization/organization-sender")
    )
    identifiers = (org_call["json"] or {}).get("identifier") or []
    ura_ident = next(i for i in identifiers if i.get("system") == "http://fhir.nl/fhir/NamingSystem/ura")
    assert ura_ident.get("value") == "99999999"


def test_build_httpx_verify_adds_custom_ca_to_system_store(appmod, monkeypatch, tmp_path):
    ca_file = tmp_path / "custom-root.crt"
    ca_file.write_text("dummy-ca", encoding="utf-8")

    class DummyContext:
        def __init__(self):
            self.loaded_cafile = None

        def load_verify_locations(self, *, cafile: str):
            self.loaded_cafile = cafile

    ctx = DummyContext()
    create_default_context_calls: list[None] = []

    def _fake_create_default_context():
        create_default_context_calls.append(None)
        return ctx

    monkeypatch.setattr(appmod.settings, "verify_tls", True, raising=False)
    monkeypatch.setattr(appmod.settings, "ca_certs_file", str(ca_file), raising=False)
    monkeypatch.setattr(appmod.ssl, "create_default_context", _fake_create_default_context)

    verify = appmod._build_httpx_verify()

    assert verify is ctx
    assert len(create_default_context_calls) == 1
    assert ctx.loaded_cafile == str(ca_file)


def test_build_httpx_verify_loads_mtls_cert_chain(appmod, monkeypatch, tmp_path):
    cert_file = tmp_path / "client.pem"
    key_file = tmp_path / "client.key"
    cert_file.write_text("dummy-cert", encoding="utf-8")
    key_file.write_text("dummy-key", encoding="utf-8")

    class DummyContext:
        def __init__(self):
            self.loaded_cafile = None
            self.loaded_cert_chain = None

        def load_verify_locations(self, *, cafile: str):
            self.loaded_cafile = cafile

        def load_cert_chain(self, *, certfile: str, keyfile: str | None = None):
            self.loaded_cert_chain = (certfile, keyfile)

    ctx = DummyContext()

    monkeypatch.setattr(appmod.settings, "verify_tls", True, raising=False)
    monkeypatch.setattr(appmod.settings, "ca_certs_file", None, raising=False)
    monkeypatch.setattr(appmod.settings, "mtls_cert_file", str(cert_file), raising=False)
    monkeypatch.setattr(appmod.settings, "mtls_key_file", str(key_file), raising=False)
    monkeypatch.setattr(appmod.ssl, "create_default_context", lambda: ctx)

    verify = appmod._build_httpx_verify()

    assert verify is ctx
    assert ctx.loaded_cafile is None
    assert ctx.loaded_cert_chain == (str(cert_file), str(key_file))


def test_build_httpx_verify_rejects_mtls_key_without_cert(appmod, monkeypatch, tmp_path):
    key_file = tmp_path / "client.key"
    key_file.write_text("dummy-key", encoding="utf-8")

    monkeypatch.setattr(appmod.settings, "verify_tls", True, raising=False)
    monkeypatch.setattr(appmod.settings, "mtls_cert_file", None, raising=False)
    monkeypatch.setattr(appmod.settings, "mtls_key_file", str(key_file), raising=False)

    with pytest.raises(RuntimeError, match="MCSD_MTLS_KEY_FILE vereist ook MCSD_MTLS_CERT_FILE"):
        appmod._build_httpx_verify()


def test_bgz_preflight_location_routing_and_metadata_probe_ok(appmod, client, monkeypatch):
    async def _fake_capability_mapping(*, target: str, organization: str | None, include_oauth: bool, limit: int):
        return _capability_mapping_stub(target=target)

    # Capability mapping stub
    monkeypatch.setattr(appmod, "poc9_msz_capability_mapping", _fake_capability_mapping)

    fake = FakeHttpClient()
    fake.queue_get_response(
        DummyResponse(
            200,
            {           
                "identifier": [
                    {"system": "http://fhir.nl/fhir/NamingSystem/ura", "value": "87654321"},
                ],
                "resourceType": "CapabilityStatement",
                "rest": [
                    {
                        "resource": [
                            {"type": "Task", "interaction": [{"code": "create"}]}  # Task-create supported
                        ]
                    }
                ],
            },
        )
    )
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    r = client.post(
        "/bgz/preflight",
        json={
            "receiver_org_ref": "Organization/org-fallback",
            "receiver_target_ref": "Location/loc-1",
            "receiver_notification_endpoint_id": "ep-1",
            "check_receiver": True,
            "include_oauth": False,
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["supported"] is True
    assert body["resolved_receiver_base"] == "https://receiver.example/fhir"
    assert body["resolved_receiver_ura"] == "87654321"
    assert body["notification_endpoint_id"] == "ep-1"
    assert body["frontend_endpoint_id_match"] is True

    # Probe output
    probe = body["receiver_probe"]
    assert probe["attempted"] is True
    assert probe["ok"] is True
    assert probe["reachable"] is True
    assert probe["task_create_supported"] is True

    # Routing output
    routing = body["task_routing"]
    assert routing["target_type"] == "Location"
    assert routing["location_ref"] == "Location/loc-1"
    assert routing["owner_ref"] == "Organization/org-owner"  # Location.managingOrganization (via mapping.organization)


def test_bgz_preflight_not_ready_when_metadata_has_no_task_create(appmod, client, monkeypatch):
    async def _fake_capability_mapping(*, target: str, organization: str | None, include_oauth: bool, limit: int):
        return _capability_mapping_stub(target=target)

    monkeypatch.setattr(appmod, "poc9_msz_capability_mapping", _fake_capability_mapping)

    fake = FakeHttpClient()
    fake.queue_get_response(
        DummyResponse(
            200,
            {
                "resourceType": "Organization",
                "identifier": [
                    {
                        "system": "http://fhir.nl/fhir/NamingSystem/ura",
                        "value": "87654321",
                    }
                ],
            },
        )
    )
    # CapabilityStatement zonder Task.create
    fake.queue_get_response(
        DummyResponse(
            200,
            {
                "identifier": [
                    {"system": "http://fhir.nl/fhir/NamingSystem/ura", "value": "87654321"},
                ],
                "resourceType": "CapabilityStatement",
                "rest": [
                    {
                        "resource": [
                            {"type": "Task", "interaction": [{"code": "read"}]}
                        ]
                    }
                ],
            },
        )
    )
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    r = client.post(
        "/bgz/preflight",
        json={
            "receiver_org_ref": "Organization/org-fallback",
            "receiver_target_ref": "Location/loc-1",
            "receiver_notification_endpoint_id": "ep-1",
            "check_receiver": True,
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receiver_probe"]["attempted"] is True
    assert body["receiver_probe"]["task_create_supported"] is False

    # Verwacht: frontend krijgt duidelijk signaal dat verzenden niet kan.
    assert body["ready_to_send"] is False


def test_bgz_preflight_healthcareservice_routing_owner_ref_only(appmod, client, monkeypatch):
    async def _fake_capability_mapping(*, target: str, organization: str | None, include_oauth: bool, limit: int):
        return _capability_mapping_stub(
            target=target,
            target_identifier={
                "system": "https://sys.local/identifiers/healthcareservices",
                "value": "topicus-healthcareservice-unit-1",
            },
        )

    monkeypatch.setattr(appmod, "poc9_msz_capability_mapping", _fake_capability_mapping)

    fake = FakeHttpClient()
    fake.queue_get_response(
        DummyResponse(
            200,
            {
                "identifier": [
                    {"system": "http://fhir.nl/fhir/NamingSystem/ura", "value": "87654321"},
                ],
                "resourceType": "CapabilityStatement",
                "rest": [],
            },
        )
    )
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    r = client.post(
        "/bgz/preflight",
        json={
            "receiver_target_ref": "HealthcareService/hs-1",
            "check_receiver": True,
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    routing = body["task_routing"]
    assert routing["target_type"] == "HealthcareService"
    assert routing["owner_ref"] == "Organization/org-owner"
    assert routing["location_ref"] is None
    assert routing["extension_healthcareservice_ref"] == "HealthcareService/hs-1"
    assert routing["extension_healthcareservice_identifier_value"] == "topicus-healthcareservice-unit-1"


def test_bgz_task_preview_location_routing_builds_task_from_template(appmod, client, monkeypatch):
    template = _load_json(_data_file(appmod, "notification-task.json"))

    async def _fake_resolve(*, receiver_target_ref: str, receiver_org_ref: str | None, receiver_notification_endpoint_id: str | None):
        return (
            {
                "organization": {"reference": "Organization/org-owner", "display": "Ziekenhuis Oost"},
            },
            "https://receiver.example/fhir",
            "ep-1",
            "Location/loc-1",
            "Organization/org-fallback",
            "Location",
            "87654321",
        )

    monkeypatch.setattr(appmod, "_resolve_bgz_notify_destination", _fake_resolve)

    r = client.post(
        "/bgz/task-preview",
        json={
            "receiver_ura": "87654321",
            "receiver_name": "Ziekenhuis Oost - Cardiologie",
            "receiver_org_ref": "Organization/org-fallback",
            "receiver_org_name": "Ziekenhuis Oost",
            "receiver_target_ref": "Location/loc-1",
            "receiver_notification_endpoint_id": "ep-1",
            "patient_bsn": "999999990",
            "patient_name": "Test Patient",
            "description": "BgZ beschikbaar voor test (locatie)",
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["resolved_receiver_base"] == "https://receiver.example/fhir"
    assert body["notification_endpoint_id"] == "ep-1"
    assert isinstance(body.get("workflow_task_id"), str)
    assert body["workflow_task_id"].strip()
    assert body["workflow_task_identifier_system"] == "urn:ietf:rfc:3986"
    _assert_urn_uuid(body["workflow_task_identifier_value"])
    assert body["authorization_base"]

    task = body["task"]
    assert_task_matches_notification_template(
        task=task,
        template=template,
        sender_ura="12345678",
        sender_uzi_sys="urn:oid:2.16.528.1.1007.3.2.1234567",
        receiver_ura="87654321",
        expected_workflow_task_identifier_value=body["workflow_task_identifier_value"],
        expected_authorization_base=body["authorization_base"],
    )


def test_bgz_task_preview_healthcareservice_routing_builds_task_from_template(appmod, client, monkeypatch):
    template = _load_json(_data_file(appmod, "notification-task.json"))

    async def _fake_resolve(*, receiver_target_ref: str, receiver_org_ref: str | None, receiver_notification_endpoint_id: str | None):
        return (
            {
                "organization": {"reference": "Organization/org-owner", "display": "Ziekenhuis Oost"},
            },
            "https://receiver.example/fhir",
            "ep-1",
            "HealthcareService/hs-1",
            "Organization/org-fallback",
            "HealthcareService",
            "87654321",
        )

    monkeypatch.setattr(appmod, "_resolve_bgz_notify_destination", _fake_resolve)

    r = client.post(
        "/bgz/task-preview",
        json={
            "receiver_ura": "87654321",
            "receiver_name": "Ziekenhuis Oost - Cardiologie",
            "receiver_org_ref": "Organization/org-fallback",
            "receiver_target_ref": "HealthcareService/hs-1",
            "receiver_notification_endpoint_id": "ep-1",
            "patient_bsn": "999999990",
            "patient_name": "Test Patient",
            "description": "BgZ beschikbaar voor test (service)",
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("workflow_task_id"), str)
    assert body["workflow_task_id"].strip()
    assert body["workflow_task_identifier_system"] == "urn:ietf:rfc:3986"
    _assert_urn_uuid(body["workflow_task_identifier_value"])
    assert body["authorization_base"]
    task = body["task"]

    assert_task_matches_notification_template(
        task=task,
        template=template,
        sender_ura="12345678",
        sender_uzi_sys="urn:oid:2.16.528.1.1007.3.2.1234567",
        receiver_ura="87654321",
        expected_workflow_task_identifier_value=body["workflow_task_identifier_value"],
        expected_authorization_base=body["authorization_base"],
    )



def test_bgz_task_preview_generates_workflow_task_id_when_missing(appmod, client, monkeypatch):
    template = _load_json(_data_file(appmod, "notification-task.json"))

    async def _fake_resolve(*, receiver_target_ref: str, receiver_org_ref: str | None, receiver_notification_endpoint_id: str | None):
        return (
            {
                "organization": {"reference": "Organization/org-owner", "display": "Ziekenhuis Oost"},
            },
            "https://receiver.example/fhir",
            "ep-1",
            "Organization/org-1",
            "Organization/org-1",
            "Organization",
            "87654321",
        )

    monkeypatch.setattr(appmod, "_resolve_bgz_notify_destination", _fake_resolve)

    r = client.post(
        "/bgz/task-preview",
        json={
            "receiver_ura": "87654321",
            "receiver_name": "Ziekenhuis Oost",
            "receiver_org_ref": "Organization/org-1",
            "receiver_target_ref": "Organization/org-1",
            "receiver_notification_endpoint_id": "ep-1",
            "patient_bsn": "999999990",
            "patient_name": "Test Patient",
            "description": "BgZ beschikbaar voor test (preview - generated workflow id)",
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert isinstance(body.get("workflow_task_id"), str)
    assert body["workflow_task_id"].strip()
    uuid.UUID(body["workflow_task_id"])
    assert body["workflow_task_identifier_system"] == "urn:ietf:rfc:3986"
    _assert_urn_uuid(body["workflow_task_identifier_value"])
    assert body["authorization_base"]

    task = body["task"]
    assert_task_matches_notification_template(
        task=task,
        template=template,
        sender_ura="12345678",
        sender_uzi_sys="urn:oid:2.16.528.1.1007.3.2.1234567",
        receiver_ura="87654321",
        expected_workflow_task_identifier_value=body["workflow_task_identifier_value"],
        expected_authorization_base=body["authorization_base"],
    )


@pytest.mark.parametrize(
    ("receiver_target_ref_norm", "receiver_target_identifiers", "target_type", "expected_healthcareservice_identifier", "expected_location_identifier"),
    [
        ("Organization/org-1", None, "Organization", None, None),
        (
            "HealthcareService/hs-1",
            [{"system": "https://sys.local/identifiers/healthcareservices", "value": "topicus-healthcareservice-unit-1"}],
            "HealthcareService",
            {"system": "https://sys.local/identifiers/healthcareservices", "value": "topicus-healthcareservice-unit-1"},
            None,
        ),
        (
            "Location/loc-1",
            [{"system": "https://sys.local/identifiers/locations", "value": "topicus-location-unit-1"}],
            "Location",
            None,
            {"system": "https://sys.local/identifiers/locations", "value": "topicus-location-unit-1"},
        ),
    ],
)
def test_build_bgz_workflow_task_populates_target_identifier_extensions(
    appmod,
    receiver_target_ref_norm: str,
    receiver_target_identifiers: Optional[List[Dict[str, Any]]],
    target_type: str,
    expected_healthcareservice_identifier: Optional[Dict[str, Any]],
    expected_location_identifier: Optional[Dict[str, Any]],
):
    task = appmod._build_bgz_workflow_task(
        workflow_task_id="wf-777",
        workflow_task_identifier_value="urn:uuid:11111111-1111-1111-1111-111111111111",
        group_identifier="urn:uuid:22222222-2222-2222-2222-222222222222",
        authorization_base="auth-base-123",
        sender_ura="12345678",
        sender_name="Huisartsenpraktijk De Vries",
        sender_uzi_sys="urn:oid:2.16.528.1.1007.3.2.1234567",
        sender_system_name="SYS EPD POC9",
        receiver_ura="87654321",
        receiver_target_ref_norm=receiver_target_ref_norm,
        receiver_target_identifiers=receiver_target_identifiers,
        target_type=target_type,
        patient_bsn="999999990",
        patient_name="Test Patient",
        description="BgZ beschikbaar voor test (workflow target extension)",
    )

    _assert_workflow_task_target_extensions(
        appmod,
        task,
        expected_healthcareservice_identifier=expected_healthcareservice_identifier,
        expected_location_identifier=expected_location_identifier,
    )
    dumped = json.dumps(task)
    assert "DYNAMIC:receiver_healthcareservice_id" not in dumped
    assert "DYNAMIC:receiver_location_id" not in dumped


def test_bgz_task_preview_rejects_non_urn_sender_software_identifier(appmod, client, monkeypatch):
    monkeypatch.setattr(appmod.settings, "sender_uzi_sys", "00009876543", raising=False)

    async def _fake_resolve(*, receiver_target_ref: str, receiver_org_ref: str | None, receiver_notification_endpoint_id: str | None):
        return (
            {
                "organization": {"reference": "Organization/org-1", "display": "Ziekenhuis Oost"},
            },
            "https://receiver.example/fhir",
            "ep-1",
            "Organization/org-1",
            "Organization/org-1",
            "Organization",
            "87654321",
        )

    monkeypatch.setattr(appmod, "_resolve_bgz_notify_destination", _fake_resolve)

    response = client.post(
        "/bgz/task-preview",
        json={
            "receiver_ura": "87654321",
            "receiver_name": "Ziekenhuis Oost",
            "receiver_org_ref": "Organization/org-1",
            "receiver_target_ref": "Organization/org-1",
            "receiver_notification_endpoint_id": "ep-1",
            "patient_bsn": "999999990",
        },
        headers=_auth_headers(appmod),
    )

    assert response.status_code == 500
    payload = response.json()
    detail = payload.get("detail", payload)
    assert detail["message"] == "MCSD_SENDER_UZI_SYS moet een RFC3986 URN zijn (urn:oid:... of urn:uuid:...)."


def test_bgz_notify_posts_task_and_returns_result(appmod, client, monkeypatch):
    template = _load_json(_data_file(appmod, "notification-task.json"))

    async def _fake_resolve(*, receiver_target_ref: str, receiver_org_ref: str | None, receiver_notification_endpoint_id: str | None):
        return (
            {
                "organization": {"reference": "Organization/org-1", "display": "Ziekenhuis Oost"},
                "mapping": {
                    "nuts_oauth": {
                        "chosen": {
                            "address": "https://receiver.example/nuts-oauth2",
                        }
                    }
                },
            },
            "https://receiver.example/fhir",
            "ep-1",
            "Organization/org-1",
            "Organization/org-1",
            "Organization",
            "87654321",
        )

    monkeypatch.setattr(appmod, "_resolve_bgz_notify_destination", _fake_resolve)

    fake = FakeHttpClient()
    fake.queue_post_response(DummyResponse(200, {"access_token": "receiver-token"}))
    fake.queue_post_response(DummyResponse(201, {"resourceType": "Task", "id": "task-123", "status": "requested"}))
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    r = client.post(
        "/bgz/notify",
        json={
            "receiver_ura": "87654321",
            "receiver_name": "Ziekenhuis Oost",
            "receiver_org_ref": "Organization/org-1",
            "receiver_target_ref": "Organization/org-1",
            "receiver_notification_endpoint_id": "ep-1",
            "patient_bsn": "999999990",
            "patient_name": "Test Patient",
            "description": "BgZ beschikbaar voor test (notify)",
            "workflow_task_id": "wf-777",
        },
        headers=_auth_headers(appmod),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["success"] is True
    assert body["target"] == "https://receiver.example/fhir/Task"
    assert body["resolved_receiver_base"] == "https://receiver.example/fhir"
    assert body["sender_bgz_base"] is None
    assert body["task_id"] == "task-123"
    assert body["task_status"] == "requested"
    assert body.get("workflow_task_id") == "wf-777"
    assert body["workflow_task_identifier_system"] == "urn:ietf:rfc:3986"
    _assert_urn_uuid(body["workflow_task_identifier_value"])
    assert body["authorization_base"]

    assert len(fake.post_calls) == 2
    token_post = fake.post_calls[0]
    assert token_post["url"] == "http://nuts-node:8083/internal/auth/v2/00700700/request-service-access-token"
    assert token_post["json"] == {
        "authorization_server": "https://receiver.example/nuts-oauth2/oauth2/87654321",
        "scope": "bgz-receiver",
        "token_type": "Bearer",
    }

    post = fake.post_calls[1]
    assert post["url"] == "https://receiver.example/fhir/Task"
    assert post["headers"].get("Content-Type") == "application/fhir+json"
    assert post["headers"].get("Authorization") == "Bearer receiver-token"

    sent_task = post["json"]
    assert_task_matches_notification_template(
        task=sent_task,
        template=template,
        sender_ura="12345678",
        sender_uzi_sys="urn:oid:2.16.528.1.1007.3.2.1234567",
        receiver_ura="87654321",
        expected_workflow_task_identifier_value=body["workflow_task_identifier_value"],
        expected_authorization_base=body["authorization_base"],
    )


def test_bgz_notify_uses_public_base_and_storage_base_and_persists_authorization_base(appmod, client, monkeypatch):
    async def _fake_resolve(*, receiver_target_ref: str, receiver_org_ref: str | None, receiver_notification_endpoint_id: str | None):
        return (
            {
                "organization": {"reference": "Organization/org-1", "display": "Ziekenhuis Oost"},
                "mapping": {
                    "nuts_oauth": {
                        "chosen": {
                            "address": "https://receiver.example/nuts-oauth2",
                        }
                    }
                },
            },
            "https://receiver.example/fhir",
            "ep-1",
            "Organization/org-1",
            "Organization/org-1",
            "Organization",
            "87654321",
        )

    monkeypatch.setattr(appmod, "_resolve_bgz_notify_destination", _fake_resolve)
    monkeypatch.setattr(appmod.settings, "sender_bgz_public_base", "https://sender.example/notifiedpull/fhir", raising=False)
    monkeypatch.setattr(appmod.settings, "sender_bgz_storage_base", "http://hapi-notifiedpull-stu3:8082/fhir", raising=False)
    monkeypatch.setattr(appmod.settings, "sender_bgz_base", None, raising=False)

    fake = FakeHttpClient()
    fake.queue_put_response(DummyResponse(200, {"resourceType": "Task", "id": "wf-777", "status": "requested"}))
    fake.queue_post_response(DummyResponse(200, {"access_token": "receiver-token"}))
    fake.queue_post_response(DummyResponse(201, {"resourceType": "Task", "id": "task-123", "status": "requested"}))
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    response = client.post(
        "/bgz/notify",
        json={
            "receiver_ura": "87654321",
            "receiver_name": "Ziekenhuis Oost",
            "receiver_org_ref": "Organization/org-1",
            "receiver_target_ref": "Organization/org-1",
            "receiver_notification_endpoint_id": "ep-1",
            "patient_bsn": "999999990",
            "patient_name": "Test Patient",
            "description": "BgZ beschikbaar voor test (notify split bases)",
            "workflow_task_id": "wf-777",
        },
        headers=_auth_headers(appmod),
    )
    assert response.status_code == 200, response.text

    assert len(fake.put_calls) == 1
    put_call = fake.put_calls[0]
    assert put_call["url"] == "http://hapi-notifiedpull-stu3:8082/fhir/Task/wf-777"

    workflow_task = put_call["json"]
    workflow_identifier = _find_task_identifier(workflow_task, "urn:ietf:rfc:3986")
    assert workflow_identifier is not None
    _assert_urn_uuid(workflow_identifier["value"])

    auth_input = _find_task_input(workflow_task, "authorization-base")
    assert auth_input is not None
    assert isinstance(auth_input["valueString"], str) and auth_input["valueString"]
    assert _find_task_identifier(workflow_task, "https://sys.local/fhir/NamingSystem/task-authorization-base") is None

    assert len(fake.post_calls) == 2
    assert fake.post_calls[0]["url"] == "http://nuts-node:8083/internal/auth/v2/00700700/request-service-access-token"

    notification_task = fake.post_calls[1]["json"]
    assert fake.post_calls[1]["headers"].get("Authorization") == "Bearer receiver-token"
    sender_bgz_ext = _find_task_extension(notification_task, "http://example.org/fhir/StructureDefinition/sender-bgz-base")
    assert sender_bgz_ext is None
    assert response.json()["sender_bgz_base"] == "https://sender.example/notifiedpull/fhir"
    assert notification_task["basedOn"][0]["identifier"]["value"] == workflow_identifier["value"]
    notification_auth_input = _find_task_input(notification_task, "authorization-base")
    assert notification_auth_input is not None
    assert notification_auth_input["valueString"] == auth_input["valueString"]
