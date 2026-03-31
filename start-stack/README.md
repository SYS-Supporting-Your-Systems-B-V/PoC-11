# PoC 9 Start Stack

This folder contains the Docker Compose stack used to run the full PoC locally:
ITI-91, ITI-90, ITI-130, three HAPI FHIR servers, Postgres, Redis, and an
optional Caddy reverse proxy.

## Prerequisites

- Docker Desktop / Docker Engine with the Compose plugin
- free host ports: `443` (only when using profile `caddy`), `5432`, `8000`,
  `8080`, `8081`, `8082`, `8509`, `16379`
- `../secrets/cloudflare_api_token` only when using profile `caddy`

## First-time setup

The repository already contains working PoC defaults in these tracked files:

- `poc9-start-stack/.env`
- `poc9-start-stack/iti-91.conf`
- `services/iti-90/.env.Docker`

If you want to reset them to the starter templates, copy:

- `iti-91.conf.example` -> `iti-91.conf`
- `.env.example` -> `.env`

Before you start, review at least:

- `iti-91.conf` for the ITI-91 update-client settings
- `.env` for optional Compose profiles such as `caddy`
- `../services/iti-90/.env.Docker` for ITI-90 upstream and sender settings

## Start

From the repository root:

```bash
cd poc9-start-stack
docker compose up -d
```

The first start can take a few minutes. HAPI FHIR must become healthy before the
app services and one-shot seed jobs can finish.

Main local endpoints:

- ITI-91 API docs: <http://localhost:8509/docs>
- ITI-90 API docs: <http://localhost:8000/docs>
- Directory FHIR: <http://localhost:8080/fhir>
- Update Client FHIR: <http://localhost:8081/fhir>
- Notified Pull FHIR: <http://localhost:8082/fhir>
- Postgres: `localhost:5432`
- Redis: `localhost:16379`
- Caddy HTTPS endpoint: <https://localhost:443> when profile `caddy` is enabled

## Expected state after startup

Use:

```bash
docker compose ps --all
```

Healthy default behavior looks like this:

| Service | Expected state | Notes |
| --- | --- | --- |
| `postgres` | `Up (healthy)` | Database for the HAPI and ITI-91 services |
| `redis` | `Up` | External cache for ITI-91 |
| `hapi-directory` | `Up` | Local source directory FHIR server |
| `hapi-update-client` | `Up` | ITI-91 target/update-client FHIR server |
| `hapi-notifiedpull-stu3` | `Up` | Local notified-pull FHIR server |
| `hapi-*-health` | `Up (healthy)` | Helper containers that gate startup |
| `iti-91-mcsd-update-client` | `Up (healthy)` | FastAPI service on port `8509` |
| `iti-90-address-book-proxy` | `Up` | FastAPI service on port `8000` |
| `iti-130-publisher` | `Exited (0)` | One-shot seed job for `hapi-directory` |
| `notifiedpull-seed` | `Exited (0)` | One-shot seed job for `hapi-notifiedpull-stu3` |
| `caddy` | `Up` | Only when profile `caddy` is enabled |

`iti-130-publisher` and `notifiedpull-seed` are supposed to finish and exit.
That is not a failure.

## Verify

Run these from the host:

```bash
docker compose ps --all
curl http://localhost:8509/health
curl http://localhost:8000/health
curl 'http://localhost:8080/fhir/Organization?_summary=count&_count=1'
curl 'http://localhost:8082/fhir/Task?_summary=count&_count=1'
```

What to expect:

- ITI-91 health returns HTTP `200`
- ITI-90 health returns HTTP `200`
- the default ITI-130 demo load publishes 13 `Organization` resources into
  `hapi-directory`
- the default notified-pull seed publishes 1 `Task`

Operational caveats that are easy to miss:

- ITI-90 enforces `MCSD_ALLOWED_HOSTS`. Access it via `localhost:8000` or
  another host listed in `services/iti-90/.env.Docker`; otherwise it returns
  `Invalid host header`.
- ITI-91 starts background sync immediately. With the shipped PoC config it
  connects to the external test LRZa
  `https://knooppunt-test.nuts-services.nl/lrza/mcsd`, so the service can be
  healthy while individual remote-directory updates still log validation or
  interoperability errors.

## Config map

Use this table to identify what must be configured for your own setup.

| File / Variable | Used by service(s) | Required | Purpose |
| --- | --- | --- | --- |
| `iti-91.conf` | `iti-91-mcsd-update-client` | Yes | Main ITI-91 runtime config (mounted to `/src/app.conf`) |
| `iti-91.conf:[mcsd]update_client_url` | `iti-91-mcsd-update-client` | Yes | Target FHIR base where ITI-91 writes synchronized resources |
| `iti-91.conf:[client_directory]directories_provider_urls` | `iti-91-mcsd-update-client` | Yes (when using LRZa discovery) | One or more directory-registry/LRZa endpoints |
| `.env` | `docker compose` | Usually | Local compose toggles (for example `COMPOSE_PROFILES`) |
| `.env:COMPOSE_PROFILES` | `docker compose` | No | Optional profile toggles such as `caddy` |
| `../services/iti-90/.env.Docker` | `iti-90-address-book-proxy` | Yes | ITI-90 runtime settings loaded via `env_file` |
| `../services/iti-90/.env.Docker:MCSD_BASE` | `iti-90-address-book-proxy` | Yes | Upstream mCSD/FHIR base URL for ITI-90 |
| `../services/iti-90/.env.Docker:MCSD_SENDER_*` | `iti-90-address-book-proxy` | Required for BgZ notify flow | Sender identity used in PoC notification flows |
| `client.application.yaml` | `hapi-update-client` | Yes | HAPI config for update-client-side FHIR server |
| `directory.application.yaml` | `hapi-directory` | Yes | HAPI config for source directory FHIR server |
| `notifiedpull-stu3.application.yaml` | `hapi-notifiedpull-stu3` | Yes | HAPI config for notified-pull FHIR server |
| `create-dbs.sql` | `postgres` | Yes | Database initialization script (first startup) |
| `../secrets/cloudflare_api_token` | `caddy` | Only if `caddy` profile enabled | Cloudflare API token used by Caddy |

## Seed and reset operations

Re-run the ITI-130 publisher seed:

```bash
docker compose run --rm iti-130-publisher
```

Re-run the notified-pull seed bundle:

```bash
docker compose run --rm notifiedpull-seed
```

Follow the most useful logs while debugging startup:

```bash
docker compose logs -f iti-130-publisher iti-91-mcsd-update-client iti-90-address-book-proxy
```

Stop the stack:

```bash
docker compose down
```

This stack does not mount Postgres to a named volume. `docker compose down`
therefore removes the Postgres container and resets stored stack state. Use
`docker compose down -v` only when you also want to remove the optional Caddy
volumes.

## Optional Caddy profile

Enable Caddy in one of these ways:

```bash
# option 1: in .env
COMPOSE_PROFILES=caddy

# option 2: command line
docker compose --profile caddy up -d
```

The Caddy profile requires `../secrets/cloudflare_api_token` and exposes
`mach2.disyepd.com` on port `443`.

Without Caddy, use the direct HAPI endpoints such as
`http://localhost:8080/fhir`.

## Running the pytests for each service without starting the full stack

```bash
# ITI-90:
docker compose -f poc9-start-stack/docker-compose.yaml run --rm --no-deps --entrypoint "pytest -vv tests" iti-90-address-book-proxy

# ITI-91:
docker compose -f poc9-start-stack/docker-compose.yaml run --rm --no-deps --entrypoint "pytest -vv tests" iti-91-mcsd-update-client

# ITI-130:
docker compose -f poc9-start-stack/docker-compose.yaml run --rm --no-deps --entrypoint "pytest -vv tests" iti-130-publisher
```

`docker compose run` starts `depends_on` services by default. `--no-deps` keeps
these test runs isolated, which is sufficient for the current test suites.
