# ITI-130 Publisher

`iti130_publisher.py` reads SQL source tables and publishes derived mCSD/FHIR
directory resources as an ITI-130 transaction bundle.

The script uses stable logical ids derived from source keys instead of requiring
extra FHIR id columns in the database. In practice that means the same clinic,
location, department, endpoint, practitioner, or practitioner assignment keeps
the same logical FHIR id across repeated runs.

## What It Publishes

Always:

- `Organization`
- `Location`
- `HealthcareService`
- `Endpoint`

Optional:

- `Practitioner`
- `PractitionerRole`
- `Provenance`

The publisher models departments in two ways:

- as child `Organization` resources under the clinic organization
- as `HealthcareService` resources derived 1:1 from department rows

That distinction matters when reading the generated bundle: the department
organization exists for directory structure, while the `HealthcareService`
represents the service offering itself.

## Source Tables

The publisher expects the ZBC/EPD-style tables used in this repo:

- `tblKliniek`
- `tblKliniekLocatie`
- `tblLocatie`
- `tblAfdeling`
- `tblEndpoint`
- optional practitioner tables: `tblMedewerker`, `tblMedewerkerinzet`,
  `tblRoldefinitie`

Endpoint rows can be clinic-level, location-level, or department-level. If a
source endpoint row has no explicit `payloadType*` values, the publisher can
fall back to configured defaults via `--default-endpoint-payload`.

The shipped SQLite demo seed does not rely on that fallback. It creates four
explicit endpoint rows for BGZ, notification, OAuth, and ITI-91 directory
capabilities.

## Role In This Repository

In the local stack this service runs as the one-shot Compose job
`iti-130-publisher` defined in
[`../../start-stack/docker-compose.yaml`](../../start-stack/docker-compose.yaml).

The image defaults are:

- `SQL_CONN=sqlite:///demo.db`
- `FHIR_BASE=http://hapi-directory:8080/fhir`
- entrypoint: `python iti130_publisher.py`
- default command:
  `--profile-set nl --sqlite-reset-seed --include-practitioners --fhir-reset-seed`

`Exited (0)` after `docker compose up -d` is the expected success state for
this container. The publisher is a seed/sync job, not a long-running API
service.

Re-run the seed job from the repo root:

```bash
docker compose -f start-stack/docker-compose.yaml run --rm iti-130-publisher
```

## Requirements

The container image for this service uses Python 3.11. For local runs, install
the repo requirements from this service directory:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The repo requirements include the pieces needed for:

- SQLite demo runs
- HTTP publishing with `requests`
- SQLAlchemy-based database access
- MSSQL access through `sqlalchemy-pytds` / `python-tds`

## Quick Start

Dry-run against the built-in SQLite demo:

```bash
python iti130_publisher.py \
  --sql-conn sqlite:///demo.db \
  --sqlite-reset-seed \
  --fhir-base http://localhost:8080/fhir \
  --dry-run \
  --out bundle.json
```

Publish to a server with a bearer token:

```bash
python iti130_publisher.py \
  --sql-conn "mssql+pytds://user:pass@host:1433/DBNAME" \
  --fhir-base https://fhir.example.org/fhir \
  --token YOUR_BEARER_TOKEN
```

Publish with OAuth2 client credentials instead of a static bearer token:

```bash
python iti130_publisher.py \
  --sql-conn "mssql+pytds://user:pass@host:1433/DBNAME" \
  --fhir-base https://fhir.example.org/fhir \
  --oauth-token-url https://auth.example.org/oauth/token \
  --oauth-client-id client-id \
  --oauth-client-secret client-secret \
  --oauth-scope "scope-a scope-b"
```

Use `--fhir-reset-seed` only against disposable environments. It wipes existing
directory resources before publishing.

## SQLite Demo Seed

When `SQL_CONN` points at SQLite, the script can initialize and seed a local
demo database. The seed is inserted only when the tables are empty, or when you
force a reset with `--sqlite-reset-seed`.

`--sqlite-reset-seed` is destructive for the selected SQLite database. It
deletes the existing demo rows and recreates the built-in seed.

The shipped seed inserts data for:

- 1 clinic row
- 1 location row
- 1 clinic-location link
- 12 department rows
- 4 endpoint rows
- 1 practitioner row
- 1 practitioner-role definition row
- 1 practitioner assignment row

When published with the stack defaults (`--include-practitioners` enabled), that
produces:

| Resource type | Count | Notes |
| --- | --- | --- |
| `Organization` | 13 | 1 clinic + 12 department organizations |
| `Location` | 1 | `tblLocatie` row `10` |
| `HealthcareService` | 12 | 1 per department |
| `Endpoint` | 4 | endpoint keys `900`, `901`, `902`, `903` |
| `Practitioner` | 1 | only published with `--include-practitioners` |
| `PractitionerRole` | 1 | only published with `--include-practitioners` |

That is why the stack verification expects 13 `Organization` resources in the
directory FHIR store.

## Seeded Records

### Clinic

`tblKliniek` seeds one clinic that becomes
`Organization/org-kliniek-1`.

| Field | Value |
| --- | --- |
| `kliniekkey` | `1` |
| Name | `ZBC Demo Kliniek` |
| URA | `00700700` |
| AGB | `00000000` |
| KvK | `12345678` |
| Active | `1` |
| Phone | `+31-20-0000000` |
| Email | `info@demo.invalid` |
| Website | `https://demo.invalid` |
| Address | `Demo Straat 1, 1011AA Amsterdam, NL` |
| Type | `prov / Healthcare Provider` |

### Location

`tblLocatie` seeds one location that becomes `Location/loc-10`. The linking
table `tblKliniekLocatie` connects clinic `1` to location `10`.

| Field | Value |
| --- | --- |
| `locatiekey` | `10` |
| Name | `Demo Locatie` |
| AGB | `00000000` |
| Active | `1` |
| Phone | `+31-20-1111111` |
| Email | `locatie@demo.invalid` |
| Address | `Locatie Straat 10, 1011AA Amsterdam, NL` |
| Coordinates | `52.3702, 4.8952` |
| Type | `HOSP / Hospital` |

### Departments

Each seeded department becomes:

- `Organization/org-afdeling-{afdelingkey}`
- `HealthcareService/svc-afdeling-{afdelingkey}`

| `afdelingkey` | Name | `kliniekkey` | `locatiekey` | Specialty | Service type |
| --- | --- | --- | --- | --- | --- |
| `100` | `Beweegpoli` | `1` | `10` | `1251536003 / Sport medicine` | `491 / Exercise Physiology` |
| `101` | `Dermatologie (huidziekten)` | `1` | `10` | `394582007 / Dermatology` | `168 / Dermatology` |
| `102` | `Interne geneeskunde` | `1` | `10` | `419192003 / Internal medicine` | `382 / Medical Services` |
| `103` | `Leefstijl coaching` | `1` | `10` | `722164000 / Dietetics and nutrition` | `553 / 1-on-1 Support /Mentoring /Coaching` |
| `104` | `Orthopedie` | `1` | `10` | `394801008 / Trauma & orthopaedics` | `218 / Orthopaedic Surgery` |
| `105` | `Penispoli` | `1` | `10` | `394612005 / Urology` | `222 / Urology` |
| `106` | `Plastische chirurgie` | `1` | `10` | `394611003 / Plastic surgery` | `220 / Plastic & Reconstructive Surgery` |
| `107` | `Proctologie (anus problemen)` | `1` | `10` | `408464004 / Colorectal surgery` | `221 / Surgery - General` |
| `108` | `Reumatologie (ontstekingen gewrichten)` | `1` | `10` | `394810000 / Rheumatology` | `182 / Rheumatology` |
| `109` | `Spatader- & wondzorg (vaatchirurgie en dermatologie)` | `1` | `10` | `408463005 / Vascular surgery` | `223 / Vascular Surgery` |
| `110` | `Vasectomie/sterilisatie` | `1` | `10` | `394612005 / Urology` | `54 / Family Planning` |
| `111` | `Vulvapoli (derma en gynaecologie)` | `1` | `10` | `394586005 / Gynaecology` | `567 / Women's Health Clinic` |

### Endpoints

The shipped SQLite seed creates four clinic-level endpoints, all attached to
clinic `1` and therefore published with logical ids `Endpoint/ep-900` through
`Endpoint/ep-903`.

| Endpoint key | Name | Address | Connection type | Payload type | Notes |
| --- | --- | --- | --- | --- | --- |
| `900` | `FHIR API` | `https://mach2.disyepd.com/notifiedpull/fhir` | `hl7-fhir-rest / HL7 FHIR REST` | `BGZ Server` | BGZ server endpoint |
| `901` | `Nuts OAuth2` | `https://mach2.disyepd.com/nuts-oauth2/oauth2/00700700` | `direct-project / Direct Project` | `Nuts-OAuth` | Authentication server endpoint |
| `903` | `Administration Directory` | `https://mach2.disyepd.com/fhir` | `hl7-fhir-rest / HL7 FHIR REST` | `Care Services Directory for Update Client` | ITI-91 discovery/update capability |

Unlike the older version of this README, the current seed does not leave the
payload type empty for the main FHIR endpoint. All four seeded endpoints define
their payload types explicitly.

### Practitioner Data

The SQLite seed also inserts practitioner rows. They are only published as FHIR
resources when `--include-practitioners` is enabled.

`tblRoldefinitie`:

- key `1`
- code `doctor`
- display `Doctor`

`tblMedewerker` becomes `Practitioner/prac-1000`:

| Field | Value |
| --- | --- |
| `medewerkerkey` | `1000` |
| Display name | `Dr. John Smith` |
| BIG | `12345678901` |
| AGB zorgverlener | `99999999` |
| Default location | `10` |
| Email | `john.smith@demo.invalid` |
| Mobile | `+31-6-12345678` |
| Gender | `male` |
| Birth date | `1980-05-12` |

`tblMedewerkerinzet` becomes `PractitionerRole/pracrole-5000`:

| Field | Value |
| --- | --- |
| `medewerkerinzetkey` | `5000` |
| Practitioner | `1000` |
| Department | `100` (`Beweegpoli`) |
| Role definition | `1` (`doctor`) |
| Start date | `2020-01-01` |
| Active | `1` |

## Resulting FHIR Relationships

The current publisher logic creates the following relationship shape:

- `Organization/org-kliniek-1` is the top-level clinic organization.
- `Location/loc-10` uses `managingOrganization -> Organization/org-kliniek-1`.
- `Organization/org-afdeling-{id}` uses `partOf -> Organization/org-kliniek-1`.
- `HealthcareService/svc-afdeling-{id}` uses
  `providedBy -> Organization/org-kliniek-1`.
- `HealthcareService/svc-afdeling-{id}` links to `Location/loc-10`.
- `PractitionerRole/pracrole-5000` links the practitioner, department
  organization, location, and healthcare service.

One detail that is easy to miss: the healthcare service is not published as
"provided by the department organization". It is published as provided by the
clinic organization, while the department still exists separately as a child
organization in the directory.

Representative references from the shipped seed:

```text
Organization/org-kliniek-1
  endpoint -> Endpoint/ep-900, Endpoint/ep-901, Endpoint/ep-902, Endpoint/ep-903

Location/loc-10
  managingOrganization -> Organization/org-kliniek-1

Organization/org-afdeling-100
  partOf -> Organization/org-kliniek-1

HealthcareService/svc-afdeling-100
  providedBy -> Organization/org-kliniek-1
  location -> Location/loc-10

PractitionerRole/pracrole-5000
  practitioner -> Practitioner/prac-1000
  organization -> Organization/org-afdeling-100
  location -> Location/loc-10
  healthcareService -> HealthcareService/svc-afdeling-100
  endpoint -> clinic/location/department endpoint union
```

In the shipped SQLite seed, all four endpoints are clinic-level endpoints, so
the practitioner role ultimately inherits those clinic endpoints.

## Main CLI Options

Required inputs:

- `--sql-conn` or `SQL_CONN`
- `--fhir-base` or `FHIR_BASE`

Auth and transport:

- `--token`
- `--oauth-token-url`
- `--oauth-client-id`
- `--oauth-client-secret`
- `--oauth-scope`
- `--mtls-cert`
- `--mtls-key`
- `--no-verify-tls`

Publish shaping:

- `--bundle-size`
- `--since`
- `--profile-set`
- `--include-meta-profile`
- `--include-meta-lastupdated`
- `--include-meta-source`
- `--include-provenance`
- `--assigned-id-system-base`
- `--default-ura`
- `--publisher-ura`
- `--publisher-source`
- `--default-endpoint-payload`
- `--bgz-policy`
- `--include-practitioners`

Safety and test helpers:

- `--sqlite-reset-seed`
- `--fhir-reset-seed`
- `--delete-inactive`
- `--allow-delete-endpoint`
- `--dry-run`
- `--out`
- `--lenient`
- `--production`

HTTP behavior:

- `--timeout`
- `--connect-timeout`
- `--http-retries`
- `--http-backoff`
- `--http-pool-connections`
- `--http-pool-maxsize`
- `--publish-delay`
- `--max-retry-after`

Observability:

- `--log-level`
- `--log-format`

Use `python iti130_publisher.py --help` for the full flag descriptions.

## Configuration Guidance

The previous version of this README had more operational explanation here. That
guidance is still useful, so this section keeps the practical "how do I choose
these settings?" context instead of only listing flag names.

### 1. Publish Target: `--fhir-base`

Set `--fhir-base` to the base URL of the FHIR server that acts as the
Administration Directory and accepts transaction bundles.

- Use `https://...` for anything outside disposable local testing.
- In `--production` mode, risky settings such as plain `http://` or
  `--no-verify-tls` are treated as errors.
- If you are publishing to the local stack, the default directory target is
  `http://hapi-directory:8080/fhir` in Docker or `http://localhost:8080/fhir`
  from the host.

### 2. Source Data: `--sql-conn`

Set `--sql-conn` to the database that exposes the source tables:
`tblKliniek`, `tblKliniekLocatie`, `tblLocatie`, `tblAfdeling`, and
`tblEndpoint`, plus optional practitioner tables when you use
`--include-practitioners`.

- For local testing, `sqlite:///demo.db` is the built-in demo path.
- For MSSQL, the documented path in this repo is typically
  `mssql+pytds://user:pass@host:1433/DBNAME`.
- `tblKliniekLocatie` is important: it is the bridge that lets the publisher
  link clinic, location, and downstream service resources correctly.
- If your source system uses different physical table names, the intended
  approach is to expose compatible views rather than rewriting the publisher.

### 3. Authentication And TLS

Choose one outbound authentication model for the target FHIR server:

- bearer token with `--token`
- OAuth2 client credentials with `--oauth-token-url`,
  `--oauth-client-id`, `--oauth-client-secret`, and optionally
  `--oauth-scope`
- mutual TLS with `--mtls-cert` and optionally `--mtls-key`

Operationally:

- `--token` takes precedence over OAuth settings.
- `--no-verify-tls` is only appropriate for disposable test setups.
- If `--mtls-key` is omitted, `--mtls-cert` must contain both cert and private
  key.

### 4. NL GF Identifiers And URA

For NL Generic Functions addressing, the URA is the leading organization
identifier and is reused across the generated directory resources.

The publisher determines the URA in this order:

1. `--publisher-ura`
2. source `URANummer` from the clinic row
3. `--default-ura`

That matters because the URA is used:

- as an `Organization.identifier`
- on `Location` and `HealthcareService` identifiers
- as the assigner identity for the generated NL GF AssignedId identifiers

Recommended settings:

- keep `--profile-set nl` when you are publishing for the NL GF use case
- set `--assigned-id-system-base` to a stable namespace you control instead of
  leaving the placeholder default `https://sys.local/identifiers`
- use `--publisher-ura` in PoC/test setups when you need to switch publisher
  identity without changing source rows

Normalization behavior in the current code:

- a `URA:` prefix is stripped
- a numeric URA shorter than 8 digits is left-padded with zeroes

### 5. Endpoints, Payload Types, And BGZ Policy

Endpoint rows drive discoverability and routing. In practice, the important
fields are:

- `adres` -> `Endpoint.address`
- `status`, `actief`, `ingangsdatum`, `einddatum` -> effective endpoint status
- `payloadType*` -> what the endpoint is for

If a source endpoint row has no explicit `payloadType*` values, the current
publisher falls back to two default payload codings:

- BGZ Server capabilities
- Care Services Directory for Update Client capabilities

That fallback can be overridden with repeatable `--default-endpoint-payload`
values using the format `system|code|display`.

For BGZ-oriented runs, `--bgz-policy` controls how strict the sanity check is:

- `per-clinic` is the current default
- `per-afdeling` is stricter for department-level endpoint ownership
- `per-afdeling-or-clinic` allows department services to inherit BGZ capability
  from their clinic endpoint
- `off` disables the BGZ-specific policy entirely

If you want to populate BGZ payloads explicitly in MSSQL source data, the row
shape is still:

```sql
UPDATE dbo.tblEndpoint
SET payloadTypeSystemUri = 'http://nuts-foundation.github.io/nl-generic-functions-ig/CodeSystem/nl-gf-data-exchange-capabilities',
    payloadTypeCode = 'http://nictiz.nl/fhir/CapabilityStatement/bgz2017-servercapabilities',
    payloadTypeDisplay = 'BGZ Server'
WHERE endpointkey = 900;
```

### 6. Dry Runs, Chunking, And Destructive Flags

Useful operational flags:

- `--dry-run` builds the bundle without posting it
- `--out bundle.json` writes the dry-run bundle to disk
- `--bundle-size` controls how many transaction entries are sent per chunk
- `--sqlite-reset-seed` recreates the SQLite demo data
- `--fhir-reset-seed` deletes existing directory resources before publishing

Important behavior:

- `--sqlite-reset-seed` is only for SQLite demo runs
- `--fhir-reset-seed` is destructive and intended for disposable environments
- `--fhir-reset-seed` is not allowed with `--production`

### 7. Delta Publishing: `--since`

`--since` is a publisher-side selection filter, not an ITI-130 protocol field.
It tells the script to publish a best-effort delta since a UTC timestamp.

Example:

```bash
python iti130_publisher.py \
  --sql-conn "mssql+pytds://user:pass@host:1433/DBNAME" \
  --fhir-base https://fhir.example.org/fhir \
  --since 2025-12-30T12:00:00Z
```

Behavior to be aware of:

- the selection is driven mainly by `LaatstGewijzigdOp >= since`
- for endpoints, and for practitioner assignments when enabled, the script also
  looks at start and end dates so status flips are not missed as easily
- in delta mode, a published resource can reference another resource that is not
  resent in the same run because that referenced resource was already published
  earlier
- because of that, sanity checking is more tolerant in delta mode than in full
  runs

## Environment Defaults

CLI arguments override environment variables and `.env` values. The current
`Settings` model reads these defaults:

Core connection:

- `SQL_CONN`
- `FHIR_BASE`

Reset helpers:

- `SQLITE_RESET_SEED`
- `FHIR_RESET_SEED`

Authentication:

- `FHIR_TOKEN`
- `OAUTH_TOKEN_URL`
- `OAUTH_CLIENT_ID`
- `OAUTH_CLIENT_SECRET`
- `OAUTH_SCOPE`

Publish shaping:

- `BUNDLE_SIZE`
- `SINCE_UTC`
- `PROFILE_SET`
- `ASSIGNED_ID_SYSTEM_BASE`
- `DEFAULT_URA`
- `PUBLISHER_URA`
- `INCLUDE_META_LASTUPDATED`
- `INCLUDE_META_SOURCE`
- `INCLUDE_PROVENANCE`
- `PUBLISHER_SOURCE`
- `BGZ_POLICY`

mTLS:

- `MTLS_CERT`
- `MTLS_KEY`

HTTP behavior:

- `HTTP_TIMEOUT`
- `HTTP_CONNECT_TIMEOUT`
- `HTTP_RETRIES`
- `HTTP_BACKOFF`
- `HTTP_POOL_CONNECTIONS`
- `HTTP_POOL_MAXSIZE`
- `PUBLISH_DELAY_SECONDS`
- `MAX_RETRY_AFTER_SECONDS`

Logging:

- `ITI130_LOG_LEVEL`
- `ITI130_LOG_FORMAT`

Some flags are intentionally CLI-only in the current implementation. If you
need them, set them explicitly in the command or container entrypoint. The main
examples are `--include-meta-profile`, `--include-practitioners`,
`--delete-inactive`, `--allow-delete-endpoint`, `--lenient`,
`--no-verify-tls`, and `--production`.

## Tests

```bash
pytest -vv tests
```

The test suite covers transaction bundle semantics, NL GF mapping, seeded
endpoint behavior, and repo-specific PoC routing expectations.

## Related Docs

- Full stack: [`../../start-stack/README.md`](../../start-stack/README.md)
- Repository overview: [`../../README.md`](../../README.md)
