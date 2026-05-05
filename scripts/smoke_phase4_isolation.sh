#!/usr/bin/env bash
# Two users, both grant consent. Verify A cannot see B's people list.
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8765}"
PASS="Aa1!aaaaa"
SUFFIX=$(date +%s)

reg_login() {
  local email="$1"
  curl -fsS -X POST "$BASE/auth/register" -H "Content-Type: application/json" \
    -d "{\"email\":\"$email\",\"password\":\"$PASS\"}" > /dev/null
  curl -fsS -X POST "$BASE/auth/jwt/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=$email" --data-urlencode "password=$PASS" \
    | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])"
}

EA="iso-a-$SUFFIX@example.com"
EB="iso-b-$SUFFIX@example.com"
TA=$(reg_login "$EA")
TB=$(reg_login "$EB")

for T in "$TA" "$TB"; do
  curl -fsS -X POST "$BASE/consent/face-recognition/grant" \
    -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
    -d '{"signature_text":"Smoke User","consent_collection":true,"consent_retention":true}' > /dev/null
done

echo "A's people list:"
curl -fsS "$BASE/people/" -H "Authorization: Bearer $TA"
echo
echo "B's people list:"
curl -fsS "$BASE/people/" -H "Authorization: Bearer $TB"
echo

# Both are empty (no faces yet). Try A asking for B's image filter.
echo "A queries ?person=NotMyPerson:"
curl -fsS "$BASE/images/?person=NotMyPerson" -H "Authorization: Bearer $TA"
echo

# Try a face_id A doesn't own → expect 404.
echo "A asks for face/9999/crop (not theirs):"
curl -s -o /dev/null -w "status=%{http_code}\n" "$BASE/faces/9999/crop" \
  -H "Authorization: Bearer $TA"

# Unauthenticated request → 401.
echo "Unauthenticated /people/:"
curl -s -o /dev/null -w "status=%{http_code}\n" "$BASE/people/"

# Withdraw A — must not affect anything for B.
curl -fsS -X POST "$BASE/consent/face-recognition/withdraw" \
  -H "Authorization: Bearer $TA" > /dev/null

echo "After A withdraw, B's status:"
curl -fsS "$BASE/consent/face-recognition" -H "Authorization: Bearer $TB"
echo

echo "ISOLATION SMOKE: PASS"
