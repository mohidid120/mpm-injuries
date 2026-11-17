"""Latest official NBA injury report -> injuries.json for the site's live next-game projections (docs/SITE_HOSTING.md).

Usage: python injuries/scrape.py [out.json] [--pdf file.pdf | --pdf dir] [--force]
--pdf dir parses the newest report already in dir (pretip.sh: data/injury_reports/pdf, fetched by collect_injuries.R).
Runs in the cloud (.github/workflows/injuries.yml, every 15 min, 11:00-02:00 ET). Port of collect_injuries.R
(fetch_latest, parse_report, name_key): same URL slots, same column cuts, same wrapped-name repair, same name key.
P(play) by status is applied in the browser (site/js/rotation_allocate.js applyReport) with the exported status
rates, as rotation_next_report. The file is rewritten only when a newer report snapshot appears.
"""
import bisect, datetime as dt, json, os, re, sys, time, unicodedata, urllib.request
from zoneinfo import ZoneInfo

import pdfplumber

BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
ET = ZoneInfo("America/New_York")
COLS = ["game_date", "game_time", "matchup", "team", "player", "status", "reason"]
STATUSES = {"Out", "Questionable", "Doubtful", "Probable", "Available"}


def slots(now, hours=30):
    """Report names in 15-minute slots going back `hours` from now (ET), newest first, each named by its own date."""
    t = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    out = []
    for _ in range(hours * 4):
        h, m, ap = t.strftime("%I"), t.strftime("%M"), t.strftime("%p")
        out += [f"{t.date()}_{h}_{m}{ap}"] + ([f"{t.date()}_{h}{ap}"] if m == "00" else [])
        t -= dt.timedelta(minutes=15)
    return out


def fetch_latest(now=None, have=None, pace=0.5):
    """(snapshot name, pdf bytes) of the latest report published so far. Anti-block: only names newer than `have`
    (the report we already hold) are probed, paced `pace` s apart; nba.com answers unpublished names with 403s, and
    unpaced walks of 30 h of names every 15 min risk an Akamai IP block."""
    now = now or dt.datetime.now(ET)
    for i, name in enumerate(slots(now)):
        if have and pub_time(name) and pub_time(name) <= have:
            break
        if i:
            time.sleep(pace)
        try:
            with urllib.request.urlopen(BASE + name + ".pdf", timeout=20) as r:
                body = r.read()
            if len(body) > 1000:
                return name, body
        except Exception:
            pass
    return None, None


def parse_report(path):
    """Player rows of one report PDF, as collect_injuries.R parse_report."""
    cuts, lines, page = None, [], 0
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            words = [(w["x0"], round(w["top"]), w["text"]) for w in pg.extract_words(x_tolerance=1)]
            head = [w for w in words if w[2] in ("Game", "Matchup", "Team", "Player", "Current", "Reason")]
            if len(head) >= 6:  # column starts from the header row (repeated on each page)
                hy = next(w[1] for w in head if w[2] == "Matchup")
                head = [w for w in head if abs(w[1] - hy) < 3]
                gx = sorted(w[0] for w in head if w[2] == "Game")
                x = lambda k: next(w[0] for w in head if w[2] == k)
                cuts = [c - 3 for c in (gx[0], gx[1], x("Matchup"), x("Team"), x("Player"), x("Current"), x("Reason"))]
                words = [w for w in words if w[1] > hy + 5]
            if cuts is None:
                continue
            page += 1
            cells = {}
            for x0, y, t in sorted((w for w in words if w[1] > 60 and t_ok(w[2])), key=lambda w: (w[1], w[0])):
                col = bisect.bisect_right(cuts, x0)
                if col >= 1:
                    cells.setdefault(y, {}).setdefault(col, []).append(t)
            for y in sorted(cells):
                row = {k: " ".join(cells[y].get(i + 1, [])) for i, k in enumerate(COLS)}
                if all(re.fullmatch(r"[\d ]*", v) for v in row.values()):  # "Page N of M" footer: its numbers must not fill team
                    continue
                row.update(page=page, y=y)
                lines.append(row)
    is_row = lambda r: r["status"] in STATUSES or "NOT YET SUBMITTED" in f'{r["player"]} {r["status"]} {r["reason"]}'
    # a name too long for its cell wraps onto the lines just above and below the status line
    for r in lines:
        if r["player"] == "" and r["status"] in STATUSES:
            nb = [o for o in lines if o["page"] == r["page"] and abs(o["y"] - r["y"]) <= 12 and o is not r
                  and o["status"] not in STATUSES and o["player"] != ""]
            r["player"] = " ".join(o["player"] for o in sorted(nb, key=lambda o: o["y"]))
    # a long reason wraps the same way: its pieces sit on non-status lines just above and below
    for r in lines:
        if r["reason"] == "" and r["status"] in STATUSES:
            nb = [o for o in lines if o["page"] == r["page"] and abs(o["y"] - r["y"]) <= 12 and o is not r
                  and o["status"] not in STATUSES and o["reason"] != ""]
            r["reason"] = " ".join(o["reason"] for o in sorted(nb, key=lambda o: o["y"]))
    last = {}
    for r in lines:
        for k in ("game_date", "matchup", "team"):
            if r[k]:
                last[k] = r[k]
            else:
                r[k] = last.get(k)
    return [r for r in lines if is_row(r)]


def t_ok(t):
    return not re.fullmatch(r"Page|of", t)


def name_key(x):
    """collect_injuries.R name_key on "family first" (accents transliterated, suffixes and non-letters dropped)."""
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
    x = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", x)
    return re.sub(r"[^a-z]", "", x)


def pub_time(name):
    """Publication time (ET) of a snapshot name such as 2026-11-07_09_30PM or 2026-11-07_05PM; None if unparseable."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)$", name or "")
    return m and dt.datetime.strptime(f"{m[1]} {m[2]}:{m[3] or '00'} {m[4]}", "%Y-%m-%d %I:%M %p").replace(tzinfo=ET)


def report_json(name, rows, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    pub, m = pub_time(name), re.match(r"(\d{4}-\d{2}-\d{2})", name)
    return {
        "snapshot": name, "report_date": m[1],
        "published_utc": pub.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetched_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": [{"game_date": r["game_date"], "matchup": r["matchup"], "team": r["team"], "player": r["player"],
                  "key": name_key(re.sub(r"^(.*?), (.*)$", r"\1 \2", r["player"] or "")), "status": r["status"],
                  "reason": (r["reason"] or "").strip(),
                  "not_submitted": "NOT YET SUBMITTED" in f'{r["player"]} {r["status"]} {r["reason"]}'.upper()}
                 for r in rows],
    }


def main(argv):
    out = next((a for a in argv if a.endswith(".json")), "data/live/injuries.json")
    try:
        with open(out) as f:
            have = pub_time(json.load(f).get("snapshot"))
    except (OSError, ValueError):
        have = None
    if "--pdf" in argv:
        path = argv[argv.index("--pdf") + 1]
        if os.path.isdir(path):
            names = [f[:-4] for f in os.listdir(path) if f.endswith(".pdf") and pub_time(f[:-4])]
            if not names:
                print("no cached report"); return 0
            path = os.path.join(path, max(names, key=pub_time) + ".pdf")
        name = re.sub(r"\.pdf$", "", path.rsplit("/", 1)[-1])
    else:
        name, body = fetch_latest(have=None if "--force" in argv else have)
        if name is None:
            print("no newer report published" if have else "no report published yet"); return 0
    if have and "--force" not in argv and pub_time(name) and pub_time(name) <= have:  # never replace a newer report (the other writer got there first)
        print(f"unchanged: {name}"); return 0
    if "--pdf" not in argv:
        path = "/tmp/injury_report.pdf"
        with open(path, "wb") as f:
            f.write(body)
    doc = report_json(name, parse_report(path))
    with open(out, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"{name}: {len(doc['rows'])} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
