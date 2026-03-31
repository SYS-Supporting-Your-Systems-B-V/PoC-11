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


def assert_task_matches_notification_template(
    *,
    task: Dict[str, Any],
    template: Dict[str, Any],
    sender_ura: str,
    sender_name: str,
    sender_uzi_sys: str,
    sender_system_name: str,
    sender_uzi_sys: str,
    sender_system_name: str,
    receiver_ura: str,
    receiver_name: str,
    patient_bsn: str,
    patient_name: Optional[str],
    description: Optional[str],
    expected_owner_ref: Optional[str],
    expected_owner_display: Optional[str],
    expected_location_ref: Optional[str],
    expected_location_display: Optional[str],
    expected_extension_location_ref: Optional[str] = None,
    expected_extension_location_display: Optional[str] = None,
    expected_extension_healthcareservice_ref: Optional[str] = None,
    expected_extension_healthcareservice_display: Optional[str] = None,
    expected_workflow_task_id: Optional[str],
) -> None:
    """Asserties voor de gegenereerde BgZ Task op basis van notification-task.json.

    Doel:
    - vaste velden moeten gelijk blijven aan de template (profile, code, input, etc.)
    - dynamische velden worden op vorm/inhoud gevalideerd (UUIDs, datetimes, routing refs)
    """

    # --- Ongewijzigd t.o.v. template ---
    for key in ("resourceType", "id", "meta", "status", "intent", "code"):
        assert task.get(key) == template.get(key), f"Veld '{key}' wijkt af van template"

    # --- groupIdentifier / identifier: UUID URNs maar met zelfde system ---
    assert task["groupIdentifier"]["system"] == template["groupIdentifier"]["system"]
    _assert_urn_uuid(task["groupIdentifier"]["value"])

    assert isinstance(task.get("identifier"), list) and task["identifier"], "identifier ontbreekt"
    assert task["identifier"][0]["system"] == template["identifier"][0]["system"]
    _assert_urn_uuid(task["identifier"][0]["value"])

    # --- basedOn: Workflow Task reference (PoC 9 / TA Notified Pull) ---
    assert isinstance(task.get("basedOn"), list) and task["basedOn"], "basedOn ontbreekt"
    assert isinstance(task["basedOn"][0], dict), "basedOn[0] moet een dict zijn"
    based_on_ref = task["basedOn"][0].get("reference")
    assert isinstance(based_on_ref, str) and based_on_ref.strip(), "basedOn[0].reference ontbreekt"

    if expected_workflow_task_id:
        wf = str(expected_workflow_task_id).strip()
        if "://" in wf or wf.startswith("Task/"):
            expected_ref = wf
        else:
            expected_ref = f"Task/{wf}"
        assert based_on_ref == expected_ref, f"basedOn.reference wijkt af ({based_on_ref} != {expected_ref})"
    else:
        assert based_on_ref.startswith("Task/"), based_on_ref
        uuid.UUID(based_on_ref.split("Task/", 1)[1])

    # --- authoredOn / restriction.end: iso datetimes & ~365 dagen window ---
    authored = _parse_dt(task["authoredOn"])
    end = _parse_dt(task["restriction"]["period"]["end"])
    assert end > authored
    delta_days = (end - authored).days
    assert 364 <= delta_days <= 366, f"restriction.end lijkt geen 365 dagen na authoredOn ({delta_days} dagen)"

    # --- Sender (requester.onBehalfOf) ---
    assert task["requester"]["agent"]["identifier"]["system"] == template["requester"]["agent"]["identifier"]["system"]
    assert task["requester"]["agent"]["identifier"]["value"] == sender_uzi_sys
    assert task["requester"]["agent"]["display"] == sender_system_name
    assert task["requester"]["onBehalfOf"]["identifier"]["system"] == template["requester"]["onBehalfOf"]["identifier"]["system"]
    assert task["requester"]["onBehalfOf"]["identifier"]["value"] == sender_ura
    assert task["requester"]["onBehalfOf"]["display"] == sender_name

    # --- Receiver (owner.identifier + display) ---
    assert task["owner"]["identifier"]["system"] == template["owner"]["identifier"]["system"]
    assert task["owner"]["identifier"]["value"] == receiver_ura
    assert task["owner"]["display"] == (expected_owner_display or receiver_name)

    if expected_owner_ref is None:
        assert "reference" not in (task.get("owner") or {}), "owner.reference hoort niet aanwezig te zijn"
    else:
        assert task["owner"]["reference"] == expected_owner_ref

    # --- Location routing ---
    if expected_location_ref is None:
        assert "location" not in task, "Task.location hoort niet aanwezig te zijn"
    else:
        assert task["location"]["reference"] == expected_location_ref
        if expected_location_display is not None:
            assert task["location"].get("display") == expected_location_display

    location_extension = _find_task_extension(
        task,
        "http://nuts-foundation.github.io/nl-generic-functions-ig/StructureDefinition/task-stu3-location",
    )
    if expected_extension_location_ref is None:
        assert location_extension is None, "task-stu3-location hoort niet aanwezig te zijn"
    else:
        assert location_extension is not None, "task-stu3-location ontbreekt"
        assert location_extension["valueReference"]["reference"] == expected_extension_location_ref
        if expected_extension_location_display is not None:
            assert location_extension["valueReference"].get("display") == expected_extension_location_display

    healthcare_service_extension = _find_task_extension(
        task,
        "http://nuts-foundation.github.io/nl-generic-functions-ig/StructureDefinition/task-stu3-healthcareservice",
    )
    if expected_extension_healthcareservice_ref is None:
        assert healthcare_service_extension is None, "task-stu3-healthcareservice hoort niet aanwezig te zijn"
    else:
        assert healthcare_service_extension is not None, "task-stu3-healthcareservice ontbreekt"
        assert healthcare_service_extension["valueReference"]["reference"] == expected_extension_healthcareservice_ref
        if expected_extension_healthcareservice_display is not None:
            assert healthcare_service_extension["valueReference"].get("display") == expected_extension_healthcareservice_display

    # --- Patient (Task.for) ---
    assert task["for"]["identifier"]["system"] == template["for"]["identifier"]["system"]
    assert task["for"]["identifier"]["value"] == patient_bsn
    if patient_name:
        assert task["for"].get("display") == patient_name
    else:
        assert "display" not in task.get("for", {}), "for.display hoort niet aanwezig te zijn als patient_name leeg is"

    # --- Description ---
    if description:
        assert task.get("description") == description
    else:
        assert "description" not in task

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
    assert auth_value != "DYNAMIC:authorization_base", "authorization-base placeholder is niet vervangen"
    decoded = base64.b64decode(auth_value).decode()
    uuid.UUID(decoded)

    get_wf_input = _task_input_by_code(task_inputs, "get-workflow-task")
    assert get_wf_input is not None, "input get-workflow-task ontbreekt"
    assert get_wf_input.get("valueBoolean") is True


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
    monkeypatch.setattr(mod.settings, "sender_uzi_sys", "00009876543", raising=False)
    monkeypatch.setattr(mod.settings, "sender_system_name", "SYS EPD POC9", raising=False)
    monkeypatch.setattr(mod.settings, "sender_bgz_base", None, raising=False)
    monkeypatch.setattr(mod.settings, "sender_bgz_public_base", None, raising=False)
    monkeypatch.setattr(mod.settings, "sender_bgz_storage_base", "http://sender-storage.example/fhir", raising=False)
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
        "target": {"reference": target},
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
        return _capability_mapping_stub(target=target)

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

    task = body["task"]
    assert_task_matches_notification_template(
        task=task,
        template=template,
        sender_ura="12345678",
        sender_name="Huisartsenpraktijk De Vries",
        sender_uzi_sys="00009876543",
        sender_system_name="SYS EPD POC9",
        receiver_ura="87654321",
        receiver_name="Ziekenhuis Oost - Cardiologie",
        patient_bsn="999999990",
        patient_name="Test Patient",
        description="BgZ beschikbaar voor test (locatie)",
        expected_owner_ref="Organization/org-owner",
        expected_owner_display="Ziekenhuis Oost",
        expected_location_ref=None,
        expected_location_display=None,
        expected_extension_location_ref=None,
        expected_extension_location_display=None,
        expected_workflow_task_id=body["workflow_task_id"],
    )
    ext = next(
        e for e in (task.get("extension") or [])
        if (e or {}).get("url") == "http://nuts-foundation.github.io/nl-generic-functions-ig/StructureDefinition/task-stu3-location"
    )
    assert ext["valueReference"]["reference"] == "Location/loc-1"
    assert ext["valueReference"]["display"] == "Ziekenhuis Oost - Cardiologie"


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
    task = body["task"]

    assert_task_matches_notification_template(
        task=task,
        template=template,
        sender_ura="12345678",
        sender_name="Huisartsenpraktijk De Vries",
        sender_uzi_sys="00009876543",
        sender_system_name="SYS EPD POC9",
        receiver_ura="87654321",
        receiver_name="Ziekenhuis Oost - Cardiologie",
        patient_bsn="999999990",
        patient_name="Test Patient",
        description="BgZ beschikbaar voor test (service)",
        expected_owner_ref="Organization/org-owner",
        expected_owner_display="Ziekenhuis Oost",
        expected_location_ref=None,
        expected_location_display=None,
        expected_extension_healthcareservice_ref="HealthcareService/hs-1",
        expected_extension_healthcareservice_display="Ziekenhuis Oost - Cardiologie",
        expected_workflow_task_id=body["workflow_task_id"],
    )
    ext = next(
        e for e in (task.get("extension") or [])
        if (e or {}).get("url") == "http://nuts-foundation.github.io/nl-generic-functions-ig/StructureDefinition/task-stu3-healthcareservice"
    )
    assert ext["valueReference"]["reference"] == "HealthcareService/hs-1"
    assert ext["valueReference"]["display"] == "Ziekenhuis Oost - Cardiologie"



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

    task = body["task"]
    assert_task_matches_notification_template(
        task=task,
        template=template,
        sender_ura="12345678",
        sender_name="Huisartsenpraktijk De Vries",
        sender_uzi_sys="00009876543",
        sender_system_name="SYS EPD POC9",
        receiver_ura="87654321",
        receiver_name="Ziekenhuis Oost",
        patient_bsn="999999990",
        patient_name="Test Patient",
        description="BgZ beschikbaar voor test (preview - generated workflow id)",
        expected_owner_ref="Organization/org-1",
        expected_owner_display="Ziekenhuis Oost",
        expected_location_ref=None,
        expected_location_display=None,
        expected_workflow_task_id=body["workflow_task_id"],
    )

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
    assert body["sender_bgz_base"] == "https://sender.example/fhir"
    assert body["task_id"] == "task-123"
    assert body["task_status"] == "requested"
    assert body.get("workflow_task_id") == "wf-777"

    assert len(fake.post_calls) == 2
    token_post = fake.post_calls[0]
    assert token_post["url"] == "http://nuts-node:8083/internal/auth/v2/12345678/request-service-access-token"
    assert token_post["json"] == {
        "authorization_server": "https://receiver.example/nuts-oauth2",
        "scope": "eOverdracht-receiver",
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
        sender_name="Huisartsenpraktijk De Vries",
        sender_uzi_sys="00009876543",
        sender_system_name="SYS EPD POC9",
        receiver_ura="87654321",
        receiver_name="Ziekenhuis Oost",
        patient_bsn="999999990",
        patient_name="Test Patient",
        description="BgZ beschikbaar voor test (notify)",
        expected_owner_ref="Organization/org-1",
        expected_owner_display="Ziekenhuis Oost",
        expected_location_ref=None,
        expected_location_display=None,
        expected_workflow_task_id="wf-777",
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
    auth_identifier = _find_task_identifier(
        workflow_task,
        "https://sys.local/fhir/NamingSystem/task-authorization-base",
    )
    assert auth_identifier is not None
    assert auth_identifier["value"]

    auth_input = _find_task_input(workflow_task, "authorization-base")
    assert auth_input is not None
    assert auth_input["valueString"] == auth_identifier["value"]

    assert len(fake.post_calls) == 2
    assert fake.post_calls[0]["url"] == "http://nuts-node:8083/internal/auth/v2/12345678/request-service-access-token"

    notification_task = fake.post_calls[1]["json"]
    assert fake.post_calls[1]["headers"].get("Authorization") == "Bearer receiver-token"
    sender_bgz_ext = _find_task_extension(
        notification_task,
        "http://example.org/fhir/StructureDefinition/sender-bgz-base",
    )
    assert sender_bgz_ext is not None
    assert sender_bgz_ext["valueUrl"] == "https://sender.example/notifiedpull/fhir"
