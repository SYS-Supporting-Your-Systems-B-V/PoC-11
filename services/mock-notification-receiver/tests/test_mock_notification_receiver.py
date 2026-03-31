import asyncio
import importlib.util
from urllib.parse import parse_qs, urlparse
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from jwcrypto import jwk


def _import_app_module():
    module_name = "mock_notification_receiver_main_test"
    sys.modules.pop(module_name, None)
    sys.modules.pop("configs", None)
    service_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(service_root))
    module_path = service_root / "main.py"
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


def _set_settings(monkeypatch, appmod) -> None:
    monkeypatch.setattr(appmod.settings, "public_root", "https://mach2.disyepd.com/receiver-mock", raising=False)
    monkeypatch.setattr(appmod.settings, "public_base", "https://mach2.disyepd.com/receiver-mock/fhir", raising=False)
    monkeypatch.setattr(appmod.settings, "default_task_status", "requested", raising=False)
    monkeypatch.setattr(appmod.settings, "require_bearer_token", True, raising=False)
    monkeypatch.setattr(appmod.settings, "required_scope", "eOverdracht-receiver", raising=False)
    monkeypatch.setattr(appmod.settings, "session_cookie_secure", False, raising=False)
    monkeypatch.setattr(appmod.settings, "receiver_organization_ura", "87654321", raising=False)
    monkeypatch.setattr(appmod.settings, "dezi_client_id", "87654321", raising=False)
    monkeypatch.setattr(appmod.settings, "dezi_scope", "openid", raising=False)
    monkeypatch.setattr(appmod.settings, "dezi_callback_path", "auth/dezi/callback", raising=False)


def _task_payload(sender_ura: str = "12345678") -> dict:
    return {
        "resourceType": "Task",
        "status": "requested",
        "basedOn": [{"reference": "Task/wf-123"}],
        "requester": {
            "onBehalfOf": {
                "identifier": {
                    "system": "http://fhir.nl/fhir/NamingSystem/ura",
                    "value": sender_ura,
                }
            }
        },
        "owner": {
            "identifier": {
                "system": "http://fhir.nl/fhir/NamingSystem/ura",
                "value": "87654321",
            }
        },
        "for": {
            "identifier": {
                "system": "http://fhir.nl/fhir/NamingSystem/bsn",
                "value": "999999990",
            }
        },
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
        "extension": [
            {
                "url": "http://example.org/fhir/StructureDefinition/sender-bgz-base",
                "valueUrl": "https://mach2.disyepd.com/notifiedpull/fhir",
            }
        ],
    }


def test_metadata_advertises_task_create(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    with TestClient(appmod.app) as client:
        response = client.get("/fhir/metadata")
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "CapabilityStatement"
    interactions = body["rest"][0]["resource"][0]["interaction"]
    assert any(item["code"] == "create" for item in interactions)


def test_post_task_requires_bearer_token(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    with TestClient(appmod.app) as client:
        response = client.post("/fhir/Task", json=_task_payload())

    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "missing_bearer_token"


def test_post_task_is_stored_and_summarized(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="12345678",
            scopes=["eOverdracht-receiver"],
        )

    monkeypatch.setattr(appmod, "_introspect_token", _fake_introspect)

    with TestClient(appmod.app) as client:
        reset = client.delete("/debug/tasks")
        assert reset.status_code == 200

        response = client.post(
            "/fhir/Task",
            json=_task_payload(),
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201, response.text
        created = response.json()
        assert created["id"]
        assert response.headers["Location"].endswith(f"/Task/{created['id']}")

        latest = client.get("/debug/tasks/latest-summary")
        assert latest.status_code == 200
        summary = latest.json()
        assert summary["based_on"] == "Task/wf-123"
        assert summary["authorization_base"] == "auth-123"
        assert summary["sender_bgz_base"] == "https://mach2.disyepd.com/notifiedpull/fhir"
        assert summary["owner_ura"] == "87654321"
        assert summary["patient_bsn"] == "999999990"
        assert summary["sender_ura"] == "12345678"

        task_read = client.get(f"/fhir/Task/{created['id']}")
        assert task_read.status_code == 200
        assert task_read.json()["id"] == created["id"]

        task_search = client.get("/fhir/Task")
        assert task_search.status_code == 200
        bundle = task_search.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["total"] == 1


def test_post_task_rejects_when_token_organization_does_not_match_task(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="99999999",
            scopes=["eOverdracht-receiver"],
        )

    monkeypatch.setattr(appmod, "_introspect_token", _fake_introspect)

    with TestClient(appmod.app) as client:
        response = client.post(
            "/fhir/Task",
            json=_task_payload(sender_ura="12345678"),
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "organization_not_authorized"


def test_ui_state_lists_tasks_and_reports_logged_out_session(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="12345678",
            scopes=["eOverdracht-receiver"],
        )

    monkeypatch.setattr(appmod, "_introspect_token", _fake_introspect)

    with TestClient(appmod.app) as client:
        client.delete("/debug/tasks")
        create = client.post(
            "/fhir/Task",
            json=_task_payload(),
            headers={"Authorization": "Bearer test-token"},
        )
        assert create.status_code == 201, create.text

        state = client.get("/ui/state")

    assert state.status_code == 200
    body = state.json()
    assert body["dezi"]["logged_in"] is False
    assert body["service"]["public_root"] == "https://mach2.disyepd.com/receiver-mock"
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["summary"]["authorization_base"] == "auth-123"


def test_portal_includes_dezi_login_link(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    with TestClient(appmod.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "DEZI Session" in response.text
    assert 'href="auth/dezi/login"' in response.text
    assert 'fetch("ui/state"' in response.text
    assert "Proeftuin Test Identities" not in response.text


def test_ui_state_includes_dezi_claims_and_tokens_when_logged_in(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    with TestClient(appmod.app) as client:
        session = appmod.app.state.session_store.create()
        session.dezi_logged_in_at = "2026-03-31T15:00:00Z"
        session.dezi_id_token = "dezi-id-token"
        session.dezi_userinfo_jwt = "dezi-userinfo-jwt"
        session.dezi_identity = {"display_name": "Dr. Demo", "organization_ura": "87654321", "roles": ["01.041"]}
        session.dezi_claims = {"sub": "demo-sub", "relations": [{"ura": "87654321", "roles": ["01.041"]}]}
        session.dezi_token_metadata = {"scope": "openid"}
        appmod.app.state.session_store.save(session)
        client.cookies.set(appmod.settings.session_cookie_name, session.session_id)

        response = client.get("/ui/state")

    assert response.status_code == 200
    body = response.json()
    assert body["dezi"]["logged_in"] is True
    assert body["dezi"]["id_token"] == "dezi-id-token"
    assert body["dezi"]["userinfo_jwt"] == "dezi-userinfo-jwt"
    assert body["dezi"]["claims"]["sub"] == "demo-sub"


def test_dezi_client_follows_redirects():
    appmod = _import_app_module()

    with TestClient(appmod.app):
        assert appmod.app.state.dezi_client.follow_redirects is True


def test_private_key_jwt_defaults_audience_to_token_endpoint(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_get_dezi_material():
        return {
            "certificate_thumbprint_sha1": "thumbprint-sha1",
            "private_jwk": jwk.JWK.generate(kty="RSA", size=2048),
        }

    monkeypatch.setattr(appmod, "_get_dezi_material", _fake_get_dezi_material)
    monkeypatch.setattr(appmod.settings, "dezi_client_assertion_audience", None, raising=False)

    token = asyncio.run(
        appmod._build_private_key_jwt(
            "https://dezi.example/token",
            {"issuer": "https://dezi.example"},
        )
    )

    claims = appmod._jwt_segment_payload(token)
    assert claims["aud"] == "https://dezi.example/token"


def test_dezi_login_redirects_with_pkce_state_and_session(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_oidc_config():
        return {
            "authorization_endpoint": "https://dezi.example/authorize",
            "token_endpoint": "https://dezi.example/token",
            "userinfo_endpoint": "https://dezi.example/userinfo",
            "jwks_uri": "https://dezi.example/jwks",
            "issuer": "https://dezi.example",
        }

    monkeypatch.setattr(appmod, "_get_dezi_oidc_configuration", _fake_oidc_config)

    with TestClient(appmod.app) as client:
        response = client.get("/auth/dezi/login?task_id=task-abc", follow_redirects=False)

        assert response.status_code == 302
        location = response.headers["location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.netloc == "dezi.example"
        assert parsed.path == "/authorize"
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["87654321"]
        assert params["redirect_uri"] == ["https://mach2.disyepd.com/receiver-mock/auth/dezi/callback"]
        assert params["scope"] == ["openid"]
        assert params["code_challenge_method"] == ["S256"]
        assert params["state"][0]
        assert params["nonce"][0]
        assert params["code_challenge"][0]

        session_id = response.cookies.get(appmod.settings.session_cookie_name)
        assert session_id
        session = appmod.app.state.session_store.get(session_id)

    assert session is not None
    assert session.pending_task_id == "task-abc"
    assert session.pending_state == params["state"][0]
    assert session.pending_nonce == params["nonce"][0]
    assert session.pending_code_verifier


def test_dezi_callback_alias_route_exists(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    with TestClient(appmod.app) as client:
        response = client.get("/dezi", follow_redirects=False)

    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "session_missing"


def test_dezi_callback_url_supports_root_level_callback(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    monkeypatch.setattr(appmod.settings, "dezi_callback_path", "/dezi", raising=False)

    assert appmod._dezi_callback_url() == "https://mach2.disyepd.com/dezi"


def test_dezi_callback_redirects_back_to_receiver_root(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_perform_dezi_login(session, *, code: str):
        assert code == "auth-code-123"
        session.dezi_id_token = "id-token"
        return session

    monkeypatch.setattr(appmod, "_perform_dezi_login", _fake_perform_dezi_login)

    with TestClient(appmod.app) as client:
        session = appmod.app.state.session_store.create()
        session.pending_state = "state-123"
        session.pending_code_verifier = "verifier-123"
        session.pending_task_id = "task-abc"
        appmod.app.state.session_store.save(session)
        client.cookies.set(appmod.settings.session_cookie_name, session.session_id)

        response = client.get("/auth/dezi/callback?code=auth-code-123&state=state-123", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://mach2.disyepd.com/receiver-mock/?task_id=task-abc"


def test_ui_pull_uses_dezi_session_and_returns_sender_data(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    sender_access_token_calls = []

    async def _fake_discover_sender_endpoints(_sender_ura: str):
        return {
            "organization": {"resourceType": "Organization", "id": "org-sender"},
            "oauth_endpoint": {"address": "https://sender.example/nuts-oauth2/oauth2/00700700"},
            "bgz_endpoint": {"address": "https://sender.example/notifiedpull/fhir"},
        }

    async def _fake_request_sender_access_token(*, task, dezi_id_token: str, sender_oauth_endpoint: str):
        sender_access_token_calls.append(
            {
                "task_id": task["id"],
                "dezi_id_token": dezi_id_token,
                "sender_oauth_endpoint": sender_oauth_endpoint,
            }
        )
        return {"access_token": "sender-token", "token_type": "Bearer", "scope": "bgz"}

    async def _fake_fetch_sender_path(sender_bgz_base: str, sender_access_token: str, relative_path: str):
        return {
            "ok": True,
            "url": f"{sender_bgz_base.rstrip('/')}/{relative_path}",
            "status_code": 200,
            "content_type": "application/fhir+json",
            "body": {"resourceType": "Bundle", "type": "searchset", "path": relative_path, "token": sender_access_token},
        }

    monkeypatch.setattr(appmod, "_discover_sender_endpoints", _fake_discover_sender_endpoints)
    monkeypatch.setattr(appmod, "_request_sender_access_token", _fake_request_sender_access_token)
    monkeypatch.setattr(appmod, "_fetch_sender_path", _fake_fetch_sender_path)

    with TestClient(appmod.app) as client:
        client.delete("/debug/tasks")
        task = _task_payload()
        task["id"] = "notif-1"
        stored = appmod.app.state.task_store.save(task)
        session = appmod.app.state.session_store.create()
        session.dezi_id_token = "dezi-id-token"
        session.dezi_identity = {"display_name": "Dr. Demo", "organization_ura": "87654321", "roles": ["01.041"]}
        appmod.app.state.session_store.save(session)
        client.cookies.set(appmod.settings.session_cookie_name, session.session_id)

        response = client.post(f"/ui/tasks/{stored.resource['id']}/pull")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sender"]["sender_oauth_endpoint"] == "https://sender.example/nuts-oauth2/oauth2/00700700"
    assert body["sender"]["sender_bgz_base"] == "https://mach2.disyepd.com/notifiedpull/fhir"
    assert body["sender_access_token"]["received"] is True
    assert body["pulls"]["workflow_task"]["body"]["path"] == "Task/wf-123"
    assert body["pulls"]["patient"]["body"]["token"] == "sender-token"
    assert sender_access_token_calls == [
        {
            "task_id": "notif-1",
            "dezi_id_token": "dezi-id-token",
            "sender_oauth_endpoint": "https://sender.example/nuts-oauth2/oauth2/00700700",
        }
    ]
