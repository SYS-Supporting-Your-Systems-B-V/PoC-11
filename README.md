# PoC 11-13 SYS

## Purpose

This repository contains the local PoC 11/13 stack for mCSD directory exchange
and BgZ notified-pull testing around:

- [ITI-90](https://profiles.ihe.net/ITI/mCSD/ITI-90.html)
- [ITI-91](https://profiles.ihe.net/ITI/mCSD/ITI-91.html)
- [ITI-130](https://profiles.ihe.net/ITI/mCSD/ITI-130.html)
- Twiin TA Notified Pull notification and follow-up pull behavior
- Dutch Generic Functions (GF) addressing, authentication, and authorization
  concepts, including `Twiin-TA-notification`, `Nuts-OAuth`, and
  `authorization-base`

The goal is to provide a runnable integration environment where directory data
can be published, aggregated, searched, used for receiver endpoint discovery,
and then exercised through the sender-side BgZ notified-pull follow-up flow.
The stack is intended to show how the directory, Twiin TA Notified Pull, Nuts
authentication, and sender-side authorization pieces work together in one local
PoC environment.

## Scope

Included:

- `services/iti-130`: one-shot directory publisher
- `services/iti-91`: update client plus directory-registry extensions
- `services/iti-90`: address-book proxy and notification builder
- `services/sender-bgz-gateway`: protected sender-side follow-up API
- `services/nuts-node`: local Nuts node configuration and policies
- `start-stack`: the canonical Docker Compose setup
- three local HAPI FHIR servers for directory, update-client, and notified-pull
  storage
- Postgres, Redis, and optional Caddy
- PoC-level GF authentication and authorization checks around Nuts token
  issuance/introspection, workflow-task `authorization-base`, organization
  authorization, healthcare-professional role claims, and authorized FHIR paths

> [!CAUTION]
> This repository is for PoC, test, and documentation use only. It is not
> production-ready.

Out of scope:

- production hardening and deployment automation
- real organization-specific secrets, certificates, and DNS ownership
- full conformance certification for every external mCSD or BgZ participant
- long-term persistence guarantees for local demo data

## Architecture

The default local flow is:

1. `iti-130-publisher` seeds demo mCSD resources into `hapi-directory`.
2. `iti-91-mcsd-update-client` discovers source directories from config,
   provider URLs, or its registry DB, rewrites source-local ids/references, and
   writes aggregated resources into `hapi-update-client`.
3. `iti-90-address-book-proxy` reads the aggregated directory, exposes
   operator-friendly search/discovery endpoints, resolves receiver capabilities,
   and builds Twiin TA Notified Pull notification `Task` resources for the BgZ
   flow.
4. `nuts-node` is used by the sender flow for GF/Nuts token-related
   authentication integration, including receiver token requests and gateway
   token introspection.
5. `sender-bgz-gateway` protects follow-up FHIR reads and task updates against
   the internal `hapi-notifiedpull-stu3` sender store by checking
   `authorization-base`, organization authorization, DEZI-claims and role codes, and
   the FHIR paths allowed by the workflow task.
6. Optional `caddy` exposes local HTTPS routes for the configured public domain.

Supporting services:

| Component | Role | Default host port |
| --- | --- | --- |
| `iti-91-mcsd-update-client` | mCSD update client API and scheduler | `8509` |
| `iti-90-address-book-proxy` | address book, capability mapping, BgZ notify helper | `8000` |
| `sender-bgz-gateway` | protected sender-side follow-up FHIR gateway | `8001` |
| `hapi-directory` | source directory FHIR store seeded by ITI-130 | `8080` |
| `hapi-update-client` | aggregated update-client FHIR store | `8081` |
| `hapi-notifiedpull-stu3` | sender notified-pull workflow/data store | `8082` |
| `nuts-node` | local Nuts APIs | `8083`, `8084` |
| `postgres` | ITI-91 and HAPI persistence | `5432` |
| `redis` | ITI-91 external cache | `16379` |
| `caddy` | optional public-domain HTTPS facade | `443` |

## Installation And Startup

Prerequisites:

- Docker Desktop or Docker Engine with the Compose plugin
- free host ports: `443` (when `caddy` is enabled), `5432`, `8000`, `8001`,
  `8002`, `8080`, `8081`, `8082`, `8083`, `8084`, `8509`, `16379`
- runtime secrets under `secrets/`

Before first start, verify these setup items:

- if `start-stack/.env` is missing in a fresh clone, create it from the example:
  `cp start-stack/.env.example start-stack/.env`
- `start-stack/.env`: `PUBLIC_DOMAIN` must match the Nuts TLS filenames under
  `secrets/nuts-node/tls/`. With the default domain that means:
  `mach2.disyepd.com.pem`, `mach2.disyepd.com.key`, and
  `mach2.disyepd.com-chain.pem`.
- when `COMPOSE_PROFILES=caddy` and `CADDYFILE_NAME=Caddyfile`, provide
  `secrets/cloudflare_api_token`; for local-only HTTPS use `Caddyfile.local`
  instead.

Review these files before starting the stack:

- `start-stack/.env`
- `start-stack/iti-91.conf`
- `services/iti-90/.env.Docker`
- `SECRETS.md`

Start the stack:

```bash
cd start-stack
docker compose up -d
```

Verify the default boot state:

```bash
docker compose ps --all
curl http://localhost:8509/health
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl 'http://localhost:8080/fhir/Organization?_summary=count&_count=1'
curl 'http://localhost:8082/fhir/Task?_summary=count&_count=1'
```

Expected by default:

- long-running services are `Up`
- one-shot jobs `iti-130-publisher` and `notifiedpull-seed` end as `Exited (0)`
- the directory FHIR store contains the seeded ITI-130 demo data
- the notified-pull FHIR store contains the seeded workflow `Task`

For a fuller startup checklist, reset commands, common operations, and
containerized test commands, use [`start-stack/README.md`](start-stack/README.md).

## Configuration Files And Test Scripts

Main configuration surfaces:

| Path | Purpose |
| --- | --- |
| `start-stack/docker-compose.yaml` | canonical integrated local stack |
| `start-stack/.env.example` | Compose profile, public-domain, and gateway-scope defaults |
| `start-stack/iti-91.conf.example` | fork/starter template for ITI-91 runtime settings |
| `start-stack/iti-91.conf` | active local ITI-91 config mounted into the stack |
| `start-stack/*.application.yaml` | HAPI FHIR server configs |
| `start-stack/create-dbs.sql` | first-boot Postgres database creation |
| `services/iti-90/.env.example` | ITI-90 standalone env template |
| `services/iti-90/.env.Docker` | ITI-90 env file used by Compose |
| `services/iti-90/.env.pytest` | ITI-90 test env defaults |
| `services/iti-91/app.conf`, `services/iti-91/app.test.conf` | ITI-91 local/test configs |
| `services/iti-91/directory_urls.example.json` | file-based directory discovery example |
| `services/sender-bgz-gateway/.env.example` | sender gateway standalone env template |
| `services/nuts-node/nuts.yaml` | Nuts node runtime config |
| `services/nuts-node/policies/*.json` | local Nuts policy material |
| `SECRETS.md` | expected local secret/certificate layout |

Test and helper entry points:

| Path or command | Purpose |
| --- | --- |
| `services/iti-90/tests/` | ITI-90 address-book, capability, and BgZ endpoint tests |
| `services/iti-91/tests/` | ITI-91 scheduler, registry, update, cache, model, and router tests |
| `services/iti-130/tests/` | ITI-130 mapping, bundle, config, and end-to-end script tests |
| `services/sender-bgz-gateway/tests/` | sender gateway authorization and FHIR proxy tests |
| `services/iti-91/Makefile` | lint, type-check, audit, spelling, and test targets for ITI-91 |
| `scripts/setup-test-uzi-mtls.sh` | generates local ITI-90 test UZI mTLS material |
| `services/nuts-node/certs/generate-certs.sh` | helper for local Nuts certificate material |

Containerized service-test commands are documented in
[`start-stack/README.md`](start-stack/README.md#service-tests-without-full-startup).

## API Interfaces

Interactive API documentation is available after the stack starts:

| Interface | Docs or base URL | Notes |
| --- | --- | --- |
| BgZ demo UI | <http://HOST-IP-OR-NAME:8000/mcsd_bgz_verwijzing/> | functional UI that connects directory search, capability mapping, notification creation, Nuts token flow, and sender follow-up components |
| BgZ demo UI, local equivalent | <http://localhost:8000/mcsd_bgz_verwijzing/> | same UI when the stack is accessed from the host running Docker |
| ITI-91 update client | <http://localhost:8509/docs> | FastAPI docs for health, directory, scheduler, update, registry, and resource-map APIs |
| ITI-90 address-book proxy | <http://localhost:8000/docs> | FastAPI docs for address-book, MSZ discovery, capability mapping, and BgZ helper APIs |
| Sender BgZ gateway | <http://localhost:8001/docs> | FastAPI docs for protected sender-side FHIR routes |
| Directory FHIR server | <http://localhost:8080/fhir> | HAPI FHIR base seeded by `iti-130-publisher` |
| Update-client FHIR server | <http://localhost:8081/fhir> | HAPI FHIR base populated by ITI-91 |
| Notified-pull FHIR server | <http://localhost:8082/fhir> | HAPI STU3 sender workflow/data store |
| Nuts node | <http://localhost:8083/health> | local Nuts node health endpoint |

The service READMEs document the stable route groups and important request
rules:

- [`services/iti-90/README.md`](services/iti-90/README.md): `/addressbook/*`,
  `/mcsd/search/*`, `/poc9/msz/*`, and `/bgz/*`
- [`services/iti-91/README.md`](services/iti-91/README.md): `/directory/*`,
  `/directory_ignore_list/*`, `/update_resources`, `/scheduler/*`,
  `/resource_map`, and `/admin/directory-registry/*`
- [`services/sender-bgz-gateway/README.md`](services/sender-bgz-gateway/README.md):
  protected `/fhir/metadata`, `/fhir/Task`, `/fhir/Patient`, and
  `/fhir/Observation` routes
- [`services/iti-130/README.md`](services/iti-130/README.md): publisher CLI
  interface, environment variables, and generated resource semantics

## Dependencies

Runtime/platform dependencies:

- Docker with Compose plugin for the integrated stack
- Python 3.11-based service images
- Postgres 15, Redis 7.2, HAPI FHIR, Caddy, curl helper containers, and Nuts
  node as declared in [`start-stack/docker-compose.yaml`](start-stack/docker-compose.yaml)
- local certificate and secret files described in [`SECRETS.md`](SECRETS.md)

Dependency manifests:

| Path | Purpose |
| --- | --- |
| `services/iti-90/requirements.in`, `services/iti-90/requirements.txt` | pinned ITI-90 Python dependencies |
| `services/iti-130/requirements.in`, `services/iti-130/requirements.txt` | pinned ITI-130 Python dependencies |
| `services/sender-bgz-gateway/requirements.in`, `services/sender-bgz-gateway/requirements.txt` | pinned sender gateway Python dependencies |
| `services/iti-91/pyproject.toml`, `services/iti-91/poetry.lock` | ITI-91 Poetry dependencies and dev tooling |
| `start-stack/THIRD_PARTY_CONTAINER_IMAGES.md` | runtime container-image provenance and notices |
| `services/*/THIRD_PARTY_LICENSES.md` | service-level third-party dependency notices |

## Specs And Docs

- Stack startup and operations: [`start-stack/README.md`](start-stack/README.md)
- Secret layout and test certificates: [`SECRETS.md`](SECRETS.md)
- Nuts TLS certificate notes:
  [`services/nuts-node/certs/create_fake_UZI_cert.md`](services/nuts-node/certs/create_fake_UZI_cert.md)
- ITI-90 service docs: [`services/iti-90/README.md`](services/iti-90/README.md)
- ITI-91 service docs: [`services/iti-91/README.md`](services/iti-91/README.md)
- ITI-91 architecture notes: [`services/iti-91/docs/README.md`](services/iti-91/docs/README.md)
- ITI-130 service docs: [`services/iti-130/README.md`](services/iti-130/README.md)
- Sender gateway docs: [`services/sender-bgz-gateway/README.md`](services/sender-bgz-gateway/README.md)

## Operational Notes

- `start-stack` is the supported way to run the repo locally end-to-end.
- The shipped `start-stack/.env.example` enables the `caddy` profile with
  `Caddyfile.local`, which uses a local development certificate instead of the
  Cloudflare-backed setup.
- `start-stack/iti-91.conf` points ITI-91 at the external test LRZa
  `https://knooppunt-test.nuts-services.nl/lrza/mcsd`, so `/health` can be
  green while individual remote syncs still log interoperability issues.
- Postgres is not mounted to a named volume in the Compose stack. A plain
  `docker compose down` resets the stored FHIR state for the next run.

## Licensing

- Repository code: MIT by default, except where service-specific licenses apply
- `services/iti-91`: EUPL-1.2 in that service folder
- Documentation: CC BY-SA 4.0
- Third-party dependencies: see service-level `THIRD_PARTY_LICENSES.md` files
- Distribution notices: see `DISTRIBUTION_NOTICES.md`
- Runtime container image notices: see `start-stack/THIRD_PARTY_CONTAINER_IMAGES.md`
