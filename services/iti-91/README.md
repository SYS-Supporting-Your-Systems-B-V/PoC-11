# ITI-91 mCSD Update Client

This service implements mCSD ITI-91 update behavior for the PoC 11/13 stack. It
polls one or more source directories, rewrites source-local ids and references,
and writes the transformed resources into the configured update-client FHIR
server.

The code started from the reference project
`minvws/gfmodules-mcsd-update-client`, but this repository now carries
PoC-specific extensions on top of it.

## What Is Different In This Repo

Compared with the original reference implementation, this tree adds and relies
on:

- directory-registry persistence and admin APIs
- provider refresh flows and manual directory registration
- more tolerant reference parsing and pagination handling
- cache, retry, and connection-pooling hardening
- per-directory lifecycle handling for unhealthy, ignored, and deleted entries
- scheduler controls for background update and cleanup runs

So in practice this service should be read as a PoC-specific update client with
substantial local behavior, not as a verbatim copy of the upstream reference
project.

## Why This Service Matters In The Stack

The update client is the bridge between external source directories and the
local aggregated directory used by the rest of this PoC.

In practical terms:

- ITI-130 seeds a local source-style directory
- ITI-91 discovers one or more external or local source directories
- ITI-91 rewrites source-local ids and references
- ITI-91 writes the transformed resources into the update-client FHIR store
- downstream components such as the receiver and sender flows can use that
  aggregated directory view without having to understand each upstream source
  separately

That is why this service is more than a scheduler wrapper. The id/rewrite and
directory-lifecycle logic are the part that makes multiple sources coexist in
one target store without collisions.

## In This Repository

For end-to-end local use, start the stack via
[`../../start-stack/README.md`](../../start-stack/README.md). In that setup:

- the container name is `iti-91-mcsd-update-client`
- `../../start-stack/iti-91.conf` is mounted as `/src/app.conf`
- the service listens on port `8509`
- Postgres, Redis, `hapi-directory`, and `hapi-update-client` are provided by
  the same Compose network

If you start the service directly from `services/iti-91`, it reads `app.conf`
from the current working directory by default. Set `APP_ENV=<name>` to load
`app.<name>.conf` instead.

For most work, the Compose stack is the safer path because it already provides
the paired FHIR servers, Redis, and Postgres wiring that the service expects.

## Main Routes

Basic:

- `GET /`
- `GET /version.json`
- `GET /health`
- `GET /update_client`

Directory status and metrics:

- `GET /directory/health`
- `GET /directory/metrics`
- `GET /directory/all`
- `GET /directory/{id}`

Ignore list:

- `GET /directory_ignore_list/all`
- `GET /directory_ignore_list/{directory_id}`
- `POST /directory_ignore_list/{directory_id}`
- `DELETE /directory_ignore_list/{directory_id}`

Update and scheduler control:

- `POST /update_resources`
- `POST /update_resources/{directory_id}`
- `POST /scheduler/update/start`
- `POST /scheduler/update/stop`
- `GET /scheduler/update/runner_logs`
- `POST /scheduler/cleanup/start`
- `POST /scheduler/cleanup/stop`
- `GET /scheduler/cleanup/runner_logs`

Registry and resource mapping:

- `GET /resource_map`
- `GET /admin/directory-registry/providers`
- `POST /admin/directory-registry/providers`
- `POST /admin/directory-registry/providers/refresh`
- `POST /admin/directory-registry/directories`

## Route Guide

The route list above is easier to use when grouped by operational intent:

- `GET /health` answers "is the service process alive?"
- `GET /directory/health` answers "what is the health state of the known
  directories?"
- `GET /directory/all` and `GET /directory/{id}` are the main inspection routes
  for current directory state
- `POST /update_resources` and `POST /update_resources/{directory_id}` trigger a
  foreground sync run immediately
- `/scheduler/*` starts, stops, and inspects the background update/cleanup
  runners
- `/admin/directory-registry/*` manages provider and manual-directory discovery
  inputs
- `GET /resource_map` is the inspection surface for source-to-target mapping
  state

That distinction matters operationally: a healthy process with a running
scheduler can still have unhealthy source directories, ignored entries, or
partially synchronized remote data.

## Current Stack Settings

The default local stack mounts
[`../../start-stack/iti-91.conf`](../../start-stack/iti-91.conf). Important
active settings there are:

- `app.loglevel=debug`
- `scheduler.delay_input=5m`
- `scheduler.automatic_background_update=True`
- `scheduler.automatic_background_cleanup=True`
- `mcsd.authentication=off`
- `mcsd.check_capability_statement=False`
- `mcsd.require_mcsd_profiles=False`
- `mcsd.allow_missing_resources=True`
- `external_cache.ssl=False`
- `uvicorn.reload=True`
- `client_directory.directories_provider_urls=https://knooppunt-test.nuts-services.nl/lrza/mcsd`
- `client_directory.use_directory_registry_db=True`

Those defaults are intentionally forgiving for PoC use and are not
production-grade settings.

That tradeoff is deliberate: the service prefers to keep synchronization moving
through partial interoperability problems instead of failing early on every
upstream inconsistency.

## Configuration Guidance

The mounted `iti-91.conf` is the real control plane for this service. The most
important sections are:

- `[mcsd]` for source validation behavior, auth mode, and target update-client
  base URL
- `[client_directory]` for where directories are discovered and how lifecycle
  state is handled
- `[scheduler]` for background run cadence and retention
- `[external_cache]` for Redis-backed caching
- `[database]` for Postgres connectivity and pooling
- `[uvicorn]` for the local API listener and dev-mode behavior

A few settings are especially important to understand:

- `mcsd.update_client_url` is the FHIR target this service writes into
- `client_directory.directories_provider_urls` points discovery at one or more
  LRZa/provider endpoints
- `client_directory.use_directory_registry_db=True` means the DB is part of the
  discovery state, not just a transient scratch store
- lifecycle thresholds such as
  `directory_marked_as_unhealthy_after_success_timeout` and
  `ignore_client_directory_after_failed_attempts_threshold` control when remote
  sources move through degraded states
- `scheduler.automatic_background_update=True` and
  `scheduler.automatic_background_cleanup=True` mean the service starts active
  background behavior as soon as it boots

Because the service keeps registry and resource-map state in Postgres, the DB is
not just an implementation detail. It is part of the operational behavior.

## Why The Shipped Config Is PoC-Friendly But Not Production-Ready

The mounted default config intentionally biases toward interoperability and
debuggability instead of strictness.

Important examples from the current stack config:

- `mcsd.authentication=off`
- `mcsd.check_capability_statement=False`
- `mcsd.require_mcsd_profiles=False`
- `mcsd.allow_missing_resources=True`
- `uvicorn.reload=True`
- `uvicorn.use_ssl=False`
- `external_cache.ssl=False`
- `app.loglevel=debug`

Those settings help the PoC because they allow the client to keep processing
through imperfect upstream directories and make debugging easier. They are not
good production defaults because they reduce strict validation, weaken
transport/security posture, and increase the chance of partial-but-accepted
data.

## Why This Is Still Useful For PoC Work

Despite those relaxed settings, the current implementation is operationally
useful for PoC work because it adds exactly the pieces that a real multi-source
demo needs:

- persistent provider and directory registry state
- manual provider refresh and directory registration
- lifecycle handling for ignored, unhealthy, and deleted directories
- retry, cache, and connection-pooling hardening
- more tolerant reference parsing and pagination handling
- source-to-target resource tracking through the resource map

That is the difference between "a reference client that can sync in ideal
conditions" and "a PoC client that keeps moving when upstreams are inconsistent
or partially broken".

## Operational Caveat

The shipped configuration points at the external test LRZa
`https://knooppunt-test.nuts-services.nl/lrza/mcsd`. The service can therefore
be healthy on `/health` while individual background directory updates still log
validation or interoperability errors from remote systems.

So `/health` only tells you the client itself is up. It does not guarantee that
every configured source directory is currently valid, reachable, or fully
ingested.

## Setup Context

To exercise this service meaningfully you need at least:

- one update-client FHIR store
- one or more source directory FHIR stores or provider endpoints
- Postgres for directory and resource-map persistence
- Redis for the configured external cache behavior

That is why the Compose stack is the recommended local path. A standalone run is
possible, but it is much easier to misread failures when the paired target
stores and support services are not already present.

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install poetry
poetry install --no-root
python -m app.main
```

## Tests

```bash
pytest -vv tests
```

The test suite in this repo covers scheduler behavior, directory registry
handling, update flow, reference rewriting, cache behavior, and router-level
API behavior.

## Related Docs

- Architecture notes: [`docs/README.md`](docs/README.md)
- Full stack: [`../../start-stack/README.md`](../../start-stack/README.md)
- Repository overview: [`../../README.md`](../../README.md)
