# PoC 11-13 Start Stack

This folder contains the Docker Compose stack used for the integrated local PoC:

- `iti-130-publisher`
- `iti-91-mcsd-update-client`
- `iti-90-address-book-proxy`
- `sender-bgz-gateway`
- `mock-notification-receiver`
- `nuts-node`
- three HAPI FHIR servers
- Postgres, Redis, and optional Caddy

## Prerequisites

- Docker Desktop or Docker Engine with the Compose plugin
- free host ports: `443`, `5432`, `8000`, `8001`, `8002`, `8080`, `8081`,
  `8082`, `8083`, `8084`, `8509`, `16379`
- `../secrets/` present for runtime certificates and keys
- `../secrets/cloudflare_api_token` only when the `caddy` profile is enabled
  with `Caddyfile`

The shipped `.env` enables `COMPOSE_PROFILES=caddy` by default.

## Files To Review

- `.env`
- `iti-91.conf`
- `../services/iti-90/.env.Docker`
- `../services/mock-notification-receiver/.env`
- `../SECRETS.md`

If you want to reset the tracked defaults:

```bash
cp .env.example .env
cp iti-91.conf.example iti-91.conf
```

## Before First Start

The stack has a few pre-start requirements that are easy to miss because Docker
mounts them directly from the repo root `secrets/` tree.

### Nuts Node TLS Files

`nuts-node` mounts `../secrets/nuts-node/tls` into `/opt/nuts/certs` and its
runtime config expects these filenames:

- `${PUBLIC_DOMAIN}.pem`
- `${PUBLIC_DOMAIN}.key`
- `${PUBLIC_DOMAIN}-chain.pem`

With the default `PUBLIC_DOMAIN=mach2.disyepd.com`, the required files are:

- `../secrets/nuts-node/tls/mach2.disyepd.com.pem`
- `../secrets/nuts-node/tls/mach2.disyepd.com.key`
- `../secrets/nuts-node/tls/mach2.disyepd.com-chain.pem`

If these files are missing, `nuts-node` cannot start. If you change
`PUBLIC_DOMAIN`, you must also provide matching filenames in that folder.

For the local fake-UZI/TLS workflow, see:

- [`../services/nuts-node/certs/create_fake_UZI_cert.md`](../services/nuts-node/certs/create_fake_UZI_cert.md)

### Mock Receiver DEZI Material

`mock-notification-receiver` mounts the full `../secrets` tree and, by default,
looks for:

- `../secrets/mock-notification-receiver/dezi/certificaat_SYS_DEZI.crt`
- `../secrets/mock-notification-receiver/dezi/sleutel_SYS_DEZI.key`

Those files are required for the receiver-side DEZI login flow and for the UI
`pull` action that exchanges the DEZI-backed sender token.

The service can still start and receive notification `Task` resources without
them, but `/dezi` login and operator pull actions will fail when the certificate
or key is missing or invalid.

Also verify the runtime DEZI settings in
[`../services/mock-notification-receiver/.env`](../services/mock-notification-receiver/.env):

- `MOCK_RECEIVER_DEZI_CLIENT_ID`
- `MOCK_RECEIVER_PUBLIC_ROOT`
- `MOCK_RECEIVER_DEZI_CALLBACK_PATH`

Those values must match the DEZI client registration. If the public root or
callback path differs from what is registered, login will fail even when the
certificate files are present.

### Caddy Mode

The shipped `.env.example` uses `Caddyfile.local`, which is the easiest local
setup. If you switch to `Caddyfile`, you must also provide
`../secrets/cloudflare_api_token`.

With `Caddyfile.local`, browsers trust the HTTPS endpoint only after you trust
Caddy's local root CA. If you do not need browser HTTPS, you can also use the
direct `localhost` service endpoints instead.

## Start

From the repository root:

```bash
cd start-stack
docker compose up -d
```

The first start can take a few minutes because the HAPI servers, Postgres, and
the helper health containers must be ready before the application services and
one-shot seed jobs can finish.

## Main Endpoints

- ITI-91 API docs: <http://localhost:8509/docs>
- ITI-90 API docs: <http://localhost:8000/docs>
- Sender gateway: <http://localhost:8001/health>
- Mock receiver: <http://localhost:8002/health>
- Directory FHIR: <http://localhost:8080/fhir>
- Update-client FHIR: <http://localhost:8081/fhir>
- Notified-pull FHIR: <http://localhost:8082/fhir>
- Nuts node: <http://localhost:8083/health>
- Redis: `localhost:16379`
- Postgres: `localhost:5432`
- Caddy public HTTPS: <https://localhost:443> when `caddy` is enabled

## Expected State

Check:

```bash
docker compose ps --all
```

Default healthy behavior:

- long-running services show `Up`
- `iti-130-publisher` finishes as `Exited (0)`
- `notifiedpull-seed` finishes as `Exited (0)`
- `iti-91-mcsd-update-client` becomes healthy on `/health`
- the HAPI helper containers become healthy and stay running

`iti-130-publisher` and `notifiedpull-seed` are supposed to exit successfully.
That is normal.

## Verify

Run from the host:

```bash
docker compose ps --all
curl http://localhost:8509/health
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl 'http://localhost:8080/fhir/Organization?_summary=count&_count=1'
curl 'http://localhost:8082/fhir/Task?_summary=count&_count=1'
```

What to expect by default:

- ITI-91 health returns HTTP `200`
- ITI-90 health returns HTTP `200`
- sender gateway and mock receiver health endpoints return HTTP `200`
- the ITI-130 demo seed publishes 13 `Organization` resources to `hapi-directory`
- the notified-pull seed publishes 1 `Task` to `hapi-notifiedpull-stu3`

## Configuration Map

Use these files as the main configuration surface:

- `iti-91.conf`: ITI-91 runtime config mounted as `/src/app.conf`
- `.env`: Compose profile toggles and shared stack-level overrides
- `../services/iti-90/.env.Docker`: ITI-90 runtime config loaded via `env_file`
- `client.application.yaml`: HAPI update-client config
- `directory.application.yaml`: HAPI directory config
- `notifiedpull-stu3.application.yaml`: HAPI notified-pull config
- `create-dbs.sql`: Postgres initialization for first boot

Stack-specific items that are easy to miss:

- `.env:MOCK_RECEIVER_REQUIRED_INCOMING_SCOPE`
- `.env:MOCK_RECEIVER_SENDER_DATA_SCOPE`
- `.env:BGZ_GATEWAY_REQUIRED_SCOPES`
- `../services/iti-90/.env.Docker:MCSD_RECEIVER_NOTIFICATION_SCOPE`
- `../services/iti-90/.env.Docker:MCSD_SENDER_*`

## Common Operations

Re-run the ITI-130 seed job:

```bash
docker compose run --rm iti-130-publisher
```

Re-run the notified-pull seed bundle:

```bash
docker compose run --rm notifiedpull-seed
```

Follow the most useful logs:

```bash
docker compose logs -f iti-130-publisher iti-91-mcsd-update-client iti-90-address-book-proxy sender-bgz-gateway mock-notification-receiver
```

Stop the stack:

```bash
docker compose down
```

This stack does not use a named Postgres volume. A plain `docker compose down`
removes the Postgres container and resets the stored FHIR state for the next
run. Use `docker compose down -v` only when you also want to remove the optional
Caddy volumes.

## Notes

- ITI-90 enforces `MCSD_ALLOWED_HOSTS`. Access it via `localhost:8000` or
  another host listed in `../services/iti-90/.env.Docker`.
- ITI-91 starts background sync immediately. The shipped config points at the
  external test LRZa `https://knooppunt-test.nuts-services.nl/lrza/mcsd`, so a
  healthy container does not guarantee all remote directories synced cleanly.
- Without Caddy, use the direct local HAPI and FastAPI endpoints.

## Local Test Certificate Helper

Generate the local ITI-90 test UZI mTLS material from the repo root with:

```bash
./scripts/setup-test-uzi-mtls.sh
```

## Service Tests Without Full Startup

```bash
docker compose -f start-stack/docker-compose.yaml run --rm --no-deps --entrypoint pytest iti-90-address-book-proxy -vv tests
docker compose -f start-stack/docker-compose.yaml run --rm --no-deps --entrypoint pytest iti-91-mcsd-update-client -vv tests
docker compose -f start-stack/docker-compose.yaml run --rm --no-deps --entrypoint pytest iti-130-publisher -vv tests
docker compose -f start-stack/docker-compose.yaml run --rm --no-deps --entrypoint sh sender-bgz-gateway -lc "pip install --quiet pytest && pytest -vv tests"
docker compose -f start-stack/docker-compose.yaml run --rm --no-deps --entrypoint sh mock-notification-receiver -lc "pip install --quiet pytest && pytest -vv tests"
```

At the moment, `sender-bgz-gateway` and `mock-notification-receiver` have test
directories in the repo, but their images do not install `pytest` by default.
That is why their one-off test commands install `pytest` first.

The remaining stack components do not currently ship a local pytest suite in
this repository.
