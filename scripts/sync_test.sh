#!/usr/bin/env bash
# Upload a CSV through the API, map the columns, sync, and record time and memory. Run on the EC2 host.
#
# Usage:   scripts/sync_test.sh <csv-file> <admin-email> [label]
# Needs:   curl, python3, docker. Set API_DOMAIN (or BASE_URL) before running.
# Writes:  $OUT_DIR (default ~/sync-runs): <stamp>-<label>-report.json and <stamp>-<label>-docker-mem.log
#
# Column names: the HI files use "Account" (sender) and "Account.1" (receiver). The other fields match on their own.
# Change them for another file:  FROM_ACCOUNT_COL="..." TO_ACCOUNT_COL="..." scripts/sync_test.sh ...
# The password is read from the terminal; it is not stored or logged.

set -euo pipefail

CSV="${1:?usage: sync_test.sh <csv-file> <admin-email> [label]}"
EMAIL="${2:?usage: sync_test.sh <csv-file> <admin-email> [label]}"
LABEL="${3:-$(basename "$CSV" .csv)}"
BASE="${BASE_URL:-https://${API_DOMAIN:?set API_DOMAIN or BASE_URL}}"
FROM_COL="${FROM_ACCOUNT_COL:-Account}"
TO_COL="${TO_ACCOUNT_COL:-Account.1}"
OUT_DIR="${OUT_DIR:-$HOME/sync-runs}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"
REPORT="$OUT_DIR/$STAMP-$LABEL-report.json"
MEM_LOG="$OUT_DIR/$STAMP-$LABEL-docker-mem.log"

[ -f "$CSV" ] || { echo "no such file: $CSV" >&2; exit 1; }

read -rsp "password for $EMAIL: " PASS
echo

# 1. Log in and keep the token.
LOGIN_BODY="$(python3 -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$EMAIL" "$PASS")"
unset PASS
TOKEN="$(curl -sf -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' -d "$LOGIN_BODY" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
echo "logged in as $EMAIL"

# 2. Upload the file (streamed by curl, not read into memory here).
IMP_ID="$(curl -sf -H "$AUTH" -F "file=@$CSV;type=text/csv" "$BASE/api/imports" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
echo "import $IMP_ID uploaded ($(du -h "$CSV" | cut -f1))"

# 3. Mapping: automatic matches, with the two account columns set explicitly.
COLUMNS="$(curl -sf -H "$AUTH" "$BASE/api/imports/$IMP_ID/columns")"
MAPPING="$(echo "$COLUMNS" | FROM_COL="$FROM_COL" TO_COL="$TO_COL" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
m = dict(d["auto_mapping"])
m["from_account"] = os.environ["FROM_COL"]
m["to_account"] = os.environ["TO_COL"]
print(json.dumps({"mapping": m}))
')"
echo "mapping: $(echo "$MAPPING" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["mapping"]))')"

# 4. Start the sync. The API returns at once and runs the sync in the background.
curl -sf -H "$AUTH" -H 'Content-Type: application/json' -X POST -d "$MAPPING" \
  "$BASE/api/imports/$IMP_ID/sync" > /dev/null
echo "sync started $(date +%T)"

# 5. Sample docker memory every 5 seconds while the sync runs.
(
  while true; do
    date +%T
    docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.MemPerc}}' | grep aml-prod || true
    sleep 5
  done
) > "$MEM_LOG" 2>&1 &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

# 6. Poll until the sync finishes.
START="$(date +%s)"
while :; do
  IMP_JSON="$(curl -sf -H "$AUTH" "$BASE/api/imports/$IMP_ID")"
  STATUS="$(echo "$IMP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  STAGE="$(echo "$IMP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["report"].get("stage"))')"
  echo "$(( $(date +%s) - START ))s status=$STATUS stage=$STAGE"
  case "$STATUS" in
    validating|loading|triaging) sleep 10 ;;
    *) break ;;
  esac
done
echo "$IMP_JSON" > "$REPORT"

# 7. Summary.
echo "--- final: $STATUS"
echo "$IMP_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
r = d["report"]
print("elapsed_s:", r.get("elapsed_s"))
print("process_peak_rss_mb:", r.get("process_peak_rss_mb"))
print("rows:", r.get("rows"), " accounts:", r.get("accounts"), " cases_opened:", r.get("cases_opened"))
if r.get("error"):
    print("error:", r["error"])
'
echo "report: $REPORT"
echo "docker memory log: $MEM_LOG"
python3 - "$MEM_LOG" <<'PY'
import re, sys

# docker stats prints "<used> / <limit>" per container; keep the peak of <used> in MiB.
UNITS = {"B": 1 / 2**20, "kB": 1000 / 2**20, "KiB": 1 / 1024, "MB": 1000**2 / 2**20,
         "MiB": 1, "GB": 1000**3 / 2**20, "GiB": 1024}
peak: dict[str, float] = {}
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = re.match(r"(\S+) ([\d.]+)(\w+) / ", line)
    if m and m.group(3) in UNITS:
        name, used = m.group(1), float(m.group(2)) * UNITS[m.group(3)]
        peak[name] = max(peak.get(name, 0.0), used)
print("peak docker memory per container (MiB):")
for name in sorted(peak):
    print(f"  {name:<28} {peak[name]:8.0f}")
PY
