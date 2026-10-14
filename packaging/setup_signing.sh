#!/bin/zsh
# One-time setup: create a local, self-signed code-signing certificate in your login keychain.
#
#   packaging/setup_signing.sh
#
# Why: an ad-hoc signed app is identified by macOS by a hash of its exact contents, so every
# rebuild looks like a different app and Screen Recording / Accessibility permissions stop
# working (System Settings still shows them as on). Signed with this certificate, every build
# has the same identity and permissions stay granted.
#
# The certificate only works on this Mac: it is not trusted by other Macs and is not an Apple
# Developer ID (see README for distributing to others). The private key is created in a temporary
# folder, imported into the login keychain, and the temporary copy is deleted. It is only usable
# by /usr/bin/codesign (macOS may ask once to allow that; choose "Always Allow").
#
# To remove it later: Keychain Access › login › My Certificates › delete
# "Minecraft Manager Local Signing" (builds then fall back to ad-hoc signing).
set -euo pipefail
NAME="Minecraft Manager Local Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-certificate -c "$NAME" "$KEYCHAIN" >/dev/null 2>&1; then
  echo "The signing certificate \"$NAME\" already exists. Nothing to do."
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

cat > "$TMP/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
subjectKeyIdentifier = hash
EOF

echo "Creating certificate \"$NAME\" (valid 10 years)…"
openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -config "$TMP/cert.cnf" -keyout "$TMP/key.pem" -out "$TMP/cert.pem" 2>/dev/null

# The .p12 password only protects the temporary file during import; it is never stored.
P12_PASS="$(openssl rand -hex 16)"
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" \
  -out "$TMP/identity.p12" -passout "pass:$P12_PASS"

echo "Importing into the login keychain (usable by codesign only)…"
security import "$TMP/identity.p12" -k "$KEYCHAIN" -P "$P12_PASS" -T /usr/bin/codesign >/dev/null

if security find-identity -p codesigning "$KEYCHAIN" | grep -q "$NAME"; then
  echo "Done. packaging/build_app.sh will now sign the app with \"$NAME\"."
else
  echo "The certificate was imported but codesign can't see it as a signing identity." >&2
  exit 1
fi
