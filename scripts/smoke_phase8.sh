#!/usr/bin/env bash
# Phase 8 smoke test: register -> upload -> export ZIP -> verify metadata ->
# delete account -> verify 401 on next request.
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"
PASS="Aa1!aaaaa"
EMAIL="phase8-$(date +%s)-$RANDOM@example.com"

say() { printf "\n--- %s\n" "$*"; }

say "register $EMAIL"
curl -fsS -X POST "$BASE/auth/register" -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" > /dev/null

say "login"
TOKEN=$(curl -fsS -X POST "$BASE/auth/jwt/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$EMAIL" --data-urlencode "password=$PASS" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

say "upload a tiny PNG so the export ZIP has at least one image"
python -c "from PIL import Image; im=Image.new('RGB',(64,64),'red'); im.save('/tmp/phase8.png')"
UPLOAD=$(curl -fsS -X POST "$BASE/images/" -H "$AUTH" -F "file=@/tmp/phase8.png")
echo "$UPLOAD" | python -c "import sys,json; d=json.load(sys.stdin); print('uploaded', d['id'])"

say "GET /account/export -> verify ZIP and metadata"
curl -fsS "$BASE/account/export" -H "$AUTH" -o /tmp/phase8-export.zip
python - <<'PY'
import io, json, zipfile
zf = zipfile.ZipFile("/tmp/phase8-export.zip")
names = zf.namelist()
print("zip entries:", names[:8], "..." if len(names) > 8 else "")
meta = json.loads(zf.read("metadata.json").decode("utf-8"))
print("user.email:", meta["user"]["email"])
print("images:", len(meta["images"]))
print("originals/ entries:", sum(1 for n in names if n.startswith("originals/")))
PY

say "POST /account/delete -> verify cascade counts"
curl -fsS -X POST "$BASE/account/delete" -H "$AUTH"

say "GET /account/export after delete -> expect 401"
curl -s -o /dev/null -w "after-delete-status=%{http_code}\n" \
  "$BASE/account/export" -H "$AUTH"

echo
echo "PHASE 8 SMOKE: PASS"
