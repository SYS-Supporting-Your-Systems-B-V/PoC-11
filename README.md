# PoC 11-13 SYS

This repository contains the local PoC 11/13 stack for mCSD directory exchange
and BgZ notified-pull testing around:

- [ITI-90](https://profiles.ihe.net/ITI/mCSD/ITI-90.html)
- [ITI-91](https://profiles.ihe.net/ITI/mCSD/ITI-91.html)
- [ITI-130](https://profiles.ihe.net/ITI/mCSD/ITI-130.html)

It includes:

- `services/iti-130`: one-shot directory publisher
- `services/iti-91`: update client plus directory-registry extensions
- `services/iti-90`: address-book proxy and notification builder
- `services/sender-bgz-gateway`: protected sender-side follow-up API
- `start-stack`: the canonical Docker Compose setup

> [!CAUTION]
> This repository is for PoC, test, and documentation use only. It is not
> production-ready.

## Quickstart

Prerequisites:

- Docker Desktop or Docker Engine with the Compose plugin
- free host ports: `443` (when `caddy` is enabled), `5432`, `8000`, `8001`,
  `8002`, `8080`, `8081`, `8082`, `8083`, `8084`, `8509`, `16379`
- runtime secrets under `secrets/`

Before first start, verify these setup items:

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
