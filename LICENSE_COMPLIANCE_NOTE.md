# License Compliance Note

Date: 2026-05-12

## Requirement (PvA)
- Source code must be licensed under MIT.
- Documentation must be licensed under CC BY-SA 4.0.

## Current repo status
- Root code license: `LICENSE` is MIT.
- Documentation license text: `LICENSES/CC-BY-SA-4.0.txt` added.
- `services/sender-bgz-gateway`:
  - Code: MIT by root license inheritance; no service-specific exception noted.
  - Third-party dependency inventory added (`services/sender-bgz-gateway/THIRD_PARTY_LICENSES.md`).
- `services/iti-130`:
  - Code: MIT (`services/iti-130/LICENSE.md`).
  - Docs: CC BY-SA 4.0 (noted in `services/iti-130/README.md`).
- `services/iti-90`:
  - Code: MIT (`services/iti-90/LICENSE.md`).
  - Docs: CC BY-SA 4.0 (noted in `services/iti-90/README.md`).
- `services/iti-91`:
  - Code: EUPL-1.2 (`services/iti-91/LICENSE.md`, `services/iti-91/REUSE.toml`, `services/iti-91/pyproject.toml`).
  - Docs: CC BY-SA 4.0 (`services/iti-91/LICENSE.md`, `services/iti-91/REUSE.toml`).

## Mismatches / exceptions
- `services/iti-91` code is EUPL-1.2, not full MIT. Due to the existance of LGPL-3.0-only licenses from the psycopg library. Since we do not make source code changes to this library this service can still fall under MIT.
- Because of the `services/iti-91` exception, the full repository/stack should not be described as MIT-only.
- `services/iti-91` includes LGPL-3.0-only dependencies (`psycopg`, `psycopg-binary`, `psycopg-pool`). These may be usable in an MIT-licensed application, but distribution must preserve LGPL notices and avoid restricting the rights required by the LGPL.
- MPL-2.0 dependencies such as `certifi` and `pathspec` are generally manageable as third-party dependencies. If MPL-covered files are modified, those modified files remain subject to MPL terms.
- Third-party dependencies are under various licenses (e.g., Apache-2.0, BSD, PSF-2.0, MPL-2.0, LGPL-3.0-only). These do not usually change the license of this repository's own code, but their license notices and obligations must be preserved.
- Runtime container images used by `start-stack/docker-compose.yaml` are tracked in `start-stack/THIRD_PARTY_CONTAINER_IMAGES.md`. The Compose runtime image references and Dockerfile base images are pinned by digest where available. Redis is pinned to `7.2.4`, the last BSD-3-Clause Redis release line documented by the official Docker image license note.

## Third-party license inventories
- `services/sender-bgz-gateway/THIRD_PARTY_LICENSES.md`
- `services/iti-130/THIRD_PARTY_LICENSES.md`
- `services/iti-90/THIRD_PARTY_LICENSES.md`
- `services/iti-91/THIRD_PARTY_LICENSES.md`
