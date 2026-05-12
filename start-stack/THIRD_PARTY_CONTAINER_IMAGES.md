# Third-Party Container Image Notices

Date: 2026-05-12

This retained notices bundle covers the runtime images referenced by
`start-stack/docker-compose.yaml` and the base images referenced by service
Dockerfiles used by that Compose stack.

This is a container/image notices bundle, not a package-level SBOM. It should be
kept with release artifacts and refreshed whenever `start-stack/docker-compose.yaml`
or the service Dockerfiles change.

## Direct Compose Images

| Compose service | Image reference | Local digest / image id observed | License notice |
| --- | --- | --- | --- |
| `hapi-update-client`, `hapi-notifiedpull-stu3`, `hapi-directory` | `hapiproject/hapi@sha256:7611e4d6601f35dd8c223ed2ed47a2807be06976f71b2e5990e6541bbc90c16f` | Running containers used image id `sha256:2c174d3db731441283df8c5c7f032c88a8859b9f19fd4bc2a659b0c8e692d1f1` | Local OCI label: `Apache-2.0`; source label: `https://github.com/hapifhir/hapi-fhir-jpaserver-starter`; version label: `v8.6.0-1`. Include upstream notices. |
| `hapi-update-client-health`, `hapi-notifiedpull-stu3-health`, `hapi-directory-health`, `notifiedpull-seed` | `curlimages/curl@sha256:d94d07ba9e7d6de898b6d96c1a072f6f8266c687af78a74f380087a0addf5d17` | Local image id `sha256:9dcf90ad7bb5f2233d364a8dd4974ed7495f8bda55867b26aa3c99babe939af5` | Local OCI label: `MIT`; source label: `https://github.com/curl/curl-container`; observed container label name `curl-linux-amd64:8.18.0`. Include upstream notices. |
| `redis` | `redis:7.2.4@sha256:5a93f6b2e391b78e8bd3f9e7e1e1e06aeb5295043b4703fb88392835cec924a0` | Registry metadata reports Redis `7.2.4`; amd64 manifest `sha256:9341b6548cc35b64a6de0085555264336e2f570e17ecff20190bf62222f2bd64`; source label: `https://github.com/docker-library/redis.git#f623bf8a6fef29b1459a29ff9f852c0f88d76b5a:7.2/debian` | Redis `7.2.4` is the last BSD-3-Clause Redis release line documented by the official Docker image license note. Include upstream notices. |
| `postgres` | `postgres:15@sha256:98fe06b500b5eb29e45bf8c073eb0ca399790ce17b1d586448edc4203627d342` | Running container used image id `sha256:7a1c59bcd56a11f9f93a5c2fa7d0b2fa86b936ce7a3f9f2d70576f8ba77efcd0`; observed `PG_VERSION=15.15-1.pgdg13+1` | PostgreSQL is released under the PostgreSQL License, a BSD/MIT-like permissive license. Include upstream notices. |

## Built Compose Images

| Compose service | Built image observed locally | Dockerfile base image(s) | License notice |
| --- | --- | --- | --- |
| `iti-91-mcsd-update-client` | `start-stack-iti-91-mcsd-update-client:latest`, image id `sha256:d2a13b089bfba039f9ddedf56935cc3f19373ddbd1f139dc6b99dca0fe21f121` | `python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf` | Application code is EUPL-1.2. Python dependencies are listed in `services/iti-91/THIRD_PARTY_LICENSES.md`. The Python base image also contains OS packages and Python runtime components under their own licenses. |
| `iti-130-publisher` | `start-stack-iti-130-publisher:latest`, image id `sha256:c267ae1e7521e36630c20e0b74d38fc226bb6c91053c23348be300abb73bb9b2` | `python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf` | Application code is MIT. Python dependencies are listed in `services/iti-130/THIRD_PARTY_LICENSES.md`. The Python base image also contains OS packages and Python runtime components under their own licenses. |
| `iti-90-address-book-proxy` | `start-stack-iti-90-address-book-proxy:latest`, image id `sha256:144fb6e1d29085ef58f4accc6afa7439265d0d2b446299111db093b6a047f9dd` | `python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf` | Application code is MIT. Python dependencies are listed in `services/iti-90/THIRD_PARTY_LICENSES.md`. The Python base image also contains OS packages and Python runtime components under their own licenses. |
| `sender-bgz-gateway` | `start-stack-sender-bgz-gateway:latest`, image id `sha256:3a4753504356d15665a38d46f00794c20ece698e819db241af244899fe8c05ff` | `python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf` | Application code inherits the root MIT license. Python dependencies are listed in `services/sender-bgz-gateway/THIRD_PARTY_LICENSES.md`. The Python base image also contains OS packages and Python runtime components under their own licenses. |
| `nuts-node` | `start-stack-nuts-node:latest`, image id `sha256:19f1ebfb32ea8ab8621f2d86f602c0e72c8a3162a956df69cf9af6ce2e870541` | `nutsfoundation/nuts-node:project-gf@sha256:362b321fe9b8b72c11872a0108bd41a3d05dd4b7b390090247740482a6b0a1e0` | Include the upstream Nuts node image notices and the licenses for added Alpine packages installed by this Dockerfile (`ca-certificates`, `su-exec`). |
| `caddy` | `start-stack-caddy:latest`, image id `sha256:b5c08f168a9e14a628510effe49b78cc8382795a599189ad46d82b56fbbbcc40` | builder: `caddy:2.11.2-builder@sha256:1ecefa333507828a592aaecc68d5f62a993787057429c04e9b0438a65c980a30`; final: `caddy:2.11.2@sha256:25cdc846626b62d05f6b633b9b40c2c9f6ef89b515dc76133cefd920f7dbe562`; module: `github.com/caddy-dns/cloudflare@v0.2.4` | Local OCI label: `Apache-2.0`; source label: `https://github.com/caddyserver/caddy-docker`; version label: `v2.11.2`. The image includes the Cloudflare DNS module built with `xcaddy`; include upstream notices for that module. |

## Shared Base Image

`python:3.11-slim` was observed locally as:

- digest: `python@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf`
- image id: `sha256:d1a45c0fb43d72e0c013ee97d460aa8f49f2c1bbd588b17a558cc6f7078b4276`
- observed Python version: `3.11.15`

Docker's official Python image documentation notes that the image contains
software under multiple licenses and that the image user is responsible for
license compliance for all contained software.

## Reference Sources

- Redis license overview: https://redis.io/legal/licenses/
- Redis official Docker image license note: https://hub.docker.com/_/redis/
- PostgreSQL license: https://www.postgresql.org/about/licence/
- Python official Docker image license note: https://hub.docker.com/_/python/
