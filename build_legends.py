"""AthScore 'Legends' layer: notable footballers whose careers began before the free per-player stats start (about 2005), from Wikidata.
Wikidata holds each club spell of a player with matches played (P1350), goals (P1351), start and end dates, and awards received (P166).
That gives career-per-club totals, not seasons, so each spell is spread evenly over its years. The rating is therefore rougher than the
stats-based leagues and is shown with lower confidence. Players are limited to those with at least MIN_SITELINKS Wikipedia language editions.
Queries run one birth year at a time and are cached in state/legends/ (finished years are never refetched).
Run:  python3 build_legends.py   -> legends_players.csv
Environment: LEGENDS_DIR=<folder> reads {year}.json and {year}_awards.json instead of the network (used for tests).
"""
import unicodedata, csv, json, os, re, sys, time, collections, statistics, urllib.request, urllib.parse, urllib.error

ENDPOINT = "https://query.wikidata.org/sparql"
UA = {"User-Agent": "AthScoreIndex/1.0 (https://github.com/joeygattellari7/athscore-index; research project) python-urllib",
      "Accept": "application/sparql-results+json"}
FIRST_BIRTH, LAST_BIRTH = 1915, 1985
MIN_SITELINKS = 30
LAST_SEASON = 2005            # seasons after this come from the stats-based leagues
CACHE = "state/legends"
BUDGET = float(os.environ.get("LEGENDS_BUDGET_MIN", "40")) * 60
T0 = time.time()
FAILED = []

SPELLS = """SELECT ?p ?pLabel ?birth ?posLabel ?team ?teamLabel ?start ?end ?m ?g ?leagueLabel ?countryLabel WHERE {
  ?p wdt:P106 wd:Q937857 ; wdt:P569 ?birth ; wikibase:sitelinks ?sl .
  FILTER(?sl >= %(lo)d && ?sl < %(hi)d)
  FILTER(YEAR(?birth) = %(y)d)
  ?p p:P54 ?st . ?st ps:P54 ?team ; pq:P1350 ?m ; pq:P580 ?start .
  OPTIONAL { ?st pq:P582 ?end } OPTIONAL { ?st pq:P1351 ?g }
  ?team wdt:P118 ?league .
  OPTIONAL { ?team wdt:P17 ?country }
  OPTIONAL { ?p wdt:P413 ?pos }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""
AWARDS = """SELECT ?p ?awardLabel ?when WHERE {
  ?p wdt:P106 wd:Q937857 ; wdt:P569 ?birth ; wikibase:sitelinks ?sl .
  FILTER(?sl >= %(lo)d && YEAR(?birth) = %(y)d)
  ?p p:P166 ?a . ?a ps:P166 ?award ; pq:P585 ?when .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


def sparql(q, tries=4):
    last = None
    for i in range(tries):
        try:
            url = ENDPOINT + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=100) as r:
                return json.loads(r.read().decode("utf-8", "replace"))["results"]["bindings"]
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "20")))
            else:
                time.sleep(5 * (i + 1))
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(str(last)[:120])


def v(b, k):
    return b.get(k, {}).get("value")


def fetch_year(y):
    """Spells for players born in year y. A slow query is split by popularity band."""
    rows = []
    for lo, hi in ((MIN_SITELINKS, 60), (60, 10 ** 6)):
        rows += sparql(SPELLS % {"lo": lo, "hi": hi, "y": y})
        time.sleep(1.5)
    return rows


def load_year(y):
    os.makedirs(CACHE, exist_ok=True)
    local = os.environ.get("LEGENDS_DIR")
    if local:
        sp = f"{local}/{y}.json"; aw = f"{local}/{y}_awards.json"
        return (json.load(open(sp)) if os.path.exists(sp) else []), (json.load(open(aw)) if os.path.exists(aw) else [])
    sp, aw = f"{CACHE}/{y}.json", f"{CACHE}/{y}_awards.json"
    if os.path.exists(sp) and os.path.exists(aw):
        return json.load(open(sp)), json.load(open(aw))
    if time.time() - T0 > BUDGET:
        FAILED.append(f"{y}: skipped for now (time budget)")
        return (json.load(open(sp)) if os.path.exists(sp) else []), (json.load(open(aw)) if os.path.exists(aw) else [])
    try:
        spells = fetch_year(y)
        awards = sparql(AWARDS % {"lo": MIN_SITELINKS, "y": y}); time.sleep(1.5)
    except Exception as e:
        FAILED.append(f"{y}: {e}")
        return [], []
    spells = [{k: v(b, k) for k in ("p", "pLabel", "birth", "posLabel", "team", "teamLabel", "start", "end", "m", "g", "leagueLabel", "countryLabel")} for b in spells]
    awards = [{k: v(b, k) for k in ("p", "awardLabel", "when")} for b in awards]
    json.dump(spells, open(sp, "w"), separators=(",", ":")); json.dump(awards, open(aw, "w"), separators=(",", ":"))
    return spells, awards


def pos_of(label):
    t = str(label or "").lower()
    if "goalkeeper" in t: return "GK"
    if "defend" in t or "back" in t or "sweeper" in t: return "DEF"
    if "midfield" in t: return "MID"
    if "forward" in t or "striker" in t or "winger" in t: return "FWD"
    return "MID"


SECOND_DIV = re.compile(r"(serie b|segunda|championship|second division|division 2|ligue 2|2\. bundesliga|zweite|segunda division|primera b|serie c|third|league one|league two|division two|\b2\.|\bb\b)", re.I)
HONOURS = [(re.compile(r"ballon d.or", re.I), "Ballon d'Or", 6.0), (re.compile(r"golden (shoe|boot)|top scorer|capocannoniere|pichichi", re.I), "Golden Boot", 3.0),
           (re.compile(r"footballer of the year|player of the year|player of the season|best player", re.I), "Player of the Season", 3.0)]


LEAGUE_RX = [("Premier League", re.compile(r"^(english )?(premier league|football league first division|first division|football league)$", re.I), re.compile(r"england|united kingdom", re.I)),
             ("La Liga", re.compile(r"^(la liga|primera divisi.n|liga espa.ola)$", re.I), None),
             ("Serie A", re.compile(r"^serie a$", re.I), None),
             ("Bundesliga", re.compile(r"^bundesliga$", re.I), None),
             ("Ligue 1", re.compile(r"^(ligue 1|division 1|championnat de france.*)$", re.I), re.compile(r"france", re.I))]


def top_league(label, country):
    """Map a Wikidata league label to one of the five leagues the page covers, or None."""
    for nm, rx, cx in LEAGUE_RX:
        if rx.match(str(label or "").strip()) and (cx is None or cx.search(str(country or ""))):
            return nm
    return None


LEAGUE_TITLES = {  # season end year -> {league: champion, letters only}
    1993: {"Serie A": "milan", "La Liga": "barcelona", "Bundesliga": "werderbremen", "Ligue 1": "marseille", "Premier League": "manchesterunited"},
    1994: {"Serie A": "milan", "La Liga": "barcelona", "Bundesliga": "bayernmunich", "Ligue 1": "parissaintgermain"},
    1995: {"Serie A": "juventus", "La Liga": "realmadrid", "Bundesliga": "dortmund", "Ligue 1": "nantes"},
    1996: {"Serie A": "milan", "La Liga": "atleticomadrid", "Bundesliga": "dortmund", "Ligue 1": "auxerre"},
    1997: {"Serie A": "juventus", "La Liga": "realmadrid", "Bundesliga": "bayernmunich", "Ligue 1": "monaco"},
    1998: {"Serie A": "juventus", "La Liga": "barcelona", "Bundesliga": "kaiserslautern", "Ligue 1": "lens"},
    1999: {"Serie A": "milan", "La Liga": "barcelona", "Bundesliga": "bayernmunich", "Ligue 1": "bordeaux"},
    2000: {"Serie A": "lazio", "La Liga": "deportivo", "Bundesliga": "bayernmunich", "Ligue 1": "monaco"},
    2001: {"Serie A": "roma", "La Liga": "realmadrid", "Bundesliga": "bayernmunich", "Ligue 1": "nantes"},
    2002: {"Serie A": "juventus", "La Liga": "valencia", "Bundesliga": "dortmund", "Ligue 1": "lyon"},
    2003: {"Serie A": "juventus", "La Liga": "realmadrid", "Bundesliga": "bayernmunich", "Ligue 1": "lyon"},
    2004: {"Serie A": "milan", "La Liga": "valencia", "Bundesliga": "werderbremen", "Ligue 1": "lyon"},
    2005: {"Serie A": "juventus", "La Liga": "barcelona", "Bundesliga": "bayernmunich", "Ligue 1": "lyon"}}
CL_WINNERS = {1993: "marseille", 1994: "milan", 1995: "ajax", 1996: "juventus", 1997: "dortmund", 1998: "realmadrid", 1999: "manchesterunited",
              2000: "realmadrid", 2001: "bayernmunich", 2002: "realmadrid", 2003: "milan", 2004: "porto", 2005: "liverpool"}


def year_of(s):
    m = re.match(r"-?(\d{4})", str(s or ""))
    return int(m.group(1)) if m else None


def build():
    spells, awards = [], collections.defaultdict(list)
    for y in range(FIRST_BIRTH, LAST_BIRTH + 1):
        sp, aw = load_year(y)
        spells += sp
        for a in aw:
            awards[a["p"]].append(a)
    # one row per (player, team, start)
    seen = {}
    for s in spells:
        k = (s["p"], s["team"], s["start"])
        if k not in seen:
            seen[k] = s
    by_player = collections.defaultdict(list)
    for s in seen.values():
        by_player[s["p"]].append(s)
    rows = []
    for pid, sl in by_player.items():
        name = sl[0]["pLabel"] or pid.rsplit("/", 1)[-1]
        if re.fullmatch(r"Q\d+", name or ""):
            continue
        pos = pos_of(sl[0]["posLabel"]); by = year_of(sl[0]["birth"])
        sl.sort(key=lambda s: s["start"])
        for i, s in enumerate(sl):
            ys = year_of(s["start"]); ye = year_of(s["end"]) if s["end"] else None
            if ye is None:
                ye = year_of(sl[i + 1]["start"]) if i + 1 < len(sl) else ys + 1
            if ye < ys: ye = ys + 1
            n = max(1, ye - ys)
            m = float(s["m"] or 0); g = float(s["g"]) if s["g"] not in (None, "") else None
            if m <= 0 or (g is None and pos in ("MID", "FWD")):
                continue
            league = top_league(s["leagueLabel"], s["countryLabel"])
            if not league:
                continue            # only careers in the five leagues the page covers
            for k in range(n):
                se = ys + 1 + k
                if se > LAST_SEASON:
                    break
                rows.append(dict(pid=pid, name=name, pos=pos, by=by, se=se, club=s["teamLabel"] or "", league=league, country=s["countryLabel"] or "",
                                 apps=m / n, goals=(g or 0.0) / n, tier=1, honours=[]))
    # merge duplicate (player, season) rows when two spells overlap
    merged = {}
    for r in rows:
        k = (r["pid"], r["se"])
        if k in merged:
            o = merged[k]; o["apps"] += r["apps"]; o["goals"] += r["goals"]
            if r["apps"] > 0: o["club"] = o["club"] if o["apps"] - r["apps"] >= r["apps"] else r["club"]
        else:
            merged[k] = r
    rows = list(merged.values())
    # honours from awards (season = year of the award)
    idx = collections.defaultdict(dict)
    for r in rows: idx[r["pid"]][r["se"]] = r
    for pid, al in awards.items():
        for a in al:
            yr = year_of(a["when"]); label = a["awardLabel"] or ""
            for rx, nm, pts in HONOURS:
                if rx.search(label) and yr and idx.get(pid):
                    se = yr if yr in idx[pid] else min(idx[pid], key=lambda s: abs(s - yr))
                    if abs(se - yr) <= 1 and nm not in idx[pid][se]["honours"]:
                        idx[pid][se]["honours"].append(nm)
                    break
    # team honours 1993-2005 (season = end year). Wikidata awards hold individual prizes only, so league titles and Champions League wins are entered here.
    for r in rows:
        ck = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", r["club"]).encode("ascii", "ignore").decode().lower())
        for nm, key in (("League champions", LEAGUE_TITLES.get(r["se"], {}).get(r["league"])), ("Champions League", CL_WINNERS.get(r["se"]))):
            ok = key and (ck == key or (len(key) > 5 and key in ck) or (len(key) <= 5 and re.fullmatch(r"(as|ac|ss|rc)?" + key + r"(fc)?", ck)))
            if ok and r["apps"] >= 10 and nm not in r["honours"]:
                r["honours"].append(nm)
    # rating: output from goals per game (outfield) or appearances (keepers and defenders), z-scored within position and decade
    for r in rows:
        r["gpg"] = r["goals"] / r["apps"] if r["apps"] else 0
        r["dec"] = (r["se"] // 10) * 10
    grp = collections.defaultdict(list)
    for r in rows: grp[(r["pos"], r["dec"])].append(r)
    for (pos, dec), g in grp.items():
        pool = [r for r in g if r["apps"] >= 10] or g
        key = (lambda r: r["gpg"]) if pos in ("MID", "FWD") else (lambda r: r["apps"])
        mu = statistics.mean(key(r) for r in pool); sd = statistics.pstdev([key(r) for r in pool]) or 1
        wgt = 1.0 if pos == "FWD" else 0.8 if pos == "MID" else 0.7
        for r in g:
            z = max(-2.5, min(3.5, (key(r) - mu) / sd))
            r["out"] = round(max(0.0, min(100.0, 50 + 16 * wgt * z)), 1)
    pts = {"Ballon d'Or": 6.0, "Golden Boot": 3.0, "Player of the Season": 3.0, "League champions": 6.0, "Champions League": 6.0}
    for r in rows:
        r["imp"] = r["out"]                                          # no team goals to compare against: reuse output
        r["cred"] = 50.0                                             # no team table for these clubs: neutral
        r["avail"] = round(min(100.0, 100 * r["apps"] / 30.0), 1)
        r["honpts"] = min(15.0, sum(pts.get(h, 0) for h in r["honours"]))
        r["hon"] = round(100 * r["honpts"] / 15.0, 1)
        r["perf"] = round(0.35 * r["out"] + 0.20 * r["imp"] + 0.20 * r["cred"] + 0.15 * r["hon"] + 0.10 * r["avail"], 1)
    names = collections.defaultdict(set)
    for r in rows: names[r["name"]].add(r["pid"])
    for r in rows:
        if len(names[r["name"]]) > 1:
            r["name"] = f"{r['name']} ({r['by'] or r['pid'].rsplit('/', 1)[-1]})"
    return rows


def main():
    hdr = "athlete,sport,position,competition,tier,season,games,perf,stat,birth_year,note,apps,minutes,goals,assists,xg,xa,cs,saves,yc,rc,bonus,titles,awards,honours,team_ppg,team_z,current,out,imp,cred,avail,hon,honpts,prev_perf,prev_games".split(",")
    rows = build()
    rows.sort(key=lambda r: (r["name"], r["se"]))
    with open("legends_players.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(hdr)
        for r in rows:
            w.writerow([r["name"], "Football", r["pos"], r["league"], r["tier"], r["se"], round(r["apps"], 1), r["perf"], "", r["by"] or "", r["club"],
                        round(r["apps"]), "", round(r["goals"]), "", "", "", "", "", "", "", "", int("League champions" in r["honours"]), len([h for h in r["honours"] if h != "League champions"]), "; ".join(r["honours"]), "", "", 0,
                        r["out"], r["imp"], r["cred"], r["avail"], r["hon"], r["honpts"], "", ""])
    print(len(rows), "player-seasons,", len({r["pid"] for r in rows}), "players")
    json.dump({"rows": len(rows), "players": len({r["pid"] for r in rows}), "failed": FAILED}, open("legends_status.json", "w"))
    if FAILED:
        print("NOTES:", *FAILED[:20], sep="\n  ")


if __name__ == "__main__":
    main()
