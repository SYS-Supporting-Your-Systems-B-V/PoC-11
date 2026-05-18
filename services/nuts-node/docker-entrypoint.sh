#!/bin/sh
set -eu

local_ca_src="/opt/local-caddy/caddy-local-root.crt"
local_ca_dst="/usr/local/share/ca-certificates/caddy-local-root.crt"
public_domain="${PUBLIC_DOMAIN:-your.domain.com}"

sed "s/__PUBLIC_DOMAIN__/${public_domain}/g" /opt/nuts/nuts.yaml.template > /opt/nuts/nuts.yaml

if [ -f "$local_ca_src" ]; then
  if [ ! -f "$local_ca_dst" ] || ! cmp -s "$local_ca_src" "$local_ca_dst"; then
    cp "$local_ca_src" "$local_ca_dst"
    update-ca-certificates >/dev/null 2>&1 || update-ca-certificates
  fi
fi

exec su-exec nuts-usr /usr/bin/nuts "$@"
