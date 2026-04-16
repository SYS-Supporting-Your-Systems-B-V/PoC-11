# Fake UZI Cert Creation

Use `services/nuts-node/certs/create_fake_UZI_cert.md` as the canonical guide.

Short version for local Windows use from the repo root:

```powershell
$certs = (Resolve-Path 'secrets/nuts-node/tls').Path
docker run --rm `
  -v "${certs}:/certs" `
  nutsfoundation/go-didx509-toolkit:1.1.0 `
  vc `
  /certs/mach2.disyepd.com-chain.pem `
  /certs/mach2.disyepd.com.key `
  "CN=Fake UZI Root CA" `
  "did:web:mach2.disyepd.com:nuts-oauth2:iam:e21b9338-1f61-4a6b-9e28-866871d7c8a0"
```

If you use Git Bash instead of PowerShell:

```bash
CERTS_DIR="$(pwd -W)/secrets/nuts-node/tls"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${CERTS_DIR}:/certs" \
  nutsfoundation/go-didx509-toolkit:1.1.0 \
  vc \
  /certs/mach2.disyepd.com-chain.pem \
  /certs/mach2.disyepd.com.key \
  "CN=Fake UZI Root CA" \
  "did:web:mach2.disyepd.com:nuts-oauth2:iam:e21b9338-1f61-4a6b-9e28-866871d7c8a0"
```
