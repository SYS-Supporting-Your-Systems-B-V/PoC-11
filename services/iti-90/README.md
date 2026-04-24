# mCSD ITI-90 Address Book Proxy

This FastAPI service fronts an upstream mCSD/FHIR directory and adds two kinds
of behavior:

- address-book searches with flattened output for operator-facing clients
- BgZ notified-pull helpers for endpoint discovery, capability mapping, and
  notification delivery

The `/poc9/...` route names are retained for backward compatibility with the
existing UI and test flow, but this service is part of the current PoC 11/13
stack.

## In This Repository

For end-to-end local use, start the full stack via
[`../../start-stack/README.md`](../../start-stack/README.md). In that setup:

- the container name is `iti-90-address-book-proxy`
- the service listens on port `8000`
- configuration comes from `services/iti-90/.env.Docker`

This app enforces `MCSD_ALLOWED_HOSTS`, so use `http://localhost:8000` or
another configured host name during manual testing.

## What This Service Adds

Compared with a plain pass-through proxy, this service adds three important
layers:

- request normalization and allow-listing for upstream directory queries
- flattened response shapes for UI-oriented address-book searches
- sender-side BgZ workflow helpers that resolve receiver endpoints from the
  directory instead of trusting arbitrary client-supplied URLs

## Common Usage Modes

This service is usually used in one of two ways:

- as an address-book/search proxy in front of an upstream mCSD/FHIR directory
- as the sender-side helper for the local BgZ notified-pull flow

In address-book mode, the important settings are mainly `MCSD_BASE`, upstream
auth/TLS, and host/CORS limits. The `/addressbook/*` and `/mcsd/search/*`
routes remain useful even when the BgZ helpers are disabled.

In BgZ mode, the service also needs sender identity settings, sender FHIR base
settings, and access to the local Nuts node so it can request a receiver token,
build the notification Task, and maintain the sender workflow Task.

If you set `MCSD_NOTIFIEDPULL_ENABLED=false`, the service still works as an
address-book proxy, but all `/bgz/*` routes return `503`.

## Routes

General:

- `GET /health`
- `GET /docs`

HTML helpers:

- `GET /mscd_zoek/`
- `GET /mcsd_bgz_verwijzing/`

Directory search:

- `GET /mcsd/search/{resource}`
- `GET /addressbook/organization`
- `GET /addressbook/location`
- `GET /addressbook/search`
- `GET /addressbook/find-practitionerrole`

MSZ and capability mapping:

- `GET /poc9/msz/organizations`
- `GET /poc9/msz/orgunits`
- `GET /poc9/msz/endpoints`
- `GET /poc9/msz/capability-mapping`

BgZ helpers:

- `POST /bgz/load-data`
- `POST /bgz/preflight`
- `POST /bgz/task-preview`
- `POST /bgz/notify`

When `MCSD_NOTIFIEDPULL_ENABLED=false`, all `/bgz/*` endpoints return `503`.

## How The BgZ Endpoints Differ

`POST /bgz/load-data` is a demo helper. It pushes the bundled sample data into a
target FHIR base so the local sender flow has predictable test content.

`POST /bgz/preflight` validates whether a notification can be sent. It checks
sender configuration, resolves the receiver notification endpoint from the
directory, derives routing information from the selected target, and can probe
the receiver `/metadata` endpoint before any Task is sent.

`POST /bgz/task-preview` builds the notification Task without sending it. This
is useful for UI inspection and troubleshooting because it shows the exact Task
shape and the resolved routing metadata.

`POST /bgz/notify` performs the full flow: resolve the receiver endpoint, create
or prepare the sender workflow-task state, and submit the notification Task to
the resolved receiver endpoint.

What that means in practice:

- the frontend no longer decides the final receiver base URL
- a chosen endpoint id from the UI is treated as a hint and checked for staleness
- routing is recalculated from the directory again before sending
- the notification Task and the sender workflow Task are related but distinct:
  one is sent outward, the other is hosted on the configured sender storage FHIR
  base for the follow-up flow

## Key Configuration

Core upstream settings:

- `MCSD_BASE`: required upstream mCSD/FHIR base URL
- `MCSD_UPSTREAM_TIMEOUT`
- `MCSD_HTTPX_MAX_CONNECTIONS`
- `MCSD_HTTPX_MAX_KEEPALIVE_CONNECTIONS`
- `MCSD_BEARER_TOKEN`
- `MCSD_VERIFY_TLS`
- `MCSD_CA_CERTS_FILE`
- `MCSD_MTLS_CERT_FILE`
- `MCSD_MTLS_KEY_FILE`

Access control and safety:

- `MCSD_API_KEY`: optional `X-API-Key` protection for all endpoints except `/health`
- `MCSD_ALLOW_ORIGINS`
- `MCSD_ALLOWED_HOSTS`
- `MCSD_IS_PRODUCTION`
- `MCSD_MAX_QUERY_PARAMS`
- `MCSD_MAX_QUERY_VALUE_LENGTH`
- `MCSD_MAX_QUERY_PARAM_VALUES`

BgZ sender flow:

- `MCSD_NOTIFIEDPULL_ENABLED`
- `MCSD_SENDER_URA`
- `MCSD_SENDER_NAME`
- `MCSD_SENDER_UZI_SYS`
- `MCSD_SENDER_SYSTEM_NAME`
- `MCSD_SENDER_BGZ_PUBLIC_BASE`
- `MCSD_SENDER_BGZ_STORAGE_BASE`
- `MCSD_SENDER_BGZ_BASE`
- `MCSD_SENDER_NUTS_SUBJECT_ID`
- `MCSD_NUTS_INTERNAL_BASE`
- `MCSD_RECEIVER_NOTIFICATION_SCOPE`
- `MCSD_RECEIVER_TOKEN_TIMEOUT`

Audit and troubleshooting:

- `MCSD_AUDIT_HMAC_KEY`
- `MCSD_ALLOW_TASK_PREVIEW_IN_PRODUCTION`
- `MCSD_CAPABILITY_CACHE_TTL_SECONDS`
- `MCSD_DEBUG_DUMP_JSON`
- `MCSD_DEBUG_DUMP_DIR`
- `MCSD_DEBUG_DUMP_REDACT`
- `MCSD_LOG_LEVEL`

`MCSD_SENDER_BGZ_PUBLIC_BASE` is the externally advertised sender URL.
`MCSD_SENDER_BGZ_STORAGE_BASE` is the internal sender FHIR base used to create
and update workflow tasks. `MCSD_SENDER_BGZ_BASE` remains as a legacy fallback
for both when the split variables are not set.

## Configuration Guidance

The earlier version of this README had much more deployment guidance. That was
useful, and the current short variable list was not enough, so the practical
guidance is restored here in English.

### 1. Upstream Directory Connection

`MCSD_BASE` is the full upstream FHIR base URL. It determines the upstream
host, path, scheme, and port in one setting.

Examples:

```bash
MCSD_BASE=https://hapi.fhir.org/baseR4
MCSD_BASE=http://localhost:8080/fhir
MCSD_BASE=https://mtls.example.org/address-book/admin-directory
```

Operationally:

- there are no separate protocol or port settings
- all pass-through and flattened search endpoints ultimately read from this base
- `GET /health` does not probe the upstream; it only confirms the proxy itself
  is alive

### 2. Upstream Auth, TLS, And mTLS

When the upstream directory requires authentication or private trust anchors,
these settings control that connection:

- `MCSD_BEARER_TOKEN`
- `MCSD_VERIFY_TLS`
- `MCSD_CA_CERTS_FILE`
- `MCSD_MTLS_CERT_FILE`
- `MCSD_MTLS_KEY_FILE`

Behavior to be aware of:

- `MCSD_CA_CERTS_FILE` is only relevant when TLS verification is on
- if `MCSD_MTLS_KEY_FILE` is omitted, the cert file must also include the
  private key
- for local or PoC environments, disabling TLS verification may work, but the
  service explicitly rejects that when `MCSD_IS_PRODUCTION=true`

In the stack, the repo-root `secrets/` tree is mounted into the ITI-90
container, so paths such as `/secrets/iti-90/mtls/...` and
`/secrets/shared/...` are the intended runtime layout.

### 3. Access Control And Production Guardrails

Three settings matter most for exposing the proxy safely:

- `MCSD_API_KEY`
- `MCSD_ALLOW_ORIGINS`
- `MCSD_ALLOWED_HOSTS`

If `MCSD_API_KEY` is set, all protected endpoints require `X-API-Key`, while
`/health` remains open for liveness checks.

`MCSD_ALLOWED_HOSTS` is enforced by the app, which is why stack testing should
use `localhost:8000` or another configured host name. If the host header does
not match, the app rejects the request before the route logic runs.

When `MCSD_IS_PRODUCTION=true`, the service fails fast at startup if any of
these unsafe defaults are still present:

- `MCSD_ALLOW_ORIGINS=["*"]`
- `MCSD_ALLOWED_HOSTS=["*"]`
- `MCSD_VERIFY_TLS=false`

That is deliberate: this service is often used as an integration boundary, so
production mode is meant to surface weak defaults early instead of letting them
slip through.

### 4. BgZ Sender Identity And Base URLs

For the sender-side BgZ flow, these settings are the important ones:

- `MCSD_SENDER_URA`
- `MCSD_SENDER_NAME`
- `MCSD_SENDER_UZI_SYS`
- `MCSD_SENDER_SYSTEM_NAME`
- `MCSD_SENDER_NUTS_SUBJECT_ID`
- `MCSD_SENDER_BGZ_PUBLIC_BASE`
- `MCSD_SENDER_BGZ_STORAGE_BASE`
- legacy fallback: `MCSD_SENDER_BGZ_BASE`

The code currently treats the sender base as two concerns:

- `MCSD_SENDER_BGZ_PUBLIC_BASE` is the externally advertised sender URL that
  the receiver should use later in the flow
- `MCSD_SENDER_BGZ_STORAGE_BASE` is the internal sender FHIR base where this
  proxy creates and updates workflow tasks

`MCSD_SENDER_BGZ_BASE` is still supported as a legacy fallback for both, but
the split variables are the clearer configuration for the current stack.

Also important:

- `MCSD_SENDER_UZI_SYS` must be a RFC3986 URN such as `urn:oid:...` or
  `urn:uuid:...`
- `POST /bgz/preflight` and `POST /bgz/notify` require a usable sender storage
  base, because they need to host the workflow Task
- `POST /bgz/task-preview` can still generate the notification Task without
  writing the workflow Task, but it still needs the sender identity values

### 5. Receiver Token And Nuts Integration

The sender-side flow no longer trusts a free-form receiver base from the client.
Instead it:

1. resolves the receiver destination from the directory
2. resolves the matching notification endpoint
3. requests a receiver token through the local Nuts node
4. posts the notification Task to the resolved receiver endpoint

The key settings here are:

- `MCSD_NUTS_INTERNAL_BASE`
- `MCSD_RECEIVER_NOTIFICATION_SCOPE`
- `MCSD_RECEIVER_TOKEN_TIMEOUT`
- optionally `MCSD_SENDER_NUTS_SUBJECT_ID`

If `MCSD_SENDER_NUTS_SUBJECT_ID` is unset, the code falls back to the sender
URA as the subject id used in the internal Nuts token request.

### 6. Query Limits And Capability Cache

The raw pass-through search endpoint has input-shaping controls:

- `MCSD_MAX_QUERY_PARAMS`
- `MCSD_MAX_QUERY_VALUE_LENGTH`
- `MCSD_MAX_QUERY_PARAM_VALUES`

Those limits mainly protect `GET /mcsd/search/{resource}` from overly large or
pathological query strings. The more opinionated flattened endpoints have their
own route-specific validation.

The service also keeps a small in-memory cache for best-effort capability
checks, controlled by `MCSD_CAPABILITY_CACHE_TTL_SECONDS`. That cache is mainly
there to avoid repeatedly probing the same receiver metadata during interactive
testing.

### 7. Debug Dumps, Task Preview, And Troubleshooting

These settings are specifically for test and troubleshooting workflows:

- `MCSD_ALLOW_TASK_PREVIEW_IN_PRODUCTION`
- `MCSD_DEBUG_DUMP_JSON`
- `MCSD_DEBUG_DUMP_DIR`
- `MCSD_DEBUG_DUMP_REDACT`

Important behavior:

- `POST /bgz/task-preview` is blocked in production unless
  `MCSD_ALLOW_TASK_PREVIEW_IN_PRODUCTION=true`
- debug JSON dumps are off by default
- when enabled, the proxy writes outgoing payloads for `POST /bgz/load-data`
  and `POST /bgz/notify` to disk
- known BSN fields are redacted before writing when
  `MCSD_DEBUG_DUMP_REDACT=true`

This is useful for local debugging, but those files can still contain sensitive
integration data. Keep it off outside controlled environments.

## Current BgZ Behavior

- capability mapping resolves receiver endpoints from the directory instead of
  accepting a free-form receiver base URL from the client
- notifications are built as minimal STU3 `Task` resources with
  `authorization-base` in `Task.input`
- sender follow-up state is hosted on the configured sender storage FHIR base
- task preview and notify share the same backend routing logic

In practice that means the frontend does not decide where the notification is
posted. It can choose a receiver target and optionally a frontend-visible
endpoint id, but the backend resolves the effective notification destination
again from the directory and rejects stale selections.

The `/poc9/msz/capability-mapping` response is also intentionally richer than a
simple yes/no answer. It explains which endpoints were found on the target and
organization, which capability combination won, and which normalized bases are
safe to use for the next step in the flow.

## Quick Start By Usage Mode

### Address-Book Only

If you only want the mailbox and practitioner search features, the minimal setup
is small:

```bash
export MCSD_BASE=https://hapi.fhir.org/baseR4
export MCSD_NOTIFIEDPULL_ENABLED=false
python main.py
```

That keeps the address-book routes available while making it explicit that this
instance is not meant to send BgZ notifications.

### Full BgZ Sender Flow

For the sender-side notified-pull flow, the practical minimum is larger because
the service needs a sender identity, a sender workflow-task store, and a Nuts
node for receiver-token requests:

```bash
export MCSD_BASE=http://localhost:8080/fhir
export MCSD_SENDER_URA=12345678
export MCSD_SENDER_NAME="Demo Sender"
export MCSD_SENDER_UZI_SYS=urn:oid:2.16.528.1.1007.3.2.1234567
export MCSD_SENDER_SYSTEM_NAME="Demo Sender System"
export MCSD_SENDER_BGZ_STORAGE_BASE=http://localhost:8082/fhir
export MCSD_RECEIVER_NOTIFICATION_SCOPE=demo-scope
python main.py
```

If you plan to expose a receiver-usable sender URL later in the flow, also set
`MCSD_SENDER_BGZ_PUBLIC_BASE`. If you want `POST /bgz/notify` to work, a local
Nuts node still needs to be reachable at `MCSD_NUTS_INTERNAL_BASE`.

## Practical Endpoint Guide

The shortened README lost too much of the day-to-day usage detail. The sections
below restore the important parts without bringing back the old duplicated PoC
tables.

### Raw FHIR Search

`GET /mcsd/search/{resource}` is the low-level pass-through route. It forwards
queries to `MCSD_BASE`, but only for a fixed allow-list of resource types:

- `Practitioner`
- `PractitionerRole`
- `HealthcareService`
- `Location`
- `Organization`
- `Endpoint`
- `OrganizationAffiliation`

It also applies per-request guardrails:

- only allow-listed search parameters are forwarded
- `_count` is clamped to `1..200`
- repeated query values are limited by `MCSD_MAX_QUERY_PARAM_VALUES`
- oversized parameter sets are rejected before the upstream call

That makes this endpoint useful for troubleshooting or direct FHIR exploration
without exposing the full upstream search surface.

### Address-Book Endpoints

`GET /addressbook/search` is the most feature-rich mailbox and practitioner
lookup route. It does not just proxy a single upstream search. Instead it:

1. searches `Practitioner`
2. searches `PractitionerRole` with `_include` resources
3. enriches best-effort with `HealthcareService`
4. enriches best-effort with `OrganizationAffiliation`
5. flattens the result into frontend-friendly rows

It accepts both facade-style parameters and chained-style aliases. Useful
examples are:

- `name`, `family`, `given`
- `organization`
- `org_name`
- `specialty`
- `city`, `postal`
- `near=lat|lng|distance|unit`
- aliases such as `practitioner.name`, `organization.name:contains`,
  `location.address-city`, `location.near`, and `location.near-distance`

`mode=fast` keeps upstream fan-out small for interactive use. `mode=full`
follows more paging and enrichment and is better when completeness matters more
than latency.

`GET /addressbook/organization` is narrower: it looks for functional mailboxes
on `Organization` resources and returns e-mail addresses from:

- `Organization.telecom`
- included `Endpoint.address` values with a `mailto:` scheme

`GET /addressbook/location` is similar, but location-oriented. It resolves a
mailbox with this precedence:

- `Location.telecom`
- `Organization.telecom` of the linked managing organization
- `Organization.endpoint` values with `mailto:`

`GET /addressbook/find-practitionerrole` is a thin helper route for the older
UI flow: it first searches practitioners by name, then fetches matching
`PractitionerRole` resources, with optional organization or specialty filters.

### MSZ Discovery And Capability Mapping

The `/poc9/msz/*` routes are the discovery layer for the sender-side demo flow.

`GET /poc9/msz/organizations` returns active organizations plus their included
technical endpoints. `GET /poc9/msz/orgunits` expands a chosen organization into
locations, healthcare services, sub-organizations, or all three. Both routes
use cursor-based pagination so the UI can safely do "load more" without
exposing raw upstream paging URLs.

`GET /poc9/msz/endpoints` reads technical endpoints for a selected
`Location/<id>`, `HealthcareService/<id>`, or `Organization/<id>`. If the
selected target has no endpoint references of its own, the implementation can
fall back to a parent organization endpoint when that is the only routable
option.

`GET /poc9/msz/capability-mapping` is the bridge between discovery and sending.
It evaluates target-level and organization-level endpoints and returns a
decision:

- `A`: all required capabilities found directly on the target
- `B`: all required capabilities found on the organization
- `C`: the final answer is a target-plus-organization combination
- `D`: the required capability set is incomplete

For the current sender-side flow, the required capability is the
`Twiin-TA-notification` payload type. The BgZ FHIR server capability is exposed
as additional information, and `Nuts-OAuth` is only added when
`include_oauth=true`.

The response is intentionally verbose because the next BgZ step needs more than
just "supported or not". It includes candidate lists, the chosen endpoint, the
normalized base URL, and enough target and organization context for routing and
UI display.

### BgZ Endpoint Walkthrough

`POST /bgz/load-data` is a demo seeding helper. It loads the bundled BgZ sample
resources into a target FHIR base with `PUT {ResourceType}/{id}` and rewrites
the sender organization URA in the sample bundle before upload.

`POST /bgz/preflight` is the safest first step for a UI or operator. It checks
that sender configuration is complete, resolves the notification endpoint again
from the directory, optionally probes receiver `/metadata`, and returns the
effective routing fields the backend would later use in `/bgz/notify`.

`POST /bgz/task-preview` uses the same routing logic as `/bgz/notify` but stops
before the outbound send. It is the easiest way to inspect the exact Task shape
that would be posted. In production mode it stays blocked unless
`MCSD_ALLOW_TASK_PREVIEW_IN_PRODUCTION=true`.

`POST /bgz/notify` performs the full send flow:

1. resolve the receiver endpoint via capability mapping
2. re-resolve the receiver URA from the directory
3. request a receiver access token through the local Nuts node
4. create or update the sender workflow Task on the storage FHIR base
5. post the notification Task to `{resolved_receiver_base}/Task`

Two practical details matter here:

- the client-supplied `receiver_ura` is treated as a consistency check, not as
  the source of truth
- the chosen frontend endpoint id is also treated as a hint and can be rejected
  as stale if the backend remap no longer matches it

### HTML Helper Pages

The bundled HTML pages are still useful and should be documented, because they
show how the repo exercises the service:

- `GET /mscd_zoek/` serves the standalone mailbox search UI and only depends on
  the address-book routes
- `GET /mcsd_bgz_verwijzing/` serves the sender-side BgZ demo page used in the
  notified-pull walkthrough

They are thin clients around backend routes. Important checks such as host
validation, endpoint resolution, receiver-token acquisition, and routing
selection still happen in the API layer.

## Observability

The app has more built-in diagnostics than the current short README suggested.

### Request IDs

All requests accept an optional `X-Request-ID` header.

- if the client sends one, the same value is propagated and returned
- if the client does not send one, the proxy generates one
- the response always includes `X-Request-ID`
- normalized JSON error responses also include the request id

That makes it much easier to correlate:

- client requests
- upstream FHIR calls
- application logs
- BgZ audit events

### Audit Logging

BgZ flow endpoints emit structured audit events through the `mcsd.audit` logger.
Those events are meant to be compact and traceable rather than full payload
dumps.

If `MCSD_AUDIT_HMAC_KEY` is set, patient identifiers are pseudonymized in the
audit stream using an HMAC-derived value instead of being logged directly.

### File Logging

At startup, the service also writes a log file into the debug dump directory.
That is useful in environments where stdout is not the only log sink.

### OpenAPI And HTML Helpers

For interactive testing:

- `/docs` exposes the FastAPI Swagger UI
- `/mscd_zoek/` serves the bundled search page
- `/mcsd_bgz_verwijzing/` serves the bundled BgZ demo page

Those HTML helpers are intentionally thin clients around the backend routes. The
important routing and security checks still happen server-side.

## Operational Notes

- `GET /health` only checks the proxy itself. It does not verify that
  `MCSD_BASE` is reachable.
- When `MCSD_API_KEY` is configured, all endpoints except `/health` require
  `X-API-Key`.
- When `MCSD_IS_PRODUCTION=true`, the service fails fast if permissive CORS,
  permissive allowed-hosts, or disabled TLS verification are still configured.
- Debug JSON dumps are optional and meant for local troubleshooting. They should
  stay off in production.
- The service writes a startup log file into the debug dump directory, which is
  useful when stdout is not the only log sink.

## Local Run

Install and start:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```

By default the service loads `.env` from this folder. Set `MCSD_ENV_FILE` to
use a different file. In the stack, Docker loads `.env.Docker` through
`env_file`.

## Tests

```bash
pytest -vv tests
```

The test suite is split by behavior:

- `tests/test_app.py` covers raw search, flattened address-book behavior, and
  upstream request shaping such as `Accept: application/fhir+json`
- `tests/test_capability_mapping.py` covers endpoint normalization and the
  target/organization capability-selection logic
- `tests/test_bgz_endpoints.py` covers preflight, task preview, notify, load
  data, sender-base split behavior, and the Nuts token-request path

Some tests depend on the public HAPI FHIR server and may skip gracefully when
that upstream is unreachable from the environment where the suite is run.

## Related Docs

- Full stack: [`../../start-stack/README.md`](../../start-stack/README.md)
- Secret layout: [`../../SECRETS.md`](../../SECRETS.md)
