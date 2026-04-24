# Mock Notification Receiver

This service is a receiver-side test harness for the local BgZ notified-pull
flow. It is meant for operator-driven end-to-end testing, not as a production
receiver implementation.

## Responsibilities

- expose a small operator portal on `/`
- expose a minimal receiver FHIR surface on `/fhir`
- accept notification `Task` resources on `POST /fhir/Task`
- store received tasks in memory for inspection
- support a DEZI login roundtrip for the operator
- request sender-side Nuts access tokens
- pull the protected workflow task and authorized sender data
- mark the sender workflow task as completed

## What It Does Not Do

- persistent storage
- full FHIR profile validation
- production-grade receiver workflow processing
- custom receiver-to-sender trust shortcuts outside the current PoC flow

It is intentionally opinionated for testability: tasks are stored in memory,
debug endpoints are kept simple, and the operator portal exposes the current
session and received-task state directly so integration issues are easy to see.

## Current PoC 11/13 Flow

- incoming notifications are minimal STU3 `Task` resources
- `authorization-base` is read from `Task.input`
- the sender workflow task is fetched primarily via
  `Task?identifier=<basedOn.identifier.system>|<basedOn.identifier.value>`
- if the notification only contains an explicit workflow-task reference, the
  receiver can still fall back to `Task/{id}`
- sender follow-up reads are derived from the workflow task `Task.input` list,
  not from a hardcoded receiver-side allowlist
- sender endpoint discovery is done through the local directory using the sender
  URA from the notification
- task completion uses `PUT /fhir/Task/{id}` on the sender gateway

## Stack Setup Requirements

In the Compose stack this service mounts the repo-level `secrets/` tree and
loads additional runtime settings from
[`./.env`](./.env).

For the receiver-side DEZI login and operator `pull` action to work, the
following files must exist:

- `/secrets/mock-notification-receiver/dezi/certificaat_SYS_DEZI.crt`
- `/secrets/mock-notification-receiver/dezi/sleutel_SYS_DEZI.key`

In the repository layout those map to:

- `../../secrets/mock-notification-receiver/dezi/certificaat_SYS_DEZI.crt`
- `../../secrets/mock-notification-receiver/dezi/sleutel_SYS_DEZI.key`

Also verify these settings in [`./.env`](./.env):

- `MOCK_RECEIVER_DEZI_CLIENT_ID`
- `MOCK_RECEIVER_PUBLIC_ROOT`
- `MOCK_RECEIVER_DEZI_CALLBACK_PATH`
- `MOCK_RECEIVER_ORGANIZATION_URA`

Those values must match the registered DEZI client and the public callback URL.
If they do not, the service may still boot and accept incoming notification
tasks, but DEZI login and sender-data pull will fail.

## Routes

Public or notification-facing:

- `GET /health`
- `GET /fhir/metadata`
- `POST /fhir/Task`
- `GET /dezi`
- `GET /auth/dezi/callback`

Operator portal:

- `GET /`
- `GET /ui/state`
- `GET /auth/dezi/login`
- `POST /auth/dezi/logout`
- `POST /ui/tasks/{id}/pull`
- `POST /ui/tasks/{id}/complete`

Debug and inspection:

- `GET /fhir/Task`
- `GET /fhir/Task/{id}`
- `GET /debug/tasks`
- `GET /debug/tasks/latest`
- `GET /debug/tasks/latest-summary`
- `DELETE /debug/tasks`

Portal, inspection, and debug routes can be protected with optional basic auth.
Notification ingest on `POST /fhir/Task` is not gated by that portal auth layer.

## Operator Behavior

The portal flow is deliberately split into two actions after a notification is
received:

- `pull` performs DEZI-backed sender access-token retrieval and then fetches the
  workflow task plus the workflow-task-declared sender data paths
- `complete` requests a sender token without DEZI attestation and updates the
  sender workflow task status to `completed`

This separation makes it easier to debug whether a failure is in notification
delivery, DEZI login, sender token issuance, sender data authorization, or task
completion.

## Notification Authorization

By default `POST /fhir/Task` requires `Authorization: Bearer ...`. The token is
introspected through the local Nuts node, and the request is accepted only when:

- the token is active
- the token carries the configured incoming scope
- `organization_ura` or fallback `subject_id` matches
  `Task.requester.onBehalfOf.identifier.value`
- `Task.owner.identifier.value` matches the configured receiver organization URA

The portal basic-auth layer is separate from this check. It protects operator
and debug pages, not the notification delivery path itself.

## Key Configuration

Core service settings:

- `MOCK_RECEIVER_HOST`
- `MOCK_RECEIVER_PORT`
- `MOCK_RECEIVER_PUBLIC_ROOT`
- `MOCK_RECEIVER_PUBLIC_BASE`
- `MOCK_RECEIVER_DEFAULT_TASK_STATUS`
- `MOCK_RECEIVER_LOG_LEVEL`

Notification and sender flow:

- `MOCK_RECEIVER_REQUIRE_BEARER_TOKEN`
- `MOCK_RECEIVER_NUTS_INTERNAL_BASE`
- `MOCK_RECEIVER_INTROSPECTION_TIMEOUT`
- `MOCK_RECEIVER_REQUIRED_INCOMING_SCOPE`
- `MOCK_RECEIVER_SENDER_DATA_SCOPE`
- `MOCK_RECEIVER_SENDER_TOKEN_TIMEOUT`
- `MOCK_RECEIVER_LOCAL_ADRESBOOK_FHIR_BASE`
- `MOCK_RECEIVER_ORGANIZATION_URA`
- `MOCK_RECEIVER_NUTS_SUBJECT_ID`

Portal auth:

- `MOCK_RECEIVER_PORTAL_BASIC_AUTH_USERNAME`
- `MOCK_RECEIVER_PORTAL_BASIC_AUTH_PASSWORD`
- `MOCK_RECEIVER_PORTAL_BASIC_AUTH_REALM`

DEZI settings:

- `MOCK_RECEIVER_DEZI_WELL_KNOWN_URL`
- `MOCK_RECEIVER_DEZI_CLIENT_ID`
- `MOCK_RECEIVER_DEZI_SCOPE`
- `MOCK_RECEIVER_DEZI_CALLBACK_PATH`
- `MOCK_RECEIVER_DEZI_TIMEOUT`
- `MOCK_RECEIVER_DEZI_VERIFY_TLS`
- `MOCK_RECEIVER_DEZI_CA_CERTS_FILE`
- `MOCK_RECEIVER_DEZI_INTROSPECTION_ENDPOINT`
- `MOCK_RECEIVER_DEZI_CLIENT_ASSERTION_AUDIENCE`
- `MOCK_RECEIVER_DEZI_CERTIFICATE_FILE`
- `MOCK_RECEIVER_DEZI_PRIVATE_KEY_FILE`

Outbound TLS:

- `MOCK_RECEIVER_OUTBOUND_VERIFY_TLS`
- `MOCK_RECEIVER_OUTBOUND_CA_CERTS_FILE`

## DEZI Notes

The service uses Authorization Code + PKCE and `private_key_jwt` client
authentication for the DEZI token exchange. In the Compose stack the
certificate/key pair is mounted from the repo-level `secrets/` tree.

The DEZI certificate and key are not required for plain notification ingest on
`POST /fhir/Task`. They are required when an operator uses the browser flow on
`/dezi` or the UI `pull` action that obtains a sender token after login.

After login, the resulting session stores:

- the DEZI id token when available
- optional token-id data derived from DEZI introspection
- normalized identity claims used for sender access-token requests
- enough session state for the UI to show what happened during login and sender
  token selection

## Stack Defaults

In the Compose stack:

- the service listens on port `8002`
- the public root defaults to `https://mach2.disyepd.com/receiver-mock`
- the public FHIR base defaults to `https://mach2.disyepd.com/receiver-mock/fhir`
- DEZI key material is expected under `/secrets/mock-notification-receiver/dezi/`
- `start-stack/.env` overrides the callback path to `/dezi`, while the service
  still supports `/auth/dezi/callback` as an alias route

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The service loads `.env` from this folder automatically when present.

## Tests

```bash
pytest -vv tests
```

## Related Docs

- Full stack: [`../../start-stack/README.md`](../../start-stack/README.md)
