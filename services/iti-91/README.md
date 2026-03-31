# ITI-91 mCSD Update Client

This service implements mCSD ITI-91 update behavior for PoC-9 and synchronizes
mCSD resources from one or more source directories into the configured update
client FHIR server.

The implementation originated from the reference project
[`minvws/gfmodules-mcsd-update-client`](https://github.com/minvws/gfmodules-mcsd-update-client),
but in this repository it has been extended substantially for PoC operations.

## Running in this repository

For end-to-end local use, run this service through the Compose stack in
[`../../poc9-start-stack/README.md`](../../poc9-start-stack/README.md). In that
setup:

- the service starts as `iti-91-mcsd-update-client`
- `../../poc9-start-stack/iti-91.conf` is mounted as `/src/app.conf`
- Postgres, Redis, `hapi-update-client`, and `hapi-directory` are provided by
  the same Compose network
- the background scheduler starts immediately at bootstrap

If you start the service directly from `services/iti-91`, it looks for
`app.conf` in the current working directory by default. You can also switch to
`app.<env>.conf` by setting `APP_ENV=<env>`.

## Status in this repository

This service is no longer "just the reference implementation". Compared to the
upstream reference, `services/iti-91` contains major functional additions and
behavior changes, including:

- Directory registry persistence and APIs:
  - New provider and provider-directory tables/migrations (`sql/017-...`)
  - New `/admin/directory-registry/*` endpoints
  - Manual directory registration and provider refresh workflows
- Registry-backed directory sourcing:
  - Optional `use_directory_registry_db` mode
  - Multiple `directories_provider_urls` support
  - Endpoint-address deduping and origin tracking (`provider` vs `manual`)
- More resilient FHIR directory discovery:
  - Fallback from `Organization` search to `Endpoint` search
  - Better pagination handling for expiring `_getpages` links
  - More permissive reference parsing for absolute and query references
- Update/sync engine hardening:
  - Optional parallel updates (`scheduler.max_concurrent_directory_updates`)
  - Per-directory lock protection and richer update status responses
  - Resource dependency ordering before transaction bundle submission
  - Better unresolved-reference handling (best-effort continuation)
  - Bulk cache existence checks and batched resource-map writes
- Cache and HTTP robustness improvements:
  - Namespaced per-run cache keys, TTL support, and Redis health fallback
  - Retry logic for transient HTTP failures/statuses with backoff + jitter
  - Connection pooling support for outbound HTTP calls
- Operational lifecycle controls:
  - Reason tracking for ignored directories (`reason_ignored`)
  - Improved stale/offline/error handling and cleanup paths
  - Timezone-safe stale/deletion processing

## Why this is PoC-ready

For PoC execution, this implementation is ready because it supports practical
cross-directory synchronization under imperfect real-world conditions:

- It can continuously discover and refresh directory endpoints from LRZa and/or
  manually registered sources.
- It tolerates partial interoperability mismatches and transient failures well
  enough to keep PoC flows running.
- It includes admin endpoints to inspect/adjust provider and directory state
  without redeploying.
- It has explicit lifecycle logic for stale, ignored, removed, and deleted
  directories.
- It includes significant automated coverage in `tests/` for update flow,
  caching, directory registry behavior, scheduler behavior, and FHIR reference
  handling.

## Current PoC settings (`poc9-start-stack/iti-91.conf`)

The running PoC stack mounts
[`../../poc9-start-stack/iti-91.conf`](../../poc9-start-stack/iti-91.conf) as
`/src/app.conf` in the ITI-91 container.

Key active settings are:

- `[app]`: `loglevel=debug`
- `[scheduler]`: `delay_input=5m`, automatic update/cleanup enabled
- `[mcsd]`: `authentication=off`, `check_capability_statement=False`,
  `require_mcsd_profiles=False`, `allow_missing_resources=True`
- `[uvicorn]`: `reload=True`, `swagger_enabled=True`, `use_ssl=False`
- `[external_cache]`: Redis enabled, `ssl=False`
- `[client_directory]`:
  - `directories_provider_urls=https://knooppunt-test.nuts-services.nl/lrza/mcsd`
  - `use_directory_registry_db=True`
  - lifecycle thresholds enabled for unhealthy/ignored/deleted handling

## Operational caveat with the shipped PoC config

The default `directories_provider_urls` points to an external test LRZa. That
means the container can be healthy and reachable on `/health` while background
directory updates still log validation, data-shape, or interoperability errors
from remote directories. This does not prevent the local stack from booting, but
it does mean update completeness depends on the current state of that external
test environment.

## Why current PoC settings are not production-ready

The following active settings deliberately make this deployment more forgiving
for PoC usage, but less strict/secure than production:

| Setting (current value) | PoC benefit | Production concern |
| --- | --- | --- |
| `mcsd.check_capability_statement=False` | Allows syncing from endpoints that do not fully advertise capabilities | Can ingest from non-compliant servers without early failure |
| `mcsd.require_mcsd_profiles=False` | Accepts servers that do not declare expected mCSD/NL-GF profiles | Reduces profile-level interoperability guarantees |
| `mcsd.allow_missing_resources=True` | Skips unsupported resource types instead of failing the whole update | Can silently produce partial datasets |
| `mcsd.authentication=off` | Simplifies local integration and testing | No authentication/authorization protection |
| `uvicorn.reload=True` | Faster development iteration | Dev-mode behavior; in this codebase also enables permissive CORS (`*`) |
| `uvicorn.use_ssl=False` | Simplifies local networking | API traffic is unencrypted |
| `external_cache.ssl=False` | Simplifies local Redis setup | Cache traffic is unencrypted |
| `app.loglevel=debug` | More troubleshooting detail during PoC | Verbose logs may expose sensitive operational data |
| `directories_provider_urls=.../knooppunt-test...` | Directly uses a test LRZa source | Depends on non-production upstream service behavior |

Additional production gaps in this PoC profile:

- default local DB credentials are used in the DSN
- telemetry and stats are disabled (`enabled=False`)
- automatic background update and cleanup start immediately at bootstrap

## Service docs

- Architecture and behavior details: [`docs/README.md`](docs/README.md)
- Full PoC stack startup/operations: [`../../poc9-start-stack/README.md`](../../poc9-start-stack/README.md)
- Repository-level setup and licensing context: [`../../README.md`](../../README.md)

## Setup context

To test ITI-91 behavior you need at least:

- one update client FHIR store
- one or more source directory FHIR stores

This repository provides those dependencies through `poc9-start-stack`.

For a reproducible local setup, prefer the Compose stack over a standalone run.

## Docker container builds

`make container-build` and `make container-build-sa` are convenience wrappers
from the original reference project. On environments without GNU `make`
(for example many Windows setups), use the direct `docker build` commands below.

Default mode (runs with `docker/init.sh` entrypoint):

```bash
cd services/iti-91
docker build --build-arg NEW_UID=1000 --build-arg NEW_GID=1000 -f docker/Dockerfile .
```

Standalone mode (uses `docker/init-standalone.sh` entrypoint):

```bash
cd services/iti-91
docker build --build-arg standalone=true -f docker/Dockerfile .
```

Standalone mode expects a config mounted as `/src/app.conf` at runtime.

If GNU `make` is installed, these wrappers are equivalent:

```bash
make container-build
make container-build-sa
```

## Licensing

- Service code: EUPL-1.2 (`LICENSE.md` in this folder)
- Repository-level context: [`../../README.md`](../../README.md)
