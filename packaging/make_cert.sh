#!/bin/sh
# Creates, once, a self-signed code-signing certificate "ImNotARobot Local" in the login keychain.
# Signed with it, every build is the same program to macOS, so the "Allow in the Background" (login
# item) and notification permissions survive a rebuild instead of being asked for again.
#
# What had to be true for codesign to accept such a certificate, probed in a throwaway keychain:
# - it must carry the code-signing extended key usage (openssl -addext extendedKeyUsage=codeSigning);
# - /usr/bin/codesign must be allowed to use the private key (security import -T /usr/bin/codesign);
# - trust is NOT needed for signing: an untrusted identity is only listed by
#   `security find-identity -p codesigning` without -v, but codesign -s takes it all the same;
set -e

NAME="ImNotARobot Local"
LOGIN=$(security login-keychain -d user | tr -d '"' | sed 's/^ *//;s/ *$//')
if [ -z "$LOGIN" ] || [ ! -e "$LOGIN" ]; then
  echo "error: login keychain not found" >&2
  exit 1
fi

# untrusted identities are skipped by find-identity -v, so the check runs without it
if security find-identity -p codesigning "$LOGIN" | grep -q "\"$NAME\""; then
  echo "\"$NAME\" is already in $LOGIN, nothing to do."
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
PASS=$(openssl rand -hex 16)
openssl req -x509 -newkey rsa:2048 -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -days 3650 -nodes \
  -subj "/CN=$NAME/O=ImNotARobot" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null
openssl pkcs12 -export -out "$TMP/cert.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -name "$NAME" -passout "pass:$PASS" 2>/dev/null

if ! security import "$TMP/cert.p12" -k "$LOGIN" -P "$PASS" -T /usr/bin/codesign; then
  echo "error: the login keychain looks locked, I could not import." >&2
  echo "Run this one command (it asks for your Mac password), then run me again:" >&2
  echo "  security unlock-keychain" >&2
  exit 1
fi

# The certificate is deliberately NOT added to the trust settings: signing works without it, and a
# root trusted for code signing whose key sits in the login keychain would be a weaker Mac for no gain.

security find-identity -p codesigning "$LOGIN" | grep "\"$NAME\"" || true
echo "created \"$NAME\" in $LOGIN; packaging/build.sh now signs builds with it."
