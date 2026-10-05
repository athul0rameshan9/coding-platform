#!/usr/bin/env bash
# Runs the Judge0 load test. The auth token is extracted from ../../secret.txt
# (outside the repo) at
# run time and exported only into this process — it is never written into code.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRET="${JUDGE0_SECRET_FILE:-$HERE/../../secret.txt}"

if [[ -z "${JUDGE0_URL:-}" ]]; then
  echo "set JUDGE0_URL, e.g. JUDGE0_URL=http://<IP>:2358 ./run.sh" >&2
  exit 2
fi
export JUDGE0_URL

if [[ -z "${JUDGE0_TOKEN:-}" ]]; then
  if [[ ! -r "$SECRET" ]]; then
    echo "no JUDGE0_TOKEN in env and $SECRET is not readable" >&2
    exit 2
  fi
  # AUTHN_TOKEN=<hex> in the deployment transcript
  JUDGE0_TOKEN="$(grep -oE 'AUTHN_TOKEN=[0-9a-f]{16,}' "$SECRET" | head -1 | cut -d= -f2)"
  if [[ -z "$JUDGE0_TOKEN" ]]; then
    echo "could not extract AUTHN_TOKEN from $SECRET" >&2
    exit 2
  fi
  export JUDGE0_TOKEN
fi

# Fail fast if the deployment is unreachable or the token is wrong.
code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' \
  -H "X-Auth-Token: $JUDGE0_TOKEN" "$JUDGE0_URL/about")"
if [[ "$code" != "200" ]]; then
  echo "preflight GET /about returned HTTP $code (expected 200)" >&2
  exit 1
fi

exec python3 "$HERE/judge0_loadtest.py" "$@"
