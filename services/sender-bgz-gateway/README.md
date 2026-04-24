# Sender BgZ Gateway

This service exposes the protected sender-side FHIR surface used after a BgZ
notification has been delivered. It sits in front of the internal sender FHIR
server and only proxies requests that pass the current authorization rules.

## Responsibilities

- introspect incoming access tokens through the local Nuts node
- resolve the authorized workflow task from `authorization-base`
- allow only workflow-task-authorized follow-up reads
- scope patient data reads to the authorized patient context
- proxy valid reads and task status updates to the internal HAPI STU3 server

This gateway is intentionally separate from `services/iti-90`:

- `iti-90` handles discovery, capability mapping, and notification creation
- `sender-bgz-gateway` protects the sender-side follow-up pull and task update

## Exposed Routes

- `GET /health`
- `GET /fhir/metadata`
- `GET /fhir/Task/{task_id}`
- `GET /fhir/Task?identifier=<system>|<value>`
- `PUT /fhir/Task/{task_id}`
- `GET /fhir/Observation/$lastn`
- `GET /fhir/Patient`
- `GET /fhir/Patient/{id}`
- `GET /fhir/Observation`
- `GET /fhir/Observation/{id}`

Other FHIR resource types return `404`. Generic `GET /fhir/Task` search only
supports the `identifier` query parameter.

## Current PoC 11/13 Authorization Model

- the token is introspected through the local Nuts node
- the token must carry `authorization-base`
- the authorized workflow task is the task whose `Task.input` contains that same
  `authorization-base`
- workflow-task reads require organization authorization, but do not require
  healthcare-professional claims
- patient and observation reads require healthcare-professional claims from
  introspection
- sender data reads are allowed only when the requested FHIR path is declared in
  the workflow task `Task.input`
- the gateway does not accept a custom DEZI forwarding header as a substitute
  for introspection

The gateway keeps a legacy repo-specific Task identifier system only so it can
strip old data during `PUT` updates. It is not used as the current lookup
contract.

The important design point is that finding the workflow task is only the first
authorization step. A valid `authorization-base` does not automatically grant
access to arbitrary sender resources. The workflow task still has to declare the
follow-up paths that are allowed.

## Request Rules

Workflow task read:

- valid bearer token required
- `organization_ura` must match the authorized task context
- `authorization-base` must match the authorized workflow task
- task must still be in `requested` state
- `GET /fhir/Task/{id}` only returns the actual authorized workflow task

Workflow task search:

- only `identifier=<system>|<value>` is supported
- returns the authorized workflow task only when that identifier matches
- primarily exists for the receiver-side workflow-task lookup flow

The dedicated search route is kept because the receiver flow may start from the
notification `basedOn.identifier` instead of a direct workflow-task id.

Patient and observation data:

- valid bearer token required
- `organization_ura` must match the authorized task context
- `employee_identifier` and `employee_roles` must be present
- `BGZ_GATEWAY_REQUIRED_SCOPES`, when configured, must match the token scopes
- `BGZ_GATEWAY_MEDICAL_ROLE_CODES`, when configured, must intersect the token roles
- the upstream request is automatically narrowed to the authorized patient

This patient scoping is enforced in the gateway, not left to the caller. For
`Patient` searches the gateway rewrites the upstream query to the authorized
identifier. For `Observation` reads and searches it injects the authorized
patient context before proxying to the internal FHIR server.

Task update:

- only `PUT /fhir/Task/{id}` is allowed
- the request body may only contain `resourceType`, `id`, and `status`
- only `completed` and `failed` are accepted as new statuses
- the task must still be active when the update is made

The gateway preserves the stored sender-side Task metadata and rejects attempts
to update unrelated fields from the receiver side.

## Key Configuration

Core upstreams:

- `BGZ_GATEWAY_UPSTREAM_FHIR_BASE`
- `BGZ_GATEWAY_NUTS_INTERNAL_BASE`
- `BGZ_GATEWAY_PATIENT_IDENTIFIER_SYSTEM`

Authorization policy:

- `BGZ_GATEWAY_MEDICAL_ROLE_VALUESET_URL`
- `BGZ_GATEWAY_MEDICAL_ROLE_CODES`
- `BGZ_GATEWAY_REQUIRED_SCOPES`

Transport and logging:

- `BGZ_GATEWAY_UPSTREAM_TIMEOUT`
- `BGZ_GATEWAY_INTROSPECTION_TIMEOUT`
- `BGZ_GATEWAY_VERIFY_TLS`
- `BGZ_GATEWAY_CA_CERTS_FILE`
- `BGZ_GATEWAY_LOG_LEVEL`
- `BGZ_GATEWAY_LOG_SENSITIVE_DATA`
- `BGZ_GATEWAY_LOG_PREVIEW_CHARS`
- `BGZ_GATEWAY_IS_PRODUCTION`

## Stack Defaults

In the Compose stack:

- the service listens on port `8001`
- the upstream sender FHIR base defaults to `http://hapi-notifiedpull-stu3:8082/fhir`
- introspection goes to `http://nuts-node:8083`
- the default patient identifier system is `http://fhir.nl/fhir/NamingSystem/bsn`

## Operational Notes

- `GET /health` reports gateway status and whether a medical-role allowlist is
  configured; it does not validate the full downstream sender flow.
- If `BGZ_GATEWAY_MEDICAL_ROLE_CODES` is empty, the gateway still requires a
  non-empty role claim and does not silently disable professional-role checks.
- The configured value-set URL is documentation and audit context only. The
  gateway does not fetch and parse that remote HTML at runtime.
- Logging can include sensitive previews in non-production mode, so
  `BGZ_GATEWAY_LOG_SENSITIVE_DATA` should be treated carefully.

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
