"""AthScore data for La Liga, Serie A, Bundesliga and Ligue 1, 2014-15 to the current season. No API keys.
Source: Understat (understat.com) league pages, which carry every player's season goals, assists, xG, xA, key passes,
xGChain, xGBuildup, minutes and cards, plus every team's match-by-match points and goals.
Seasons that are finished are cached under state/understat/ and never fetched again; the current season is refreshed on every run.
If a live fetch fails, the cache is used, and for 2014-15 to 2020-21 the open douglasbc/scraping-understat-dataset copy is the fallback (players only).
Run:  python3 build_top5.py   -> top5_players.csv   (the Premier League has its own builder, build_pl_index.py)
Environment: UNDERSTAT_DIR=<folder> reads {slug}_{year}.json files instead of the network (used for tests).
"""
import csv, io, json, os, re, sys, time, collections, urllib.request, urllib.error
import build_pl_index as B   # reuse the rating engine: score(), components(), team_context(), TEAMTAB and the honour points

LEAGUES = {"La Liga": "La_liga", "Serie A": "Serie_A", "Bundesliga": "Bundesliga", "Ligue 1": "Ligue_1"}
FIRST_YEAR = 2014            # Understat's first season (2014-15)
CURRENT_YEAR = 2026          # season starting August 2026 = 2026-27 (our season number is the end year, 2027)
CACHE = "state/understat"
REPO = "https://raw.githubusercontent.com/douglasbc/scraping-understat-dataset/main/datasets/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en"}
POSMAP = {"GK": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
W_US = {"GK": {"cs": .50, "xgc": .50},
        "DEF": {"chain": .20, "build": .20, "xgi": .15, "kp": .10, "cs": .175, "xgc": .175},
        "MID": {"xgi": .30, "chain": .25, "build": .15, "kp": .15, "ga": .15},
        "FWD": {"xg": .30, "g": .15, "xa": .15, "kp": .10, "chain": .30}}
# honours that are cheap to hand-enter and unambiguous (season = end year). Domestic cups are not covered for these leagues.
CHAMPIONS_LEAGUE = {2015: "Barcelona", 2016: "Real Madrid", 2017: "Real Madrid", 2018: "Real Madrid", 2020: "Bayern Munich",
                    2022: "Real Madrid", 2024: "Real Madrid", 2025: "Paris Saint Germain", 2026: "Paris Saint Germain"}
EUROPA = {2015: "Sevilla", 2016: "Sevilla", 2018: "Atletico Madrid", 2020: "Sevilla", 2021: "Villarreal", 2022: "Eintracht Frankfurt",
          2023: "Sevilla", 2024: "Atalanta"}
DISPLAY_FIX = {"Kylian Mbappe-Lottin": "Kylian Mbappé"}
FAILED = []


def http(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def _json_from_script(html, var):
    m = re.search(r"var\s+" + var + r"\s*=\s*JSON\.parse\('(.*?)'\)", html, re.S)
    if not m:
        return None
    return json.loads(m.group(1).encode("utf-8").decode("unicode_escape"))


def live_fetch(slug, year):
    """Return {'players':[...], 'teams':{...}} from Understat, trying the page first and then its JSON endpoint."""
    err = None
    try:
        html = http(f"https://understat.com/league/{slug}/{year}")
        players, teams = _json_from_script(html, "playersData"), _json_from_script(html, "teamsData")
        if players and teams:
            return {"players": players, "teams": teams}
    except Exception as e:
        err = e
    try:
        txt = http(f"https://understat.com/getLeagueData/{slug}/{year}",
                   {"X-Requested-With": "XMLHttpRequest", "Referer": f"https://understat.com/league/{slug}/{year}"})
        d = json.loads(txt)
        players, teams = d.get("players"), d.get("teams")
        if isinstance(players, dict):
            players = list(players.values())
        if players and teams:
            return {"players": players, "teams": teams}
    except Exception as e:
        err = e
    raise RuntimeError(f"understat {slug} {year}: {err}")


def slim(raw):
    """Keep only the fields we use so the cache stays small."""
    keep = ("id", "player_name", "games", "time", "goals", "xG", "assists", "xA", "key_passes", "yellow_cards", "red_cards",
            "position", "team_title", "xGChain", "xGBuildup")
    players = [{k: p.get(k) for k in keep} for p in raw["players"]]
    teams = {}
    for t in (raw["teams"].values() if isinstance(raw["teams"], dict) else raw["teams"]):
        h = t.get("history") or []
        teams[t["title"]] = {"pts": sum(float(x["pts"]) for x in h), "games": len(h),
                             "cs": sum(1 for x in h if float(x["missed"]) == 0),
                             "xga": sum(float(x["xGA"]) for x in h), "gf": sum(float(x["scored"]) for x in h)}
    return {"players": players, "teams": teams}


def repo_fallback(slug, year):
    """Players-only copy for 2014-15 to 2020-21 from the open GitHub dataset (no team table)."""
    if year > 2020:
        return None
    fn = f"{slug.lower()}/players_{slug.lower()}_{str(year)[-2:]}-{str(year + 1)[-2:]}.csv"
    rows = list(csv.DictReader(io.StringIO(http(REPO + fn))))
    return {"players": rows, "teams": {}}


def load_season(slug, year):
    os.makedirs(CACHE, exist_ok=True)
    path = f"{CACHE}/{slug}_{year}.json"
    local = os.environ.get("UNDERSTAT_DIR")
    if local:
        return json.load(open(f"{local}/{slug}_{year}.json", encoding="utf-8"))
    if os.path.exists(path) and year < CURRENT_YEAR:
        return json.load(open(path, encoding="utf-8"))
    try:
        d = slim(live_fetch(slug, year))
        json.dump(d, open(path, "w", encoding="utf-8"), separators=(",", ":"))
        time.sleep(1.5)       # be polite to Understat
        return d
    except Exception as e:
        FAILED.append(f"{slug} {year}: {e}")
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8"))
        try:
            return repo_fallback(slug, year)
        except Exception as e2:
            FAILED.append(f"{slug} {year} fallback: {e2}")
            return None


def fl(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def pos_of(code):
    for t in str(code).split():
        if t in POSMAP:
            return POSMAP[t]
    return "MID"


def build_league(name, slug):
    B.TEAMTAB.clear()
    rows = []
    teams_by_se = {}
    for year in range(FIRST_YEAR, CURRENT_YEAR + 1):
        se = year + 1
        d = load_season(slug, year)
        if not d or not d["players"]:
            continue
        teams = d.get("teams") or {}
        teams_by_se[se] = teams
        src = "u_" + slug
        for t, v in teams.items():
            B.TEAMTAB[(src, se, t)] = (v["pts"], v["games"])
        season_rows = []
        for p in d["players"]:
            mins = fl(p["time"])
            if mins <= 0:
                continue
            club = str(p["team_title"]).split(",")[-1].strip()
            tv = teams.get(club) or {}
            tg = tv.get("games") or 38
            m = max(mins, 1) / 90
            xg, xa, gs, ast = fl(p["xG"]), fl(p["xA"]), fl(p["goals"]), fl(p["assists"])
            cs_proxy = (tv.get("cs", 0) * min(1.0, mins / (tg * 90))) if tv else 0.0
            met = dict(xg=xg / m, xa=xa / m, xgi=(xg + xa) / m, g=gs / m, ga=(gs + ast) / m,
                       chain=fl(p.get("xGChain")) / m, build=fl(p.get("xGBuildup")) / m, kp=fl(p.get("key_passes")) / m,
                       cs=(tv.get("cs", 0) / tg) if tv else 0.0, xgc=-((tv.get("xga", 0) / tg) if tv else 0.0))
            season_rows.append(dict(tkey=(src, se, club), src="us", pid=f"u{p['id']}", name=p["player_name"], pos=pos_of(p["position"]), se=se,
                                    club=club, by="", min=mins, apps=int(fl(p["games"])), goals=int(gs), assists=int(ast), cs=cs_proxy,
                                    yc=int(fl(p["yellow_cards"])), rc=int(fl(p["red_cards"])), xg=round(xg, 1), xa=round(xa, 1),
                                    saves="", bonus="", m=met, honours=[], titles=0))
        if season_rows:
            mx = max(r["min"] for r in season_rows)
            B.score(season_rows, W_US, min(450, 0.5 * mx))
            rows += season_rows
    if not rows:
        return [], 0
    for r in rows:
        r["name"] = DISPLAY_FIX.get(r["name"], r["name"])
    # unique names (two different Understat ids with one name get the id appended)
    idsby = collections.defaultdict(set)
    for r in rows:
        idsby[r["name"]].add(r["pid"])
    for r in rows:
        if len(idsby[r["name"]]) > 1:
            r["name"] = f"{r['name']} ({r['pid'][1:]})"
    byse = collections.defaultdict(list)
    for r in rows:
        byse[r["se"]].append(r)
    # honours derived from the data and the lists above
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
        for lab, table in (("Champions League", CHAMPIONS_LEAGUE), ("European trophy", EUROPA)):
            k = table.get(se)
            for r in rs:
                if k and r["club"] == k and r["apps"] >= B.SQUAD_MIN_APPS:
                    r["honours"].append(lab)
    for r in rows:
        r["honpts"] = min(B.HONOUR_CAP, sum(B.HONOUR_PTS.get(h, 0) for h in r["honours"]))
        r["hon"] = round(100 * r["honpts"] / B.HONOUR_CAP, 1)
        r["awards"] = sum(1 for h in r["honours"] if h != "League champions")
    B.team_context(rows)
    B.components(rows)
    cur_games = max((v["games"] for v in (teams_by_se.get(CURRENT_YEAR + 1) or {}).values()), default=0)
    return rows, cur_games


def prev_snapshot(slug, rnd):
    best = 0
    if os.path.isdir("state"):
        for fn in os.listdir("state"):
            m = re.fullmatch(rf"top5_{slug}_r(\d+)\.csv", fn)
            if m and int(m.group(1)) < rnd:
                best = max(best, int(m.group(1)))
    if not best:
        return {}
    out = {}
    for r in csv.DictReader(open(f"state/top5_{slug}_r{best}.csv", encoding="utf-8")):
        out[r["athlete"]] = (float(r["perf"]), float(r["games"]))
    return out


def main():
    hdr = "athlete,sport,position,competition,tier,season,games,perf,stat,birth_year,note,apps,minutes,goals,assists,xg,xa,cs,saves,yc,rc,bonus,titles,awards,honours,team_ppg,team_z,current,out,imp,cred,avail,hon,honpts,prev_perf,prev_games".split(",")
    cur_se = CURRENT_YEAR + 1
    status = {}
    with open("top5_players.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for name, slug in LEAGUES.items():
            rows, rnd = build_league(name, slug)
            status[name] = {"rows": len(rows), "round": rnd}
            if not rows:
                continue
            prev = prev_snapshot(slug, rnd) if rnd > 1 else {}
            snap = []
            for r in rows:
                pp = prev.get(r["name"]) if r["se"] == cur_se else None
                w.writerow([r["name"], "Football", r["pos"], name, 1, r["se"], round(r["min"] / 90, 1), r["perf"], "", "", r["club"], r["apps"], int(r["min"]),
                            r["goals"], r["assists"], r["xg"], r["xa"], "", "", r["yc"], r["rc"], "", r["titles"], r["awards"], "; ".join(r["honours"]),
                            r.get("tppg", ""), r.get("tz", ""), 1 if r["se"] == cur_se else 0, r["out"], r["imp"], r["cred"], r["avail"], r["hon"], r["honpts"],
                            pp[0] if pp else "", pp[1] if pp else ""])
                if r["se"] == cur_se:
                    snap.append((r["name"], r["perf"], round(r["min"] / 90, 1)))
            if rnd > 0 and snap:
                os.makedirs("state", exist_ok=True)
                with open(f"state/top5_{slug}_r{rnd}.csv", "w", newline="", encoding="utf-8") as sf:
                    sw = csv.writer(sf); sw.writerow(["athlete", "perf", "games"]); sw.writerows(snap)
            print(name, len(rows), "player-seasons, round", rnd)
    json.dump({"leagues": status, "failed": FAILED}, open("top5_status.json", "w"))
    if FAILED:
        print("FETCH PROBLEMS:", *FAILED, sep="\n  ")


if __name__ == "__main__":
    main()
