#!/usr/bin/env bash
# Phase 4 smoke test against a running backend.
# Exercises: register -> login -> consent NONE -> grant -> status GRANTED ->
# policy hash -> withdraw -> status WITHDRAWN. Cleans up the test user via
# /users/me delete at the end.
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8765}"
EMAIL="smoke-$(date +%s)@example.com"
PASS="Aa1!aaaaa"

say() { printf "\n--- %s\n" "$*"; }

say "register $EMAIL"
curl -fsS -X POST "$BASE/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" > /dev/null

say "login"
TOKEN=$(curl -fsS -X POST "$BASE/auth/jwt/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$EMAIL" --data-urlencode "password=$PASS" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

say "consent status (expect NONE)"
curl -fsS "$BASE/consent/face-recognition" -H "$AUTH"

say "policy fetch (sha256_hex returned)"
curl -fsS "$BASE/consent/face-recognition/policy" -H "$AUTH" \
  | python -c "import sys,json;d=json.load(sys.stdin);print('version=',d['version']);print('sha256=',d['sha256_hex'][:16],'...');print('len=',len(d['text']))"

say "grant consent"
curl -fsS -X POST "$BASE/consent/face-recognition/grant" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"signature_text":"Smoke Test","consent_collection":true,"consent_retention":true}'

say "consent status (expect GRANTED)"
curl -fsS "$BASE/consent/face-recognition" -H "$AUTH"

say "list people (expect empty arrays)"
curl -fsS "$BASE/people/" -H "$AUTH"

say "backfill (expect queued=0 since no images)"
curl -fsS -X POST "$BASE/people/backfill" -H "$AUTH"

say "withdraw"
curl -fsS -X POST "$BASE/consent/face-recognition/withdraw" -H "$AUTH"

say "consent status (expect WITHDRAWN)"
curl -fsS "$BASE/consent/face-recognition" -H "$AUTH"

say "DELETE /users/me (cleanup)"
curl -fsS -X DELETE "$BASE/users/me" -H "$AUTH" -o /dev/null -w "status=%{http_code}\n"

echo
echo "PHASE 4 SMOKE: PASS"
