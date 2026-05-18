# Secrets Layout

Runtime secrets and local test certificates live under the repo-root `secrets/`
directory. The goal is to keep service code clean and keep generated material
out of `services/...`.

## Layout

```text
secrets/
  cloudflare_api_token
  shared/
    nuts-development-network-ca/
      stable/
        ca.pem
        ca.key
        generate.sh
  iti-90/
    mtls/
      test-uzi-client.key
      test-uzi-client.pem
      test-uzi-client-chain.pem
  nuts-node/
    tls/
      your.domain.com.pem
      your.domain.com.key
      your.domain.com-chain.pem
```

## Why This Layout

- `secrets/<service>/...` keeps ownership obvious.
- `secrets/shared/...` avoids duplicating shared trust anchors such as the Nuts
  stable test CA.
- Docker services can mount either the whole `secrets/` tree or only the
  service-specific subfolder they need.

## ITI-90 Test UZI mTLS

Generate the shared Nuts stable CA material plus a local ITI-90 client
certificate with:

```bash
./scripts/setup-test-uzi-mtls.sh
```

That script downloads the public test CA material from
`nuts-foundation/nuts-development-network-ca` and issues a client certificate
signed by that CA for local PoC use.

## Docker Mounts

- `iti-90-address-book-proxy` mounts `../secrets:/secrets:ro`
- `nuts-node` mounts `../secrets/nuts-node/tls:/opt/nuts/certs:ro`
- `iti-91-mcsd-update-client` already mounts `../secrets:/src/secrets`

## Startup-Critical Notes

- `nuts-node` expects its TLS files under `secrets/nuts-node/tls/` and the
  filenames must match `PUBLIC_DOMAIN`. With the default
  `PUBLIC_DOMAIN=your.domain.com`, that means:
  `your.domain.com.pem`, `your.domain.com.key`, and
  `your.domain.com-chain.pem`.
- If those Nuts TLS files are missing, `nuts-node` cannot start.
- When using the real `start-stack/caddy/Caddyfile` instead of
  `Caddyfile.local`, provide `secrets/cloudflare_api_token` before startup.

## Notes

- `secrets/` is gitignored; generated keys and certificates stay local.
- Service README files remain the source of truth for service-specific env vars.
- If another service later needs certificates, prefer a new
  `secrets/<service>/...` subfolder instead of a new `certs/` directory inside
  the service.
