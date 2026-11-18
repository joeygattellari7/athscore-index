"""AthScore data for La Liga, Serie A, Bundesliga and Ligue 1. No API keys.
Source: ESPN's open soccer endpoints (standings, team rosters, and per-player season statistics: appearances, starts, minutes, goals,
assists, shots, key passes, tackles, interceptions, clearances, blocks, saves, clean sheets, cards). There is no xG in this source.
Finished seasons are cached under state/espn/ and never fetched again. The current season is refreshed on every run.
Backfilling many seasons takes a while, so each run stops starting new seasons after TOP5_BUDGET_MIN minutes (default 35) and
the next run carries on from the cache.
Run:  python3 build_top5.py   -> top5_players.csv
Environment: ESPN_DIR=<folder> reads {code}_{year}.json files instead of the network (used for tests).
"""
import csv, json, os, re, sys, time, collections, unicodedata, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import build_pl_index as B   # reuse the rating engine: score(), components(), team_context(), TEAMTAB and the honour points

LEAGUES = {"La Liga": "esp.1", "Serie A": "ita.1", "Bundesliga": "ger.1", "Ligue 1": "fra.1"}
FIRST_YEAR = 2005            # 2005-06
CURRENT_YEAR = 2026          # season starting 2026 = 2026-27 (our season number is the end year, 2027)
CACHE = "state/espn"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
CORE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/{lg}/seasons/{y}/types/1/athletes/{a}/statistics/0"
ROSTER = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/teams/{t}/roster?season={y}"
STAND = "https://site.api.espn.com/apis/v2/sports/soccer/{lg}/standings?season={y}"
BUDGET = float(os.environ.get("TOP5_BUDGET_MIN", "35")) * 60
T0 = time.time()
FAILED = []

W_E = {"GK": {"cs": .35, "gcg": .35, "sv": .30},
       "DEF": {"defact": .30, "pass": .15, "ga": .15, "cs": .20, "xgc": .20},
       "MID": {"ga": .30, "kp": .25, "defact": .15, "pass": .15, "sot": .15},
       "FWD": {"g": .35, "a": .20, "sot": .15, "shots": .10, "kp": .20}}
# honours that are cheap to hand-enter and unambiguous (season = end year); club names are compared after stripping accents and spaces.
CL = {2006: "barcelona", 2007: "acmilan", 2009: "barcelona", 2010: "intermilan", 2011: "barcelona", 2013: "bayernmunich", 2014: "realmadrid",
      2015: "barcelona", 2016: "realmadrid", 2017: "realmadrid", 2018: "realmadrid", 2020: "bayernmunich", 2022: "realmadrid",
      2024: "realmadrid", 2025: "parissaintgermain", 2026: "parissaintgermain"}
EUROPA = {2006: "sevilla", 2007: "sevilla", 2010: "atleticomadrid", 2012: "atleticomadrid", 2014: "sevilla", 2015: "sevilla", 2016: "sevilla",
          2018: "atleticomadrid", 2020: "sevilla", 2021: "villarreal", 2022: "eintrachtfrankfurt", 2023: "sevilla", 2024: "atalanta"}
ALIAS = {"internazionale": "intermilan", "inter": "intermilan", "milan": "acmilan", "bayern": "bayernmunich", "psg": "parissaintgermain",
         "parissg": "parissaintgermain", "atletico": "atleticomadrid"}


def ck(name):
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z]", "", n)
    return ALIAS.get(n, n)


def jget(url, tries=3):
    last = None
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def pos_of(p):
    ab = str((p or {}).get("abbreviation", "")).upper()
    nm = str((p or {}).get("name", "")).lower()
    if ab.startswith("G") or "goal" in nm:
        return "GK"
    if ab.startswith("D") or "defen" in nm or "back" in nm:
        return "DEF"
    if ab.startswith("M") or "mid" in nm:
        return "MID"
    return "FWD"


def flat(stats):
    d = {}
    for c in (stats or {}).get("splits", {}).get("categories", []):
        for x in c.get("stats", []):
            d[x["name"]] = x.get("value", 0)
    return d


def fetch_season(lg, year):
    st = jget(STAND.format(lg=lg, y=year))
    entries = []
    for ch in (st or {}).get("children", []):
        entries += (ch.get("standings") or {}).get("entries", [])
    if not entries:
        return None
    teams, tid = {}, {}
    for e in entries:
        s = {x["name"]: x.get("value", 0) for x in e.get("stats", [])}
        title = e["team"].get("displayName") or e["team"].get("name")
        teams[title] = {"pts": s.get("points", 0), "games": int(s.get("gamesPlayed", 0)), "gf": s.get("pointsFor", 0), "ga": s.get("pointsAgainst", 0), "cs": 0}
        tid[title] = e["team"]["id"]
    roster = {}
    for title, t in tid.items():
        r = jget(ROSTER.format(lg=lg, t=t, y=year))
        for a in (r or {}).get("athletes", []):
            if isinstance(a, dict) and "items" in a:          # some responses group athletes by position
                for b in a["items"]:
                    roster[b["id"]] = (b, title)
            elif isinstance(a, dict) and "id" in a:
                roster[a["id"]] = (a, title)

    def one(aid):
        try:
            return aid, flat(jget(CORE.format(lg=lg, y=year, a=aid)))
        except Exception as e:
            return aid, {"_err": str(e)[:80]}
    with ThreadPoolExecutor(max_workers=12) as ex:
        res = dict(ex.map(one, list(roster)))
    errs = sum(1 for v in res.values() if "_err" in v)
    if roster and errs > 0.3 * len(roster):
        raise RuntimeError(f"{lg} {year}: {errs} of {len(roster)} player lookups failed")
    players = []
    for aid, (a, title) in roster.items():
        s = res.get(aid) or {}
        if not s or s.get("minutes", 0) <= 0:
            continue
        dob = str(a.get("dateOfBirth", ""))[:4]
        players.append({"id": aid, "name": a.get("fullName") or a.get("displayName"), "pos": pos_of(a.get("position")), "by": dob if dob.isdigit() else "",
                        "team": title, "apps": s.get("appearances", 0), "starts": s.get("starts", 0), "min": s.get("minutes", 0),
                        "g": s.get("totalGoals", 0), "a": s.get("goalAssists", 0), "sot": s.get("shotsOnTarget", 0), "shots": s.get("totalShots", 0),
                        "kp": s.get("shotAssists", 0), "tk": s.get("effectiveTackles", s.get("totalTackles", 0)), "int": s.get("interceptions", 0),
                        "clr": s.get("effectiveClearance", s.get("totalClearance", 0)), "blk": s.get("blockedShots", 0),
                        "pass": s.get("accuratePasses", 0), "cs": s.get("cleanSheet", 0), "gc": s.get("goalsConceded", 0), "sv": s.get("saves", 0),
                        "yc": s.get("yellowCards", 0), "rc": s.get("redCards", 0)})
    for p in players:       # team clean sheets, estimated from its keepers
        if p["pos"] == "GK":
            teams[p["team"]]["cs"] = max(teams[p["team"]]["cs"], p["cs"])
    return {"players": players, "teams": teams}


def load_season(code, lg, year):
    os.makedirs(CACHE, exist_ok=True)
    path = f"{CACHE}/{code}_{year}.json"
    local = os.environ.get("ESPN_DIR")
    if local:
        p = f"{local}/{code}_{year}.json"
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    cached = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    if cached and year < CURRENT_YEAR:
        return cached          # finished seasons with data are never fetched again; empty ones are retried
    if time.time() - T0 > BUDGET and year < CURRENT_YEAR:
        FAILED.append(f"{lg} {year}: skipped for now (time budget), next run continues")
        return None
    try:
        d = fetch_season(lg, year)
    except Exception as e:
        FAILED.append(f"{lg} {year}: {e}")
        return cached or None
    if d is None or len(d["players"]) < 150:
        FAILED.append(f"{lg} {year}: " + ("ESPN returned no standings" if d is None else f"only {len(d['players'])} players with minutes") + ", will retry next run")
        return cached or None
    json.dump(d, open(path, "w", encoding="utf-8"), separators=(",", ":"))
    return d


def fl(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def build_league(name, code):
    lg = code
    B.TEAMTAB.clear()
    rows, teams_by_se = [], {}
    years = [CURRENT_YEAR] + list(range(CURRENT_YEAR - 1, FIRST_YEAR - 1, -1))      # current season first, then newest to oldest
    for year in years:
        se = year + 1
        d = load_season(code, lg, year)
        if not d or not d.get("players"):
            continue
        teams = d["teams"]
        teams_by_se[se] = teams
        src = "e_" + code
        for t, v in teams.items():
            B.TEAMTAB[(src, se, t)] = (v["pts"], v["games"])
        sr = []
        for p in d["players"]:
            mins = fl(p["min"])
            if mins <= 0:
                continue
            tv = teams.get(p["team"]) or {}
            tg = tv.get("games") or 38
            m = max(mins, 1) / 90
            tcs = tv.get("cs", 0)
            cs_proxy = p["cs"] if p["pos"] == "GK" else tcs * min(1.0, mins / (tg * 90))
            defact = (fl(p["tk"]) + fl(p["int"]) + fl(p["clr"]) + fl(p["blk"])) / m
            met = dict(g=fl(p["g"]) / m, a=fl(p["a"]) / m, ga=(fl(p["g"]) + fl(p["a"])) / m, sot=fl(p["sot"]) / m, shots=fl(p["shots"]) / m, kp=fl(p["kp"]) / m,
                       defact=defact, cs=(tcs / tg) if tv else 0.0, xgc=-((tv.get("ga", 0) / tg) if tv else 0.0),
                       gcg=-(fl(p["gc"]) / m), sv=fl(p["sv"]) / m)
            met["pass"] = fl(p["pass"]) / m
            sr.append(dict(tkey=(src, se, p["team"]), src="es", pid=f"e{p['id']}", name=p["name"], pos=p["pos"], se=se, club=p["team"], by=p["by"], min=mins,
                           apps=int(fl(p["apps"])), goals=int(fl(p["g"])), assists=int(fl(p["a"])), cs=cs_proxy, yc=int(fl(p["yc"])), rc=int(fl(p["rc"])),
                           xg="", xa="", saves=int(fl(p["sv"])) if p["pos"] == "GK" else "", csreal=int(fl(p["cs"])) if p["pos"] == "GK" else "",
                           bonus="", m=met, honours=[], titles=0))
        if sr:
            mx = max(r["min"] for r in sr)
            B.score(sr, W_E, min(450, 0.5 * mx))
            rows += sr
    if not rows:
        return [], 0
    idsby = collections.defaultdict(set)
    for r in rows:
        idsby[r["name"]].add(r["pid"])
    for r in rows:
        if len(idsby[r["name"]]) > 1:
            r["name"] = f"{r['name']} ({r['by'] or r['pid'][1:]})"
    byse = collections.defaultdict(list)
    for r in rows:
        byse[r["se"]].append(r)
    for se, rs in byse.items():
        teams = teams_by_se.get(se) or {}
        if teams:
            full = 2 * (len(teams) - 1)
            if all(v["games"] >= full for v in teams.values()):                      # season finished
                champ = max(teams, key=lambda t: teams[t]["pts"])
                for r in rs:
                    if r["club"] == champ and r["apps"] >= B.TITLE_MIN_APPS:
                        r["titles"] = 1; r["honours"].append("League champions")
                top = max(r["goals"] for r in rs)
                for r in rs:
                    if r["goals"] == top and top > 0:
                        r["honours"].append("Golden Boot")
        for lab, table in (("Champions League", CL), ("European trophy", EUROPA)):
            k = table.get(se)
            for r in rs:
                if k and ck(r["club"]) == k and r["apps"] >= B.SQUAD_MIN_APPS:
                    r["honours"].append(lab)
    for r in rows:
        r["honpts"] = min(B.HONOUR_CAP, sum(B.HONOUR_PTS.get(h, 0) for h in r["honours"]))
        r["hon"] = round(100 * r["honpts"] / B.HONOUR_CAP, 1)
        r["awards"] = sum(1 for h in r["honours"] if h != "League champions")
    B.team_context(rows)
    B.components(rows)
    cur_games = max((v["games"] for v in (teams_by_se.get(CURRENT_YEAR + 1) or {}).values()), default=0)
    return rows, cur_games


def prev_snapshot(code, rnd):
    best = 0
    if os.path.isdir("state"):
        for fn in os.listdir("state"):
            m = re.fullmatch(rf"top5_{re.escape(code)}_r(\d+)\.csv", fn)
            if m and int(m.group(1)) < rnd:
                best = max(best, int(m.group(1)))
    if not best:
        return {}
    return {r["athlete"]: (float(r["perf"]), float(r["games"])) for r in csv.DictReader(open(f"state/top5_{code}_r{best}.csv", encoding="utf-8"))}


def main():
    hdr = "athlete,sport,position,competition,tier,season,games,perf,stat,birth_year,note,apps,minutes,goals,assists,xg,xa,cs,saves,yc,rc,bonus,titles,awards,honours,team_ppg,team_z,current,out,imp,cred,avail,hon,honpts,prev_perf,prev_games".split(",")
    cur_se = CURRENT_YEAR + 1
    status = {}
    with open("top5_players.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for name, code in LEAGUES.items():
            rows, rnd = build_league(name, code)
            status[name] = {"rows": len(rows), "round": rnd, "seasons": len({r["se"] for r in rows})}
            if not rows:
                continue
            prev = prev_snapshot(code, rnd) if rnd > 1 else {}
            snap = []
            for r in rows:
                pp = prev.get(r["name"]) if r["se"] == cur_se else None
                w.writerow([r["name"], "Football", r["pos"], name, 1, r["se"], round(r["min"] / 90, 1), r["perf"], "", r["by"], r["club"], r["apps"], int(r["min"]),
                            r["goals"], r["assists"], "", "", r["csreal"], r["saves"], r["yc"], r["rc"], "", r["titles"], r["awards"], "; ".join(r["honours"]),
                            r.get("tppg", ""), r.get("tz", ""), 1 if r["se"] == cur_se else 0, r["out"], r["imp"], r["cred"], r["avail"], r["hon"], r["honpts"],
                            pp[0] if pp else "", pp[1] if pp else ""])
                if r["se"] == cur_se:
                    snap.append((r["name"], r["perf"], round(r["min"] / 90, 1)))
            if rnd > 0 and snap:
                os.makedirs("state", exist_ok=True)
                with open(f"state/top5_{code}_r{rnd}.csv", "w", newline="", encoding="utf-8") as sf:
                    sw = csv.writer(sf); sw.writerow(["athlete", "perf", "games"]); sw.writerows(snap)
            print(name, len(rows), "player-seasons,", status[name]["seasons"], "seasons, round", rnd)
    json.dump({"leagues": status, "failed": FAILED}, open("top5_status.json", "w"))
    if FAILED:
        print("NOTES:", *FAILED[:30], sep="\n  ")


if __name__ == "__main__":
    main()
