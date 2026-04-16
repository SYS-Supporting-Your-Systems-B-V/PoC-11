# Sender BgZ Gateway

This service exposes SYS's protected sender-side BgZ FHIR API for the post-notification pull flow.

Responsibilities:

- accept protected follow-up requests on the public sender BgZ endpoint
- introspect incoming access tokens via the local Nuts node
- enforce sender-side authorization based on:
  - `authorization-base`
  - requesting organization URA
  - healthcare-professional claims
  - task status
  - patient context
- proxy only authorized requests to the internal HAPI STU3 sender FHIR server

This service is intentionally separate from `services/iti-90/`.

- `iti-90` remains the sender-side discovery and notification orchestrator
- `sender-bgz-gateway` is the protected sender-side resource API

Key configuration:

- `BGZ_GATEWAY_UPSTREAM_FHIR_BASE`
  - internal HAPI STU3 sender FHIR base
- `BGZ_GATEWAY_NUTS_INTERNAL_BASE`
  - local Nuts internal API base used for token introspection
- `BGZ_GATEWAY_AUTHORIZATION_BASE_SYSTEM`
  - repo-local Task.identifier system used to resolve `authorization-base` to the active workflow task
- `BGZ_GATEWAY_PATIENT_IDENTIFIER_SYSTEM`
  - patient identifier system used to resolve the authorized patient
- `BGZ_GATEWAY_MEDICAL_ROLE_VALUESET_URL`
  - reference URL for the medical-role code set used by the PoC policy
  - default points to the DECOR `RoleCodeNLZorgverlenertypen` value set
  - this is metadata/reference, not a runtime HTML fetch dependency
- `BGZ_GATEWAY_MEDICAL_ROLE_CODES`
  - optional allowlist for data-access role codes; when set, token introspection roles must intersect it
  - recommended source is the configured `BGZ_GATEWAY_MEDICAL_ROLE_VALUESET_URL`
- `BGZ_GATEWAY_REQUIRED_SCOPES`
  - optional allowlist for data-access scopes

Current PoC behavior:

- `Task/{id}` read requires a valid token, matching `organization_ura`, and matching `authorization-base`
- `Task?identifier=<system>|<value>` resolves exactly the geauthoriseerde workflow task for the current `authorization-base`
- every sender data read/search must additionally match a path declared on the geauthoriseerde workflow task in `Task.input`
- finding the workflow task by `authorization-base` is therefore only the first authorization step; it does not authorize arbitrary extra resources
- patient-identifying data reads/searches additionally require `employee_identifier` and `employee_roles`
- if `BGZ_GATEWAY_MEDICAL_ROLE_CODES` is empty, the gateway still requires at least one non-empty `employee_roles` claim, but does not hardcode an arbitrary role-code list
- `Task/{id}` update additionally requires the workflow task to remain active and preserves the stored `authorization-base` metadata on the outgoing PUT

Recommended policy shape:

- keep the DECOR role value set URL configured as the human/audit reference
- keep the actual enforced code allowlist local and explicit via `BGZ_GATEWAY_MEDICAL_ROLE_CODES`
- do not fetch and parse the remote HTML at runtime
