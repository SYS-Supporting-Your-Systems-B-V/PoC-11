# Mock Notification Receiver

This service is a receiver-side mock for local end-to-end testing of the BgZ sender flow.

Responsibilities:

- expose a very small operator webpage on `/`
- expose a receiver FHIR base with `GET /fhir/metadata`
- accept notification `Task` resources on `POST /fhir/Task`
- store received tasks in memory for inspection
- support a DEZI login roundtrip for the operator
- request a sender-side Nuts access token using the DEZI id-token
- fetch the protected workflow task and BgZ data from the sender
- expose debug endpoints for manual Postman/curl testing

It is intentionally not a real receiver implementation:

- receiver-side auth is limited to bearer-token introspection plus a repo-local policy check on:
  - sender `organization_ura` matching `Task.requester.onBehalfOf.identifier.value`
  - receiver URA matching `Task.owner.identifier.value`
- no FHIR profile validation
- no persistent storage
- no production-grade receiver workflow processing

Useful endpoints:

- `GET /`
- `GET /health`
- `GET /ui/state`
- `GET /auth/dezi/login`
- `GET /auth/dezi/callback`
- `POST /auth/dezi/logout`
- `POST /ui/tasks/{id}/pull`
- `GET /fhir/metadata`
- `POST /fhir/Task`
- `GET /fhir/Task`
- `GET /fhir/Task/{id}`
- `GET /debug/tasks/latest`
- `GET /debug/tasks/latest-summary`
- `DELETE /debug/tasks`

Default public base:

- `https://mach2.disyepd.com/receiver-mock/fhir`

Default public root:

- `https://mach2.disyepd.com/receiver-mock`

This path is meant for local self-tests where `iti-90` should discover a `Twiin-TA-notification` endpoint and successfully deliver a notification task without hitting the protected sender gateway route.

Current notification authorization behavior:

- `POST /fhir/Task` requires `Authorization: Bearer ...` by default
- the bearer token is introspected via the local Nuts node (`/internal/auth/v2/accesstoken/introspect`)
- the introspection result must contain `organization_ura`
- the token `organization_ura` must match `Task.requester.onBehalfOf.identifier.value`
- `Task.owner.identifier.value` must match the configured receiver organization URA
- the token must include the configured receiver scope (default `eOverdracht-receiver`)

Current DEZI + protected pull behavior:

- the portal starts an OIDC Authorization Code + PKCE login against DEZI
- the token exchange uses `private_key_jwt` with the configured certificate/key pair in `certificates/`
- the userinfo response is decrypted and validated locally
- the resulting DEZI id-token is included in the local Nuts `request-service-access-token` call
- the sender `Nuts-OAuth` endpoint is resolved from the Query Directory using the sender URA from the incoming notification task
- the workflow task is fetched via `GET /fhir/Task?identifier=...` using `Task.basedOn[0].identifier`
- the sender BgZ base is resolved from the directory first and only falls back to the notification task extension for backward compatibility with older notifications

Important configuration:

- `MOCK_RECEIVER_PUBLIC_ROOT`
- `MOCK_RECEIVER_DIRECTORY_FHIR_BASE`
- `MOCK_RECEIVER_ORGANIZATION_URA`
- `MOCK_RECEIVER_NUTS_SUBJECT_ID`
- `MOCK_RECEIVER_DEZI_WELL_KNOWN_URL`
- `MOCK_RECEIVER_DEZI_CLIENT_ID`
- `MOCK_RECEIVER_DEZI_CERTIFICATE_FILE`
- `MOCK_RECEIVER_DEZI_PRIVATE_KEY_FILE`
- `MOCK_RECEIVER_DEZI_VERIFY_TLS`
- `MOCK_RECEIVER_OUTBOUND_VERIFY_TLS`
