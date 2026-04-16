# PoC 11 - 13 SYS

This repository contains the integrated PoC 11 - 13 setup for mCSD-based workflows in
the "Generieke Functies, lokalisatie en addressering" context, covering ITI-90,
ITI-91, and ITI-130.

> [!CAUTION]
> This repository is for PoC/testing and documentation purposes. It is **NOT**
> intended for production use.

## Quickstart (10 min)

Prerequisites:

- Docker Desktop / Docker Engine with the Compose plugin
- the following host ports are free: `443` (only when using profile `caddy`),
  `5432`, `8000`, `8080`, `8081`, `8082`, `8509`, `16379`

1. Review local config files.

The repository already contains working PoC defaults in
`start-stack/.env`, `start-stack/iti-91.conf`, and
`services/iti-90/.env.Docker`. If you want to reset them to the shipped
templates, copy the example files below.

Bash:

```bash
cp start-stack/iti-91.conf.example start-stack/iti-91.conf
cp start-stack/.env.example start-stack/.env
```

PowerShell:

```powershell
Copy-Item start-stack/iti-91.conf.example start-stack/iti-91.conf
Copy-Item start-stack/.env.example start-stack/.env
```

2. Start the full stack.

```bash
cd start-stack
docker compose up -d
```

The first boot can take a few minutes because Postgres and the three HAPI
servers must become healthy before the application services and seed jobs can
finish.

3. Verify expected success state.

```bash
docker compose ps --all
curl http://localhost:8509/health
curl http://localhost:8000/health
curl 'http://localhost:8080/fhir/Organization?_summary=count&_count=1'
curl 'http://localhost:8082/fhir/Task?_summary=count&_count=1'
```

Expected:

- long-running services are `Up`
- one-shot seed jobs `iti-130-publisher` and `notifiedpull-seed` complete as
  `Exited (0)`
- ITI-91 and ITI-90 health endpoints return HTTP `200`
- the directory FHIR server returns seeded demo content (default stack: 13
  `Organization` resources)
- the notified-pull FHIR server contains seeded demo data (default stack: 1
  `Task`)
- API docs are reachable at <http://localhost:8509/docs> and <http://localhost:8000/docs>

## Current repository state

The repository started from reference implementations, but parts have evolved
into PoC-specific implementations:

- `services/iti-91` (ITI-91 update client) is no longer only a pure reference:
  it includes PoC-oriented extensions such as directory registry persistence,
  resilient sync behavior, and additional operational controls.
- `services/iti-90` is a PoC-focused address book proxy with BgZ workflow helpers.
- `services/iti-130` is a publisher utility for feeding source data into the
  directory model.
- `poc9-start-stack` is the main Docker Compose stack for running the full PoC.

For ITI-91 specifics and current PoC settings, see
[`services/iti-91/README.md`](services/iti-91/README.md).

## IHE transaction scope

This repository covers these IHE mCSD transactions:

- [ITI-90: Find Matching Care Services](https://profiles.ihe.net/ITI/mCSD/ITI-90.html)
- [ITI-91: Request Care Services Update](https://profiles.ihe.net/ITI/mCSD/ITI-91.html)
- [ITI-130: Care Services Feed](https://profiles.ihe.net/ITI/mCSD/ITI-130.html)

## PoC scope (sending party)

For PoC 8/9 Route 9, this repository focuses on the sending-party role in an
MSZ-to-MSZ BgZ referral flow, using GF Adressering with TA Routering.

Core capabilities in scope:

- mCSD directory publication and synchronization
- address book search/discovery for organizations, org units, and endpoints
- capability-based endpoint selection for BgZ routing
- sender-side notified-pull task composition for PoC flows

Technical baseline:
<https://nuts-foundation.github.io/nl-generic-functions-ig/care-services.html>

## Documentation map

- Full-stack startup and operations: [`poc9-start-stack/README.md`](poc9-start-stack/README.md)
- ITI-91 service overview and PoC settings: [`services/iti-91/README.md`](services/iti-91/README.md)
- ITI-91 architecture details: [`services/iti-91/docs/README.md`](services/iti-91/docs/README.md)
- ITI-130 usage details: [`services/iti-130/README.md`](services/iti-130/README.md)
- ITI-90 usage details: [`services/iti-90/README.md`](services/iti-90/README.md)

## Operational notes

- `start-stack` is the canonical way to run this repository locally. The
  service-level READMEs explain internals and standalone usage, but they assume
  the stack README for the end-to-end boot sequence.
- The default ITI-91 config points to the external test LRZa
  `https://knooppunt-test.nuts-services.nl/lrza/mcsd`. The service can be
  healthy while individual background sync attempts still log validation or
  interoperability errors from external directories.
- In this Compose setup Postgres is not mounted to a named volume. `docker
  compose down` therefore removes the database container and resets stored FHIR
  state for the next `up`.

## Start the full stack

From repository root:

```bash
cd poc9-start-stack
docker compose up -d
```

Main local endpoints:

- ITI-91 API docs: <http://localhost:8509/docs>
- ITI-90 API docs: <http://localhost:8000/docs>
- Directory FHIR: <http://localhost:8080/fhir>
- Update Client FHIR: <http://localhost:8081/fhir>
- Notified Pull FHIR: <http://localhost:8082/fhir>
- Postgres: `localhost:5432`
- Redis: `localhost:16379`
- Caddy HTTPS endpoint: <https://localhost:443> (only when profile `caddy` is enabled)

For stack profiles/config/seeding details, see
[`poc9-start-stack/README.md`](poc9-start-stack/README.md).

## Configuration checklist

Before starting the stack, verify:

1. `poc9-start-stack/iti-91.conf`
2. `poc9-start-stack/.env`
3. `services/iti-90/.env.Docker`
4. `secrets/cloudflare_api_token` (only when profile `caddy` is enabled)

Template files to copy when needed:

- `poc9-start-stack/iti-91.conf.example` -> `poc9-start-stack/iti-91.conf`
- `poc9-start-stack/.env.example` -> `poc9-start-stack/.env`

## Disclaimer

This project and associated code are provided for documentation, PoC, and
demonstration purposes only.

It is not production-ready and may contain omissions, simplifications, or
incomplete security hardening. Mostly due to absence of authentication and authorization, these are out of scope for PoC 9 and thus make the repository unfit for full production purposes.


## Licensing

- Code: MIT by default (see `LICENSE.md`), except where service-specific
  licenses apply (for example `services/iti-91`).
- Documentation: CC BY-SA 4.0 (see `LICENSES/CC-BY-SA-4.0.txt`).
- Third-party dependencies: see `THIRD_PARTY_LICENSES.md` files per service.
