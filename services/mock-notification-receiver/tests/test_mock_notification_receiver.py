import asyncio
import base64
import importlib.util
import json
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
    monkeypatch.setattr(appmod.settings, "required_incoming_scope", "bgz-receiver", raising=False)
    monkeypatch.setattr(appmod.settings, "session_cookie_secure", False, raising=False)
    monkeypatch.setattr(appmod.settings, "receiver_organization_ura", "87654321", raising=False)
    monkeypatch.setattr(appmod.settings, "dezi_client_id", "87654321", raising=False)
    monkeypatch.setattr(appmod.settings, "dezi_scope", "openid", raising=False)
    monkeypatch.setattr(appmod.settings, "dezi_callback_path", "auth/dezi/callback", raising=False)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_username", "", raising=False)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_password", "", raising=False)


def _task_payload(sender_ura: str = "12345678") -> dict:
    return {
        "resourceType": "Task",
        "status": "requested",
        "basedOn": [
            {
                "identifier": {
                    "system": "urn:ietf:rfc:3986",
                    "value": "urn:uuid:11111111-1111-1111-1111-111111111111",
                }
            }
        ],
        "requester": {
            "agent": {
                "identifier": {
                    "system": "urn:ietf:rfc:3986",
                    "value": "urn:oid:2.16.528.1.1007.3.2.1234567",
                }
            },
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


def _workflow_task_payload(*pull_paths: str) -> dict:
    return {
        "resourceType": "Task",
        "id": "wf-1",
        "status": "requested",
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
            },
            *[
                {
                    "type": {
                        "coding": [
                            {
                                "system": "http://example.org/fhir/NamingSystem/workflow-input",
                                "code": f"pull-{index}",
                            }
                        ]
                    },
                    "valueString": path,
                }
                for index, path in enumerate(pull_paths, start=1)
            ],
        ],
    }


def _basic_auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


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
            scopes=["bgz-receiver"],
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
        assert summary["based_on"] == "urn:uuid:11111111-1111-1111-1111-111111111111"
        assert summary["based_on_identifier_system"] == "urn:ietf:rfc:3986"
        assert summary["based_on_identifier_value"] == "urn:uuid:11111111-1111-1111-1111-111111111111"
        assert summary["based_on_reference"] is None
        assert summary["authorization_base"] == "auth-123"
        assert summary["sender_bgz_base"] is None
        assert summary["owner_ura"] == "87654321"
        assert summary["patient_bsn"] is None
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
            scopes=["bgz-receiver"],
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


def test_post_task_accepts_matching_token_subject_id_fallback(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="00000000",
            subject_id="12345678",
            scopes=["bgz-receiver"],
        )

    monkeypatch.setattr(appmod, "_introspect_token", _fake_introspect)

    with TestClient(appmod.app) as client:
        response = client.post(
            "/fhir/Task",
            json=_task_payload(sender_ura="12345678"),
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 201, response.text


def test_post_task_rejects_when_owner_ura_does_not_match_receiver(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="12345678",
            scopes=["bgz-receiver"],
        )

    monkeypatch.setattr(appmod, "_introspect_token", _fake_introspect)
    payload = _task_payload()
    payload["owner"]["identifier"]["value"] = "00000000"

    with TestClient(appmod.app) as client:
        response = client.post(
            "/fhir/Task",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "receiver_not_authorized"


def test_ui_state_lists_tasks_and_reports_logged_out_session(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="12345678",
            scopes=["bgz-receiver"],
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
    assert 'fetch(appUrl("ui/state")' in response.text
    assert "Proeftuin Test Identities" not in response.text


def test_portal_requires_basic_auth_when_configured(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_username", "operator", raising=False)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_password", "secret", raising=False)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_realm", "Receiver Portal", raising=False)

    with TestClient(appmod.app) as client:
        unauthorized = client.get("/")
        authorized = client.get("/", headers=_basic_auth_headers("operator", "secret"))

    assert unauthorized.status_code == 401
    assert unauthorized.headers["WWW-Authenticate"] == 'Basic realm="Receiver Portal"'
    assert unauthorized.json()["detail"]["reason"] == "portal_auth_required"
    assert authorized.status_code == 200
    assert "DEZI Session" in authorized.text


def test_portal_basic_auth_does_not_block_notification_ingest(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_username", "operator", raising=False)
    monkeypatch.setattr(appmod.settings, "portal_basic_auth_password", "secret", raising=False)

    async def _fake_introspect(_token: str):
        return appmod.TokenContext(
            raw={},
            active=True,
            organization_ura="12345678",
            scopes=["bgz-receiver"],
        )

    monkeypatch.setattr(appmod, "_introspect_token", _fake_introspect)

    with TestClient(appmod.app) as client:
        create = client.post(
            "/fhir/Task",
            json=_task_payload(),
            headers={"Authorization": "Bearer test-token"},
        )
        unauthorized_state = client.get("/ui/state")
        authorized_state = client.get("/ui/state", headers=_basic_auth_headers("operator", "secret"))
        unauthorized_task_list = client.get("/fhir/Task")
        authorized_task_list = client.get("/fhir/Task", headers=_basic_auth_headers("operator", "secret"))

    assert create.status_code == 201, create.text
    assert unauthorized_state.status_code == 401
    assert authorized_state.status_code == 200
    assert len(authorized_state.json()["tasks"]) == 1
    assert unauthorized_task_list.status_code == 401
    assert authorized_task_list.status_code == 200
    assert authorized_task_list.json()["total"] == 1


def test_ui_state_includes_dezi_claims_and_tokens_when_logged_in(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    with TestClient(appmod.app) as client:
        session = appmod.app.state.session_store.create()
        session.dezi_logged_in_at = "2026-03-31T15:00:00Z"
        session.dezi_id_token = "dezi-id-token"
        session.dezi_token_id = "dezi-token-id"
        session.dezi_userinfo_jwt = "dezi-userinfo-jwt"
        session.dezi_identity = {
            "display_name": "Dr. Demo",
            "organization_ura": "87654321",
            "employee_identifier": "dezi-001",
            "roles": ["01.041"],
        }
        session.dezi_claims = {"sub": "demo-sub", "relations": [{"ura": "87654321", "roles": ["01.041"]}]}
        session.dezi_introspection = {"endpoint": "https://dezi.example/introspect", "payload": {"active": True}}
        session.dezi_token_metadata = {"scope": "openid"}
        appmod.app.state.session_store.save(session)
        client.cookies.set(appmod.settings.session_cookie_name, session.session_id)

        response = client.get("/ui/state")

    assert response.status_code == 200
    body = response.json()
    assert body["dezi"]["logged_in"] is True
    assert body["dezi"]["id_token"] == "dezi-id-token"
    assert body["dezi"]["token_id"] == "dezi-token-id"
    assert body["dezi"]["userinfo_jwt"] == "dezi-userinfo-jwt"
    assert body["dezi"]["claims"]["sub"] == "demo-sub"
    assert body["dezi"]["introspection"]["endpoint"] == "https://dezi.example/introspect"


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


def test_sender_attestation_inputs_prefers_userinfo_jwt_over_id_token():
    appmod = _import_app_module()

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-21T15:21:31Z")
    session.dezi_id_token = "raw-oidc-id-token"
    session.dezi_userinfo_jwt = "signed-dezi-userinfo-jwt"

    source, id_token, credentials = appmod._sender_attestation_inputs(session)

    assert source == "userinfo_jwt"
    assert id_token == "signed-dezi-userinfo-jwt"
    assert credentials == []


def test_sender_attestation_inputs_prefers_token_id_over_userinfo_jwt_and_id_token():
    appmod = _import_app_module()

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-21T15:21:31Z")
    session.dezi_id_token = "raw-oidc-id-token"
    session.dezi_userinfo_jwt = "signed-dezi-userinfo-jwt"
    session.dezi_token_id = "signed-dezi-token-id"

    source, id_token, credentials = appmod._sender_attestation_inputs(session)

    assert source == "token_id"
    assert id_token == "signed-dezi-token-id"
    assert credentials == []


def test_perform_dezi_login_accepts_introspection_statement_without_userinfo(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    async def _fake_oidc_config():
        return {
            "authorization_endpoint": "https://dezi.example/authorize",
            "token_endpoint": "https://dezi.example/token",
            "introspection_endpoint": "https://dezi.example/introspect",
            "jwks_uri": "https://dezi.example/jwks",
            "issuer": "https://dezi.example",
        }

    async def _fake_exchange_dezi_code(*, oidc_config, code: str, code_verifier: str):
        assert oidc_config["introspection_endpoint"] == "https://dezi.example/introspect"
        assert code == "auth-code-123"
        assert code_verifier == "verifier-123"
        return {"access_token": "dezi-access-token", "scope": "openid"}

    async def _fake_fetch_dezi_access_token_introspection(*, oidc_config, access_token: str):
        assert oidc_config["introspection_endpoint"] == "https://dezi.example/introspect"
        assert access_token == "dezi-access-token"
        return {"verklaring": "signed-dezi-token-id", "active": True}

    async def _fake_dezi_token_id_from_introspection(introspection_payload, *, oidc_config):
        assert introspection_payload["verklaring"] == "signed-dezi-token-id"
        assert oidc_config["issuer"] == "https://dezi.example"
        return (
            "signed-dezi-token-id",
            {
                "initials": "R.M.A.",
                "surname": "Laar",
                "surname_prefix": "van",
                "uzi_id": "999991772",
                "relations": [
                    {
                        "entity_name": "De Ziekenboeg",
                        "roles": ["01.010"],
                        "ura": "87654321",
                    }
                ],
            },
        )

    monkeypatch.setattr(appmod, "_get_dezi_oidc_configuration", _fake_oidc_config)
    monkeypatch.setattr(appmod, "_exchange_dezi_code", _fake_exchange_dezi_code)
    monkeypatch.setattr(appmod, "_fetch_dezi_access_token_introspection", _fake_fetch_dezi_access_token_introspection)
    monkeypatch.setattr(appmod, "_dezi_token_id_from_introspection", _fake_dezi_token_id_from_introspection)

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-21T15:21:31Z")
    session.pending_code_verifier = "verifier-123"

    updated = asyncio.run(appmod._perform_dezi_login(session, code="auth-code-123"))

    assert updated.dezi_id_token is None
    assert updated.dezi_token_id == "signed-dezi-token-id"
    assert updated.dezi_claims["uzi_id"] == "999991772"
    assert updated.dezi_identity["employee_identifier"] == "999991772"
    assert updated.dezi_identity["organization_ura"] == "87654321"
    assert updated.dezi_introspection["endpoint"] == "https://dezi.example/introspect"
    assert updated.dezi_introspection["payload"]["verklaring"] == "signed-dezi-token-id"


def test_sender_attestation_inputs_maps_frontend_dezi_claims_for_project_gf():
    appmod = _import_app_module()

    header = appmod._base64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = appmod._base64url(
        json.dumps(
            {
                "iss": "https://max.proeftuin.uzi-online.irealisatie.nl",
                "sub": "user-123",
                "aud": "8e9239df-d352-4367-b564-18bea9a1e9d4",
                "nbf": 1776844431,
                "exp": 1776844501,
                "initials": "R.M.A.",
                "surname": "Laar",
                "surname_prefix": "van",
                "uzi_id": "999991772",
                "relations": [
                    {
                        "entity_name": "De Ziekenboeg",
                        "roles": ["01.010"],
                        "ura": "42424242",
                    }
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-21T15:21:31Z")
    session.dezi_userinfo_jwt = f"{header}.{payload}.sig"

    source, id_token, credentials = appmod._sender_attestation_inputs(session)

    assert source == "mapped_userinfo_jwt"
    assert credentials == []
    mapped_claims = appmod._jwt_segment_payload(id_token or "")
    assert mapped_claims["dezi_nummer"] == "999991772"
    assert mapped_claims["abonnee_nummer"] == "42424242"
    assert mapped_claims["abonnee_naam"] == "De Ziekenboeg"
    assert mapped_claims["voorletters"] == "R.M.A."
    assert mapped_claims["voorvoegsel"] == "van"
    assert mapped_claims["achternaam"] == "Laar"
    assert mapped_claims["rol"] == "01.010"


def test_sender_attestation_inputs_requires_dezi_token_for_sender_flow():
    appmod = _import_app_module()

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-21T15:21:31Z")
    session.dezi_identity = {
        "employee_identifier": "dezi-001",
        "organization_ura": "87654321",
        "roles": ["01.041"],
    }

    source, id_token, credentials = appmod._sender_attestation_inputs(session)

    assert source == "none"
    assert id_token is None
    assert credentials == []


def test_endpoint_matches_capability_supports_fhir_payloadtype_coding():
    appmod = _import_app_module()

    endpoint = {
        "resourceType": "Endpoint",
        "payloadType": [
            {
                "coding": [
                    {
                        "system": "http://nuts-foundation.github.io/nl-generic-functions-ig/CodeSystem/nl-gf-data-exchange-capabilities",
                        "code": "Nuts-OAuth",
                    }
                ]
            }
        ],
    }

    assert appmod._endpoint_matches_capability(endpoint, "Nuts-OAuth") is True


def test_request_sender_access_token_uses_explicit_sender_scope(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    monkeypatch.setattr(appmod.settings, "sender_data_scope", "bgz-sender", raising=False)

    class _DummyResponse:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = ""

        def json(self):
            return self._body

    class _FakeHttpClient:
        def __init__(self):
            self.post_calls = []

        async def post(self, url, *, json=None, headers=None, timeout=None):
            self.post_calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
            return _DummyResponse(200, {"access_token": "sender-token", "token_type": "Bearer"})

    fake = _FakeHttpClient()
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    body = asyncio.run(
        appmod._request_sender_access_token(
            task=_task_payload(sender_ura="12345678"),
            sender_oauth_endpoint="https://sender.example/nuts-oauth2/oauth2/12345678",
            id_token="dezi-id-token",
        )
    )

    assert body["access_token"] == "sender-token"
    assert fake.post_calls == [
        {
            "url": "http://nuts-node:8083/internal/auth/v2/87654321/request-service-access-token",
            "json": {
                "authorization_server": "https://sender.example/nuts-oauth2/oauth2/12345678",
                "token_type": "Bearer",
                "scope": "bgz-sender",
                "id_token": "dezi-id-token",
            },
            "headers": {"Accept": "application/json"},
            "timeout": 10.0,
        }
    ]


def test_request_sender_access_token_includes_fallback_credentials(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    monkeypatch.setattr(appmod.settings, "sender_data_scope", "bgz-sender", raising=False)

    class _DummyResponse:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = ""

        def json(self):
            return self._body

    class _FakeHttpClient:
        def __init__(self):
            self.post_calls = []

        async def post(self, url, *, json=None, headers=None, timeout=None):
            self.post_calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
            return _DummyResponse(200, {"access_token": "sender-token", "token_type": "Bearer"})

    fake = _FakeHttpClient()
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    body = asyncio.run(
        appmod._request_sender_access_token(
            task=_task_payload(sender_ura="12345678"),
            sender_oauth_endpoint="https://sender.example/nuts-oauth2/oauth2/12345678",
            additional_credentials=[
                {
                    "@context": [
                        "https://www.w3.org/2018/credentials/v1",
                        "https://mach2.disyepd.com/contexts/dezi-user-credential-v1.ldjson",
                    ],
                    "type": "DeziUserCredential",
                    "credentialSubject": {
                        "identifier": "87654321",
                        "employee": {
                            "identifier": "dezi-001",
                            "role": "01.041",
                        },
                    },
                }
            ],
        )
    )

    assert body["access_token"] == "sender-token"
    assert fake.post_calls == [
        {
            "url": "http://nuts-node:8083/internal/auth/v2/87654321/request-service-access-token",
            "json": {
                "authorization_server": "https://sender.example/nuts-oauth2/oauth2/12345678",
                "token_type": "Bearer",
                "scope": "bgz-sender",
                "credentials": [
                    {
                        "@context": [
                            "https://www.w3.org/2018/credentials/v1",
                            "https://mach2.disyepd.com/contexts/dezi-user-credential-v1.ldjson",
                        ],
                        "type": "DeziUserCredential",
                        "credentialSubject": {
                            "identifier": "87654321",
                            "employee": {
                                "identifier": "dezi-001",
                                "role": "01.041",
                            },
                        },
                    }
                ],
            },
            "headers": {"Accept": "application/json"},
            "timeout": 10.0,
        }
    ]


def test_build_sender_additional_credentials_uses_dezi_identity():
    appmod = _import_app_module()

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-20T14:26:59Z")
    session.dezi_identity = {
        "employee_identifier": "dezi-001",
        "initials": "K.",
        "surname": "Smith",
        "roles": ["01.041"],
        "organization_ura": "87654321",
    }

    credentials = appmod._build_sender_additional_credentials(session)

    assert credentials == [
        {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://mach2.disyepd.com/contexts/dezi-user-credential-v1.ldjson",
            ],
            "type": "DeziUserCredential",
            "credentialSubject": {
                "identifier": "87654321",
                "employee": {
                    "identifier": "dezi-001",
                    "initials": "K.",
                    "surname": "Smith",
                    "role": "01.041",
                },
            },
        }
    ]


def test_issue_sender_additional_credentials_issues_signed_dezi_credentials(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    class _DummyResponse:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.text = ""

        def json(self):
            return self._body

    class _FakeHttpClient:
        def __init__(self):
            self.post_calls = []

        async def post(self, url, *, json=None, headers=None, timeout=None):
            self.post_calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
            return _DummyResponse(200, {"id": "cred-1", "type": ["DeziUserCredential", "VerifiableCredential"]})

    fake = _FakeHttpClient()
    monkeypatch.setattr(appmod.app.state, "http_client", fake, raising=False)

    async def _fake_resolve_subject_did(subject_id: str):
        assert subject_id == "87654321"
        return "did:web:example.com:nuts:iam:test"

    monkeypatch.setattr(appmod, "_resolve_subject_did", _fake_resolve_subject_did)

    issued = asyncio.run(
        appmod._issue_sender_additional_credentials(
            subject_id="87654321",
            credentials=[
                {
                    "@context": [
                        "https://www.w3.org/2018/credentials/v1",
                        "https://mach2.disyepd.com/contexts/dezi-user-credential-v1.ldjson",
                    ],
                    "type": "DeziUserCredential",
                    "credentialSubject": {
                        "identifier": "87654321",
                        "employee": {
                            "identifier": "dezi-001",
                            "role": "01.041",
                        },
                    },
                }
            ],
        )
    )

    assert issued == [{"id": "cred-1", "type": ["DeziUserCredential", "VerifiableCredential"]}]
    assert fake.post_calls[0]["url"] == "http://nuts-node:8083/internal/vcr/v2/issuer/vc"
    assert fake.post_calls[0]["json"]["issuer"] == "did:web:example.com:nuts:iam:test"
    assert fake.post_calls[0]["json"]["type"] == "DeziUserCredential"
    assert fake.post_calls[0]["json"]["@context"] == [
        "https://www.w3.org/2018/credentials/v1",
        "https://mach2.disyepd.com/contexts/dezi-user-credential-v1.ldjson",
    ]
    assert fake.post_calls[0]["json"]["credentialSubject"]["id"] == "did:web:example.com:nuts:iam:test"
    assert fake.post_calls[0]["json"]["credentialSubject"]["employee"]["identifier"] == "dezi-001"


def test_request_sender_access_token_requires_sender_scope(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)
    monkeypatch.setattr(appmod.settings, "sender_data_scope", "", raising=False)

    try:
        asyncio.run(
            appmod._request_sender_access_token(
                task=_task_payload(sender_ura="12345678"),
                sender_oauth_endpoint="https://sender.example/nuts-oauth2/oauth2/12345678",
            )
        )
    except appmod.HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail["reason"] == "misconfigured"
        assert exc.detail["message"] == "Geen sender scope geconfigureerd voor de sender tokenaanvraag."
    else:
        raise AssertionError("Expected HTTPException for missing sender scope")


def test_select_sender_access_token_prefers_candidate_with_employee_claims(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    request_calls = []

    async def _fake_request_sender_access_token(
        *,
        task,
        sender_oauth_endpoint: str,
        id_token=None,
        id_token_source=None,
        additional_credentials=None,
    ):
        request_calls.append(
            {
                "task_id": task["id"],
                "id_token": id_token,
                "id_token_source": id_token_source,
                "additional_credentials": additional_credentials,
                "sender_oauth_endpoint": sender_oauth_endpoint,
            }
        )
        return {
            "access_token": "sender-token-a",
            "token_type": "Bearer",
            "scope": "bgz-sender",
            "expires_in": 900,
        }

    async def _fake_introspect_access_token_payload(token: str):
        return {
            "active": True,
            "organization_ura": "87654321",
            "user_id": "dezi-001",
            "user_role": "doctor",
            "scope": "bgz-sender",
        }

    monkeypatch.setattr(appmod, "_request_sender_access_token", _fake_request_sender_access_token)
    monkeypatch.setattr(appmod, "_introspect_access_token_payload", _fake_introspect_access_token_payload)

    session = appmod.UserSession(session_id="sess-1", created_at="2026-04-02T11:00:00Z")
    session.dezi_id_token = "signed-dezi-id-token"
    session.dezi_userinfo_jwt = "signed-dezi-userinfo-jwt"
    session.dezi_identity = {
        "employee_identifier": "dezi-001",
        "initials": "K.",
        "surname": "Smith",
        "roles": ["doctor", "01.041"],
        "organization_ura": "87654321",
    }

    token_payload, access_token, selected_summary, evaluated = asyncio.run(
        appmod._select_sender_access_token(
            task={"id": "notif-1", "owner": {"identifier": {"value": "87654321"}}},
            sender_oauth_endpoint="https://sender.example/nuts-oauth2/oauth2/12345678",
            session=session,
        )
    )

    assert token_payload["access_token"] == "sender-token-a"
    assert access_token == "sender-token-a"
    assert selected_summary["source"] == "userinfo_jwt"
    assert selected_summary["employee_identifier"] == "dezi-001"
    assert selected_summary["introspection_raw"]["user_id"] == "dezi-001"
    assert len(evaluated) == 1
    assert evaluated[0]["introspection_raw"]["organization_ura"] == "87654321"
    assert evaluated[0]["introspection_raw"]["user_role"] == "doctor"
    assert request_calls == [
        {
            "task_id": "notif-1",
            "id_token": "signed-dezi-userinfo-jwt",
            "id_token_source": "userinfo_jwt",
            "additional_credentials": [],
            "sender_oauth_endpoint": "https://sender.example/nuts-oauth2/oauth2/12345678",
        },
    ]


def test_ui_pull_uses_dezi_session_and_returns_sender_data(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    sender_access_token_calls = []
    sender_fetch_calls = []
    workflow_task_body = _workflow_task_payload(
        "Patient?_include=Patient:general-practitioner",
        "Observation/$lastn?code=http://loinc.org|85354-9",
        "Observation/$lastn?code=http://loinc.org|29463-7",
    )

    async def _fake_discover_sender_endpoints(_sender_ura: str):
        return {
            "organization": {"resourceType": "Organization", "id": "org-sender"},
            "oauth_endpoint": {"address": "https://sender.example/nuts-oauth2"},
            "bgz_endpoint": {"address": "https://sender.example/notifiedpull/fhir"},
        }

    async def _fake_select_sender_access_token(*, task, sender_oauth_endpoint: str, session):
        sender_access_token_calls.append(
            {
                "task_id": task["id"],
                "dezi_identity": session.dezi_identity,
                "sender_oauth_endpoint": sender_oauth_endpoint,
            }
        )
        token_payload = {"access_token": "sender-token", "token_type": "Bearer", "scope": "bgz"}
        selected_summary = {
            "source": "userinfo_jwt",
            "employee_identifier": "dezi-001",
            "employee_identifier_present": True,
            "employee_roles": ["01.041"],
            "employee_roles_present": True,
            "introspection_raw": {
                "active": True,
                "employee_identifier": "dezi-001",
                "employee_roles": ["01.041"],
            },
        }
        return token_payload, "sender-token", selected_summary, [selected_summary]

    async def _fake_fetch_sender_path(
        sender_bgz_base: str,
        sender_access_token: str,
        relative_path: str,
        *,
        authorization_base: str | None = None,
    ):
        sender_fetch_calls.append(
            {
                "sender_bgz_base": sender_bgz_base,
                "sender_access_token": sender_access_token,
                "relative_path": relative_path,
                "authorization_base": authorization_base,
            }
        )
        if relative_path.startswith("Task?identifier="):
            body = {
                "resourceType": "Bundle",
                "type": "searchset",
                "total": 1,
                "entry": [{"resource": workflow_task_body}],
            }
        else:
            body = {"resourceType": "Bundle", "type": "searchset", "path": relative_path, "token": sender_access_token}
        return {
            "ok": True,
            "url": f"{sender_bgz_base.rstrip('/')}/{relative_path}",
            "status_code": 200,
            "content_type": "application/fhir+json",
            "body": body,
        }

    monkeypatch.setattr(appmod, "_discover_sender_endpoints", _fake_discover_sender_endpoints)
    monkeypatch.setattr(appmod, "_select_sender_access_token", _fake_select_sender_access_token)
    monkeypatch.setattr(appmod, "_fetch_sender_path", _fake_fetch_sender_path)

    with TestClient(appmod.app) as client:
        client.delete("/debug/tasks")
        task = _task_payload()
        task["id"] = "notif-1"
        stored = appmod.app.state.task_store.save(task)
        session = appmod.app.state.session_store.create()
        session.dezi_userinfo_jwt = "dezi-userinfo-jwt"
        session.dezi_identity = {
            "display_name": "Dr. Demo",
            "organization_ura": "87654321",
            "employee_identifier": "dezi-001",
            "roles": ["01.041"],
        }
        appmod.app.state.session_store.save(session)
        client.cookies.set(appmod.settings.session_cookie_name, session.session_id)

        response = client.post(f"/ui/tasks/{stored.resource['id']}/pull")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sender"]["sender_oauth_endpoint"] == "https://sender.example/nuts-oauth2"
    assert body["sender"]["authorization_base"] == "auth-123"
    assert body["sender"]["sender_bgz_base"] == "https://sender.example/notifiedpull/fhir"
    assert body["sender_access_token"]["received"] is True
    assert body["sender_access_token"]["attestation_source"] == "userinfo_jwt"
    assert body["sender_access_token"]["selected_introspection"]["introspection_raw"]["employee_identifier"] == "dezi-001"
    assert body["sender"]["workflow_task_identifier_system"] == "urn:ietf:rfc:3986"
    assert body["sender"]["workflow_task_identifier_value"] == "urn:uuid:11111111-1111-1111-1111-111111111111"
    assert body["pulls"]["workflow_task"]["body"]["resourceType"] == "Bundle"
    assert body["sender"]["workflow_task_pull_paths"] == [
        "Patient?_include=Patient:general-practitioner",
        "Observation/$lastn?code=http://loinc.org|85354-9",
        "Observation/$lastn?code=http://loinc.org|29463-7",
    ]
    assert body["pulls"]["Patient?_include=Patient:general-practitioner"]["body"]["token"] == "sender-token"
    assert body["pulls"]["Observation/$lastn?code=http://loinc.org|85354-9"]["body"]["path"] == "Observation/$lastn?code=http%3A%2F%2Floinc.org%7C85354-9"
    assert body["pulls"]["Observation/$lastn?code=http://loinc.org|29463-7"]["body"]["path"] == "Observation/$lastn?code=http%3A%2F%2Floinc.org%7C29463-7"
    assert all(call["authorization_base"] == "auth-123" for call in sender_fetch_calls)
    assert [call["relative_path"] for call in sender_fetch_calls] == [
        "Task?identifier=urn%3Aietf%3Arfc%3A3986%7Curn%3Auuid%3A11111111-1111-1111-1111-111111111111",
        "Patient?_include=Patient%3Ageneral-practitioner",
        "Observation/$lastn?code=http%3A%2F%2Floinc.org%7C85354-9",
        "Observation/$lastn?code=http%3A%2F%2Floinc.org%7C29463-7",
    ]
    assert sender_access_token_calls == [
        {
            "task_id": "notif-1",
            "dezi_identity": {
                "display_name": "Dr. Demo",
                "organization_ura": "87654321",
                "employee_identifier": "dezi-001",
                "roles": ["01.041"],
            },
            "sender_oauth_endpoint": "https://sender.example/nuts-oauth2/oauth2/12345678",
        }
    ]


def test_ui_complete_updates_sender_task_without_dezi_login(monkeypatch):
    appmod = _import_app_module()
    _set_settings(monkeypatch, appmod)

    sender_token_calls = []
    workflow_lookup_calls = []
    sender_update_calls = []

    async def _fake_discover_sender_endpoints(_sender_ura: str):
        return {
            "organization": {"resourceType": "Organization", "id": "org-sender"},
            "oauth_endpoint": {"address": "https://sender.example/nuts-oauth2"},
            "bgz_endpoint": {"address": "https://sender.example/notifiedpull/fhir"},
        }

    async def _fake_request_sender_access_token(
        *,
        task,
        sender_oauth_endpoint: str,
        id_token=None,
        id_token_source=None,
        additional_credentials=None,
    ):
        sender_token_calls.append(
            {
                "task_id": task["id"],
                "sender_oauth_endpoint": sender_oauth_endpoint,
                "id_token": id_token,
                "id_token_source": id_token_source,
                "additional_credentials": additional_credentials,
            }
        )
        return {
            "access_token": "sender-status-token",
            "token_type": "Bearer",
            "scope": "bgz-sender",
            "expires_in": 900,
        }

    async def _fake_introspect_access_token_payload(token: str):
        assert token == "sender-status-token"
        return {
            "active": True,
            "organization_ura": "87654321",
            "scope": "bgz-sender",
        }

    async def _fake_fetch_sender_path(
        sender_bgz_base: str,
        sender_access_token: str,
        relative_path: str,
        *,
        authorization_base: str | None = None,
    ):
        workflow_lookup_calls.append(
            {
                "sender_bgz_base": sender_bgz_base,
                "sender_access_token": sender_access_token,
                "relative_path": relative_path,
                "authorization_base": authorization_base,
            }
        )
        return {
            "ok": True,
            "url": f"{sender_bgz_base.rstrip('/')}/{relative_path}",
            "status_code": 200,
            "content_type": "application/fhir+json",
            "body": {
                "resourceType": "Bundle",
                "type": "searchset",
                "total": 1,
                "entry": [{"resource": {"resourceType": "Task", "id": "wf-1", "status": "requested"}}],
            },
        }

    async def _fake_put_sender_task_status(
        sender_bgz_base: str,
        sender_access_token: str,
        workflow_task_id: str,
        *,
        status: str,
        authorization_base: str | None = None,
    ):
        sender_update_calls.append(
            {
                "sender_bgz_base": sender_bgz_base,
                "sender_access_token": sender_access_token,
                "workflow_task_id": workflow_task_id,
                "status": status,
                "authorization_base": authorization_base,
            }
        )
        return {
            "ok": True,
            "url": f"{sender_bgz_base.rstrip('/')}/Task/{workflow_task_id}",
            "status_code": 200,
            "content_type": "application/fhir+json",
            "request_body": {"resourceType": "Task", "id": workflow_task_id, "status": status},
            "body": {"resourceType": "Task", "id": workflow_task_id, "status": status},
        }

    monkeypatch.setattr(appmod, "_discover_sender_endpoints", _fake_discover_sender_endpoints)
    monkeypatch.setattr(appmod, "_request_sender_access_token", _fake_request_sender_access_token)
    monkeypatch.setattr(appmod, "_introspect_access_token_payload", _fake_introspect_access_token_payload)
    monkeypatch.setattr(appmod, "_fetch_sender_path", _fake_fetch_sender_path)
    monkeypatch.setattr(appmod, "_put_sender_task_status", _fake_put_sender_task_status)

    with TestClient(appmod.app) as client:
        client.delete("/debug/tasks")
        task = _task_payload()
        task["id"] = "notif-1"
        stored = appmod.app.state.task_store.save(task)

        response = client.post(f"/ui/tasks/{stored.resource['id']}/complete")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notification_summary"]["id"] == "notif-1"
    assert body["sender"]["sender_oauth_endpoint"] == "https://sender.example/nuts-oauth2"
    assert body["sender"]["sender_bgz_base"] == "https://sender.example/notifiedpull/fhir"
    assert body["sender"]["workflow_task_id"] == "wf-1"
    assert body["sender"]["workflow_task_lookup_path"] == "Task?identifier=urn%3Aietf%3Arfc%3A3986%7Curn%3Auuid%3A11111111-1111-1111-1111-111111111111"
    assert body["sender_access_token"]["received"] is True
    assert body["sender_access_token"]["attestation_source"] == "none"
    assert body["sender_access_token"]["selected_introspection"]["organization_ura"] == "87654321"
    assert body["task_update"]["body"]["status"] == "completed"
    assert sender_token_calls == [
        {
            "task_id": "notif-1",
            "sender_oauth_endpoint": "https://sender.example/nuts-oauth2/oauth2/12345678",
            "id_token": None,
            "id_token_source": None,
            "additional_credentials": None,
        }
    ]
    assert workflow_lookup_calls == [
        {
            "sender_bgz_base": "https://sender.example/notifiedpull/fhir",
            "sender_access_token": "sender-status-token",
            "relative_path": "Task?identifier=urn%3Aietf%3Arfc%3A3986%7Curn%3Auuid%3A11111111-1111-1111-1111-111111111111",
            "authorization_base": "auth-123",
        }
    ]
    assert sender_update_calls == [
        {
            "sender_bgz_base": "https://sender.example/notifiedpull/fhir",
            "sender_access_token": "sender-status-token",
            "workflow_task_id": "wf-1",
            "status": "completed",
            "authorization_base": "auth-123",
        }
    ]
