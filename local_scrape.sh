#!/bin/zsh
# Edge-case injury fetch for the local site (docs/SITE_HOSTING.md): launchd com.mpm.injuries, every 15 min.
# pretip.sh is the main source: it writes site/data/live/injuries.json from the report collect_injuries.R just fetched.
# This fetches the report itself only where pretip doesn't run: overnight (before 11:00 ET), and from 35 min before a
# tip on (late scratches after the 30-min pretip run; no games today = always). Never replaces a newer report.
# Also re-exports the live allocator inputs
# (site/export_rotation_inputs.R, seconds) when a newer minutes forecast exists than the one they were built from.
# Catch-up: when online, at most hourly, backfill every report the schedule says we should have (past game-day
# snapshots + pre-tip archive, collect_injuries.R; cached, ~5 s when nothing is missing). Missed slots while the Mac
# slept or was offline are fetched here. An nba.com block (Akamai 403) backs off 6 h so it is not deepened.
# Offline: skip everything (no hanging fetches). Runs under caffeinate (launchd/guard.sh) so a lid-closed dark wake finishes it.
ROOT="/Users/mohididrees/Documents/New project/basketball_impact"; cd "$ROOT" || exit 1
source launchd/guard.sh; stay_awake "$0" "$@"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir .injuries_scrape.lock 2>/dev/null || exit 0
trap 'rmdir .injuries_scrape.lock' EXIT
ts() { date '+%F %T'; }
MPM_MIN_BATTERY=0 run_preflight >/dev/null || { echo "$(ts) offline; skipped"; exit 0; }
catch_up() {
  local stamp=logs/.injuries_catchup block=logs/.injuries_blocked out S
  [[ -f $stamp && -n $(find $stamp -mmin -60) ]] && return 0
  [[ -f $block && -n $(find $block -mmin -360) ]] && return 0
  S=$(( $(date +%m) >= 9 ? $(date +%Y) + 1 : $(date +%Y) ))
  # Anti-block: stop on network failure, 1 s between requests, at most 60 requests per hour.
  export INJ_NO_WAIT=1 PREGAME_PACE=1 INJ_MAX_REQ=60
  out=$( { Rscript collect_injuries.R && Rscript collect_injuries.R pregame $S; } 2>&1 )
  if (( $? == 0 )); then touch $stamp; rm -f $block
    echo "$(ts) catch-up ok$(print -r -- "$out" | grep -E '^pregame [0-9]+:' | tail -1 | sed 's/^/; /')"
    # New pre-tip slots -> refresh the site's past-day injury archive (seconds, local files only).
    [[ $out == *"pregame "*"(+"* ]] && echo "$(ts) $(Rscript site/export_injury_archive.R $S 2>&1 | tail -1)"
  else
    [[ $out == *"Akamai 403"* ]] && touch $block
    echo "$(ts) catch-up stopped: $(print -r -- "$out" | grep -E 'Error|blocked|network' | tail -1)"
  fi
}
catch_up
edge() {
  (( 10#$(TZ=America/New_York date +%H%M) < 1100 )) && return 0
  local f=${TIPS:-logs/pretip/$(TZ=America/Chicago date +%F)_tips} now k m
  [[ -s $f ]] || return 0
  now=$(( 10#$(TZ=UTC date +%H) * 60 + 10#$(TZ=UTC date +%M) ))
  while read k m; do  # minutes until tip, wrapped to +-12 h (tips are HHMM UTC)
    m=$(( (10#${k[1,2]} * 60 + 10#${k[3,4]} - now + 2160) % 1440 - 720 ))
    (( m <= 35 )) && return 0
  done < $f
  return 1
}
edge && echo "$(ts) $(injuries/.venv/bin/python injuries/scrape.py site/data/live/injuries.json 2>&1 | tail -1)"
fc=$(ls results_minutes/forecast_20*.csv 2>/dev/null | sort | tail -1)
ri=site/data/live/rotation_inputs.json
if [[ -n $fc && ( ! -f $ri || $fc -nt $ri ) ]] && ! pgrep -f "run_predict.R|run_minutes_forecast.R" >/dev/null; then
  echo "$(ts) $(/usr/local/bin/Rscript site/export_rotation_inputs.R 2>&1 | tail -1)"
fi
