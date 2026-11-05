#!/usr/bin/env bash
# Print every robot command apps/back audits, as it happens.
#
#   ./scripts/audit_watch.sh            # database from apps/back/.env
#   DATABASE_URL=... ./scripts/audit_watch.sh
#   ./scripts/audit_watch.sh --print    # print the query and exit
#
# For a session run by one person: with the headset on, the Mac's screen shows
# what the robot was actually sent, by whom, how it ended, and — for a stop —
# whether it damped. Rows are stamped at SEND time (dc11842), so they appear in
# the order the commands went out, once each command has finished.
#
# Read-only: one SELECT a second, nothing else.

set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

# Rows since the watch started, oldest first, each prefixed with its id so the
# loop can print it once. `result` is stored as a JSON string, so it is
# unwrapped (#>> '{}') before its fields are read.
query="select id, to_char(created_at, 'HH24:MI:SS.MS'),
       rpad(skill_name, 20), rpad(coalesce(source, '-'), 7), rpad(status, 5),
       coalesce(duration_ms::text, '-') || ' ms',
       left(coalesce(error, ''), 50),
       case when skill_name = 'stop_everything' then
         'acked=' || coalesce((result #>> '{}')::jsonb ->> 'stop_burst_acked', '?')
         || ' damp=' || coalesce((result #>> '{}')::jsonb ->> 'real_damp_fallback_reason', 'no')
       else left(coalesce(params::text, ''), 60) end
from tool_call_log where created_at > :'since' order by created_at"

if [ "${1:-}" = "--print" ]; then
    printf '%s\n' "$query"
    exit 0
fi

env_file="$repo/apps/back/.env"
url="${DATABASE_URL:-$(sed -n 's/^DATABASE_URL=//p' "$env_file" 2>/dev/null | tail -1 | tr -d '"')}"
if [ -z "$url" ]; then
    echo "✗ no DATABASE_URL in $env_file" >&2
    exit 1
fi

echo "watching tool_call_log in ${url##*/} — Ctrl-C to stop"
since="$(psql "$url" -Atc "select now()")" || {
    echo "✗ cannot reach the database ($url)" >&2
    exit 1
}
seen="$(mktemp)"
trap 'rm -f "$seen"; echo; exit 0' INT TERM
# Rows are stamped at send time but written when they FINISH, so a slow command
# can land after a faster, later one: a "newer than the last row printed"
# cursor would skip it. Re-read everything since the start and print each id
# once instead — a session is hundreds of rows, not millions.
while true; do
    rows="$(psql "$url" -At -F '  ' -v since="$since" <<<"$query" 2>/dev/null)"
    if [ -n "$rows" ]; then
        while IFS= read -r row; do
            id="${row%%  *}"
            grep -qxF -- "$id" "$seen" && continue
            printf '%s\n' "$id" >>"$seen"
            printf '%s\n' "${row#*  }"
        done <<<"$rows"
    fi
    sleep 1
done
