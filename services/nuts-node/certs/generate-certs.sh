set -e

apk add --no-cache bash openssl git
git clone --depth 1 https://github.com/nuts-foundation/go-didx509-toolkit.git /tmp/toolkit
cd /tmp/toolkit/test_ca
bash ./issue-cert.sh mach2.disyepd.com "ZBC Demo Kliniek" "Locality" 00000000000 00700700 00000000
cp -v /tmp/toolkit/test_ca/out/* /work/

chmod 644 /work/*.pem
chmod 644 /work/*.crt 2>/dev/null || true
chmod 644 /work/*.key
ls -l /work/