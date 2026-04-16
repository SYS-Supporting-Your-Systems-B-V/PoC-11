#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${ROOT_DIR}/secrets"
SHARED_CA_DIR="${SECRETS_DIR}/shared/nuts-development-network-ca/stable"
ITI90_MTLS_DIR="${SECRETS_DIR}/iti-90/mtls"

CA_PEM_URL="https://raw.githubusercontent.com/nuts-foundation/nuts-development-network-ca/master/stable/ca.pem"
CA_KEY_URL="https://raw.githubusercontent.com/nuts-foundation/nuts-development-network-ca/master/stable/ca.key"
CA_SCRIPT_URL="https://raw.githubusercontent.com/nuts-foundation/nuts-development-network-ca/master/stable/generate.sh"

CLIENT_KEY="${ITI90_MTLS_DIR}/test-uzi-client.key"
CLIENT_CERT="${ITI90_MTLS_DIR}/test-uzi-client.pem"
CLIENT_CHAIN="${ITI90_MTLS_DIR}/test-uzi-client-chain.pem"
CLIENT_CSR="${ITI90_MTLS_DIR}/test-uzi-client.csr"
CLIENT_SUBJECT="${TEST_UZI_SUBJECT:-/C=NL/O=PoC-11/OU=ITI-90/CN=Test UZI Client}"

mkdir -p "${SHARED_CA_DIR}" "${ITI90_MTLS_DIR}"

echo "Downloading Nuts stable test CA material into ${SHARED_CA_DIR}"
curl -fsSL "${CA_PEM_URL}" -o "${SHARED_CA_DIR}/ca.pem"
curl -fsSL "${CA_KEY_URL}" -o "${SHARED_CA_DIR}/ca.key"
curl -fsSL "${CA_SCRIPT_URL}" -o "${SHARED_CA_DIR}/generate.sh"

chmod 600 "${SHARED_CA_DIR}/ca.key"
chmod 644 "${SHARED_CA_DIR}/ca.pem" "${SHARED_CA_DIR}/generate.sh"

echo "Generating ITI-90 test UZI client key and certificate in ${ITI90_MTLS_DIR}"
openssl ecparam -genkey -name prime256v1 -noout -out "${CLIENT_KEY}"
openssl req -new -key "${CLIENT_KEY}" -out "${CLIENT_CSR}" -subj "${CLIENT_SUBJECT}"

EXTFILE="$(mktemp)"
cleanup() {
  rm -f "${EXTFILE}" "${CLIENT_CSR}"
}
trap cleanup EXIT

cat > "${EXTFILE}" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=critical,clientAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

openssl x509 -req \
  -in "${CLIENT_CSR}" \
  -CA "${SHARED_CA_DIR}/ca.pem" \
  -CAkey "${SHARED_CA_DIR}/ca.key" \
  -CAcreateserial \
  -out "${CLIENT_CERT}" \
  -days 365 \
  -sha256 \
  -extfile "${EXTFILE}"

cat "${CLIENT_CERT}" "${SHARED_CA_DIR}/ca.pem" > "${CLIENT_CHAIN}"

chmod 600 "${CLIENT_KEY}"
chmod 644 "${CLIENT_CERT}" "${CLIENT_CHAIN}"

openssl verify -CAfile "${SHARED_CA_DIR}/ca.pem" "${CLIENT_CERT}"

echo "Created:"
echo "  ${SHARED_CA_DIR}/ca.pem"
echo "  ${SHARED_CA_DIR}/ca.key"
echo "  ${ITI90_MTLS_DIR}/test-uzi-client.key"
echo "  ${ITI90_MTLS_DIR}/test-uzi-client.pem"
echo "  ${ITI90_MTLS_DIR}/test-uzi-client-chain.pem"
