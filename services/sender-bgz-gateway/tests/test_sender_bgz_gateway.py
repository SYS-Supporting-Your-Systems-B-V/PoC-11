import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient


def _import_app_module():
    module_name = "sender_bgz_gateway_main_test"
    sys.modules.pop(module_name, None)
    sys.modules.pop("configs", None)
    service_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(service_root))
    module_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(service_root):
            sys.path.pop(0)
    return module


@dataclass
class DummyResponse:
    status_code: int
    json_data: Optional[Dict[str, Any]] = None
    headers: Dict[str, str] = field(default_factory=lambda: {"content-type": "application/fhir+json"})
    raw_content: bytes = b""

    @property
    def content(self) -> bytes:
        if self.json_data is not None:
            return json.dumps(self.json_data).encode("utf-8")
        return self.raw_content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Dict[str, Any]:
        return dict(self.json_data or {})


class FakeHttpClient:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self._queue: List[tuple[str, str, DummyResponse]] = []

    def queue(self, method: str, url: str, response: DummyResponse) -> None:
        self._queue.append((method.upper(), url, response))

    def _next(self, method: str, url: str, **kwargs: Any) -> DummyResponse:
        self.calls.append({"method": method.upper(), "url": url, **kwargs})
        if not self._queue:
            raise AssertionError(f"No queued response left for {method} {url}")
        expected_method, expected_url, response = self._queue.pop(0)
        assert expected_method == method.upper()
        assert expected_url == url
        return response

    async def get(self, url: str, *, params: Any = None, headers: Optional[Dict[str, str]] = None, timeout: Any = None):
        return self._next("GET", url, params=params, headers=headers or {}, timeout=timeout)

    async def post(self, url: str, *, data: Any = None, headers: Optional[Dict[str, str]] = None, timeout: Any = None):
        return self._next("POST", url, data=data, headers=headers or {}, timeout=timeout)

    async def put(self, url: str, *, json: Any = None, headers: Optional[Dict[str, str]] = None):
        return self._next("PUT", url, json=json, headers=headers or {})

    async def aclose(self) -> None:
        return None


def _workflow_task(appmod, *, status: str = "requested") -> Dict[str, Any]:
    return {
        "resourceType": "Task",
        "id": "wf-1",
        "status": status,
        "owner": {
            "identifier": {
                "system": "http://fhir.nl/fhir/NamingSystem/ura",
                "value": "87654321",
            }
        },
        "for": {
            "identifier": {
                "system": appmod.settings.patient_identifier_system,
                "value": "999999990",
            }
        },
        "identifier": [
            {
                "system": "urn:ietf:rfc:3986",
                "value": "urn:uuid:11111111-1111-1111-1111-111111111111",
            },
            {
                "system": appmod.settings.authorization_base_system,
                "value": "auth-123",
            }
        ],
        "input": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://fhir.nl/fhir/NamingSystem/TaskParameter",
                            "code": "authorization-base",
                        }
                    ]
                },
                "valueString": "auth-123",
            }
        ],
    }


def _bundle(*resources: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": resource} for resource in resources],
    }


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _introspection_payload(**overrides: Any) -> Dict[str, Any]:
    base = {
        "active": True,
        "organization_ura": "87654321",
        "authorization-base": "auth-123",
        "employee_identifier": "dezi-001",
        "employee_roles": ["doctor"],
        "scope": "eOverdracht-sender",
    }
    base.update(overrides)
    return base


def _patient(patient_id: str = "patient-1") -> Dict[str, Any]:
    return {
        "resourceType": "Patient",
        "id": patient_id,
        "identifier": [
            {
                "system": "http://fhir.nl/fhir/NamingSystem/bsn",
                "value": "999999990",
            }
        ],
        "generalPractitioner": [{"reference": "Practitioner/pr-1"}],
    }


def _practitioner() -> Dict[str, Any]:
    return {"resourceType": "Practitioner", "id": "pr-1"}


def _other_patient() -> Dict[str, Any]:
    return {
        "resourceType": "Patient",
        "id": "patient-2",
        "identifier": [
            {
                "system": "http://fhir.nl/fhir/NamingSystem/bsn",
                "value": "111111111",
            }
        ],
    }


def _bundle_with_include(primary: Dict[str, Any], include: Dict[str, Any], extra_primary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 2,
        "entry": [
            {"resource": primary, "search": {"mode": "match"}},
            {"resource": include, "search": {"mode": "include"}},
            {"resource": extra_primary, "search": {"mode": "match"}},
        ],
    }


def _set_gateway_settings(monkeypatch, appmod) -> None:
    monkeypatch.setattr(appmod.settings, "upstream_fhir_base", "http://upstream/fhir", raising=False)
    monkeypatch.setattr(appmod.settings, "nuts_internal_base", "http://nuts-node:8083", raising=False)
    monkeypatch.setattr(
        appmod.settings,
        "authorization_base_system",
        "https://sys.local/fhir/NamingSystem/task-authorization-base",
        raising=False,
    )
    monkeypatch.setattr(
        appmod.settings,
        "patient_identifier_system",
        "http://fhir.nl/fhir/NamingSystem/bsn",
        raising=False,
    )
    monkeypatch.setattr(
        appmod.settings,
        "medical_role_valueset_url",
        "https://decor.example/value-sets/rolecodes",
        raising=False,
    )
    monkeypatch.setattr(appmod, "MEDICAL_ROLE_CODES", ["doctor"], raising=False)
    monkeypatch.setattr(appmod, "REQUIRED_SCOPES", [], raising=False)


def test_health(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    with TestClient(appmod.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["medical_role_valueset_url"] == "https://decor.example/value-sets/rolecodes"
    assert response.json()["medical_role_allowlist_configured"] is True


def test_task_read_authorized(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload()),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))
    fake.queue("GET", "http://upstream/fhir/Task/wf-1", DummyResponse(200, _workflow_task(appmod)))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get("/fhir/Task/wf-1", headers=_auth_headers())

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "wf-1"
    assert fake.calls[1]["params"] == [
        ("identifier", "https://sys.local/fhir/NamingSystem/task-authorization-base|auth-123"),
        ("_count", "5"),
    ]


def test_task_read_accepts_authorization_base_header_fallback(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload(**{"authorization-base": ""})),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))
    fake.queue("GET", "http://upstream/fhir/Task/wf-1", DummyResponse(200, _workflow_task(appmod)))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get(
            "/fhir/Task/wf-1",
            headers={**_auth_headers(), "X-Authorization-Base": "auth-123"},
        )

    assert response.status_code == 200, response.text
    assert fake.calls[1]["params"] == [
        ("identifier", "https://sys.local/fhir/NamingSystem/task-authorization-base|auth-123"),
        ("_count", "5"),
    ]


def test_task_read_accepts_subject_id_fallback_for_organization(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(
            200,
            _introspection_payload(
                organization_ura="00000000",
                iss="https://sender.example/nuts-oauth2/oauth2/87654321",
            ),
        ),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))
    fake.queue("GET", "http://upstream/fhir/Task/wf-1", DummyResponse(200, _workflow_task(appmod)))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get("/fhir/Task/wf-1", headers=_auth_headers())

    assert response.status_code == 200, response.text


def test_task_read_rejects_authorization_base_header_mismatch(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload(**{"authorization-base": "auth-123"})),
    )

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get(
            "/fhir/Task/wf-1",
            headers={**_auth_headers(), "X-Authorization-Base": "auth-999"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "authorization_base_mismatch"


def test_task_search_authorized_by_identifier(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload()),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get(
            "/fhir/Task?identifier=urn:ietf:rfc:3986|urn:uuid:11111111-1111-1111-1111-111111111111",
            headers=_auth_headers(),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["total"] == 1
    assert body["entry"][0]["resource"]["id"] == "wf-1"


def test_task_search_returns_empty_bundle_for_identifier_mismatch(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload()),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get(
            "/fhir/Task?identifier=urn:ietf:rfc:3986|urn:uuid:22222222-2222-2222-2222-222222222222",
            headers=_auth_headers(),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["total"] == 0
    assert body["entry"] == []


def test_task_search_requires_identifier_query(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)

    with TestClient(appmod.app) as client:
        response = client.get("/fhir/Task", headers=_auth_headers())

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "missing_task_identifier"


def test_patient_search_requires_employee_claims_from_introspection(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload(employee_identifier="", employee_roles=[])),
    )

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get(
            "/fhir/Patient",
            headers={**_auth_headers(), "X-Employee-Identifier": "dezi-001", "X-Employee-Roles": "doctor"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "missing_employee_identifier"


def test_patient_search_accepts_legacy_employeeid_claim_names(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(
            200,
            _introspection_payload(
                employee_identifier="",
                employee_roles=[],
                username="dezi-001",
                roleName="doctor",
            ),
        ),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))
    fake.queue("GET", "http://upstream/fhir/Patient", DummyResponse(200, _bundle(_patient())))
    fake.queue("GET", "http://upstream/fhir/Patient", DummyResponse(200, _bundle(_patient())))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get("/fhir/Patient", headers=_auth_headers())

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


def test_patient_search_accepts_raw_dezi_claim_names(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(
            200,
            _introspection_payload(
                employee_identifier="",
                employee_roles=[],
                dezi_id="dezi-001",
                relations=[{"roles": ["doctor"]}],
            ),
        ),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))
    fake.queue("GET", "http://upstream/fhir/Patient", DummyResponse(200, _bundle(_patient())))
    fake.queue("GET", "http://upstream/fhir/Patient", DummyResponse(200, _bundle(_patient())))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get("/fhir/Patient", headers=_auth_headers())

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


def test_patient_search_scopes_to_authorized_patient_and_keeps_include(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload()),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod))))
    fake.queue("GET", "http://upstream/fhir/Patient", DummyResponse(200, _bundle(_patient())))
    fake.queue(
        "GET",
        "http://upstream/fhir/Patient",
        DummyResponse(200, _bundle_with_include(_patient(), _practitioner(), _other_patient())),
    )

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get(
            "/fhir/Patient?_include=Patient:general-practitioner&name=ignored",
            headers=_auth_headers(),
        )

    assert response.status_code == 200, response.text
    bundle = response.json()
    refs = {(entry["resource"]["resourceType"], entry["resource"]["id"]) for entry in bundle["entry"]}
    assert ("Patient", "patient-1") in refs
    assert ("Practitioner", "pr-1") in refs
    assert ("Patient", "patient-2") not in refs

    search_call = fake.calls[3]
    assert search_call["params"] == [
        ("_include", "Patient:general-practitioner"),
        ("name", "ignored"),
        ("identifier", "http://fhir.nl/fhir/NamingSystem/bsn|999999990"),
    ]


def test_data_read_forbidden_without_professional_claims(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload(employee_identifier="", employee_roles=[])),
    )

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.get("/fhir/Patient", headers=_auth_headers())

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "missing_employee_identifier"


def test_task_update_forbidden_when_workflow_task_not_active(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload()),
    )
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(_workflow_task(appmod, status="completed"))))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.put(
            "/fhir/Task/wf-1",
            headers=_auth_headers(),
            json={"resourceType": "Task", "id": "wf-1", "status": "completed"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "workflow_task_not_active"


def test_task_update_preserves_authorization_base(monkeypatch):
    appmod = _import_app_module()
    _set_gateway_settings(monkeypatch, appmod)
    fake = FakeHttpClient()
    fake.queue(
        "POST",
        "http://nuts-node:8083/internal/auth/v2/accesstoken/introspect",
        DummyResponse(200, _introspection_payload()),
    )
    task = _workflow_task(appmod, status="requested")
    fake.queue("GET", "http://upstream/fhir/Task", DummyResponse(200, _bundle(task)))
    fake.queue("PUT", "http://upstream/fhir/Task/wf-1", DummyResponse(200, _workflow_task(appmod, status="completed")))

    with TestClient(appmod.app) as client:
        monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)
        response = client.put(
            "/fhir/Task/wf-1",
            headers=_auth_headers(),
            json={"resourceType": "Task", "id": "wf-1", "status": "completed"},
        )

    assert response.status_code == 200, response.text
    upstream_payload = fake.calls[2]["json"]
    assert upstream_payload["status"] == "completed"
    assert any(
        ident.get("system") == "https://sys.local/fhir/NamingSystem/task-authorization-base"
        and ident.get("value") == "auth-123"
        for ident in upstream_payload["identifier"]
    )
    assert any(
        any(coding.get("code") == "authorization-base" for coding in (item.get("type") or {}).get("coding") or [])
        and item.get("valueString") == "auth-123"
        for item in upstream_payload["input"]
    )
