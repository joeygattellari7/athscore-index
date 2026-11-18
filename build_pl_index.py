"""AthScore dataset for every Premier League player, 1992-93 to the current season. No API keys.
 1992-93 .. 2015-16 : pssguy/epldata (appearances, goals, assists, cards, clean sheets)
 2016-17 .. now     : vaastav/Fantasy-Premier-League (adds xG, xA, saves, bonus, ICT)
Run:  pip install pyreadr pandas ; python3 build_pl_index.py   -> pl_players.csv
"""
import csv, io, re, unicodedata, urllib.request, difflib, collections, os
import pandas as pd, pyreadr

FPL="https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/"
EPL="https://raw.githubusercontent.com/pssguy/epldata/master/data/"
FPL_SEASONS=["2016-17","2017-18","2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26","2026-27"]
CURRENT=2027
POS={"1":"GK","2":"DEF","3":"MID","4":"FWD"}
OLD_POS={"G":"GK","D":"DEF","M":"MID","F":"FWD","Goalkeeper":"GK","Defender":"DEF","Midfielder":"MID","Forward":"FWD"}
MANUAL_CODES={"25":"Middlesbrough","56":"Sunderland","88":"Hull City","110":"Stoke City","80":"Swansea City","38":"Huddersfield","97":"Cardiff City"}
CUT_GW=None      # set to N to rebuild the current season using only gameweeks 1..N (used for the 'previous round' snapshot)
LATEST_GW=0
REPO_GW=0
API_BOOT=os.environ.get('FPL_BOOTSTRAP','')   # optional: path to bootstrap-static JSON saved from fantasy.premierleague.com/api/bootstrap-static/
API_FIX=os.environ.get('FPL_FIXTURES','')      # optional: path to fixtures JSON saved from fantasy.premierleague.com/api/fixtures/
CACHE=os.environ.get("EPL_CACHE","")

def fetch_api():
    """Download the official Fantasy Premier League feeds (works from GitHub Actions; many sandboxes block it)."""
    global API_BOOT,API_FIX
    for name,url in (("bootstrap","https://fantasy.premierleague.com/api/bootstrap-static/"),("fixtures","https://fantasy.premierleague.com/api/fixtures/")):
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (AthScore Index updater)"})
        data=urllib.request.urlopen(req,timeout=120).read()
        path=f"/tmp/fpl_{name}.json"; open(path,"wb").write(data)
        if name=="bootstrap": API_BOOT=path
        else: API_FIX=path

STATE_DIR="state"
def load_state(latest):
    best=None
    if os.path.isdir(STATE_DIR):
        for fn in os.listdir(STATE_DIR):
            m=re.fullmatch(r"gw_(\d+)\.csv",fn)
            if m and int(m.group(1))<latest and (best is None or int(m.group(1))>best): best=int(m.group(1))
    if best is None: return {}
    print("previous-round snapshot: gameweek",best)
    return {(r["athlete"],CURRENT):(float(r["perf"]),float(r["games"])) for r in csv.DictReader(open(f"{STATE_DIR}/gw_{best}.csv",encoding="utf-8"))}
def save_state(final_rows,label):
    os.makedirs(STATE_DIR,exist_ok=True)
    with open(f"{STATE_DIR}/gw_{LATEST_GW}.csv","w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh); w.writerow(["athlete","perf","games"])
        for r in final_rows:
            if r["se"]==CURRENT and r["min"]>0: w.writerow([label(r["pid"]),r["perf"],round(r["min"]/90,1)])

def fetch(url):
    return urllib.request.urlopen(url,timeout=120).read()
def fcsv(path):
    return list(csv.DictReader(io.StringIO(fetch(FPL+path).decode("utf-8","replace"))))
def f(x):
    try: return float(x)
    except: return 0.0
def norm(s):
    s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z ]"," ",s).split()

# ---------------------------------------------------------------- scoring helpers
def pct(sorted_vals,v):
    lo=sum(1 for s in sorted_vals if s<v); eq=sum(1 for s in sorted_vals if s==v)
    return 100*(lo+0.5*eq)/len(sorted_vals)
def score(rows,weights,pool_min):
    """Output component: weighted z-scores within position and season (not percentiles, so elite seasons stay separated).
    Adds 'out' (0-100, 50 = typical regular, +16 per standard deviation, capped)."""
    import statistics
    for pos in ("GK","DEF","MID","FWD"):
        grp=[r for r in rows if r["pos"]==pos]
        pool=[r for r in grp if r["min"]>=pool_min] or grp
        if len(pool)<3:
            for r in grp: r["out"]=50.0
            continue
        ref={k:(statistics.mean(r["m"][k] for r in pool),statistics.pstdev([r["m"][k] for r in pool]) or 1) for k in weights[pos]}
        for r in grp: r["comp"]=sum(w*max(-2.5,min(3.5,(r["m"][k]-ref[k][0])/ref[k][1])) for k,w in weights[pos].items())
        pc=[r["comp"] for r in pool]; mu=statistics.mean(pc); sd=statistics.pstdev(pc) or 1
        for r in grp: r["out"]=round(max(0.0,min(100.0,50+16*(r["comp"]-mu)/sd)),1)

W_FPL_XG={"GK":{"shot":.35,"sav":.15,"cs":.20,"xgc":.15,"inf":.15},
   "DEF":{"xgi":.25,"inf":.25,"cre":.10,"cs":.20,"xgc":.20},
   "MID":{"xgi":.35,"cre":.20,"thr":.15,"inf":.20,"ga":.10},
   "FWD":{"xg":.30,"g":.20,"xa":.15,"thr":.20,"bon":.15}}
W_OLD={"GK":{"cs":.50,"start":.30,"gcinv":.20},
   "DEF":{"cs":.40,"gi":.20,"start":.30,"cardinv":.10},
   "MID":{"g":.30,"a":.30,"start":.25,"cs":.15},
   "FWD":{"g":.50,"a":.30,"start":.20}}

# ---------------------------------------------------------------- part A: epldata 1993-2018
TEAMTAB={}   # (source, season, team) -> (points, games)
def load_epl():
    d={}
    for n in ["players","player_game","game","game_team","goals","assists","player_team"]:
        raw=fetch(EPL+n+".rda"); open("/tmp/_"+n+".rda","wb").write(raw)
        d[n]=list(pyreadr.read_r("/tmp/_"+n+".rda").values())[0]
    pg,gm,gt,gl,asd,pt,pl=d["player_game"],d["game"],d["game_team"],d["goals"],d["assists"],d["player_team"],d["players"]
    gm["se"]=gm.game_date.apply(lambda x:x.year+1 if x.month>=7 else x.year)
    tg=gt.merge(gm[["game_id","se"]],on="game_id")
    x=pg.merge(pt[["player_team_id","player_id","team"]],on="player_team_id").merge(tg[["team_game_id","game_id","se"]],on="team_game_id")
    x["time_on"]=pd.to_numeric(x.time_on,errors="coerce").fillna(0); x["time_off"]=pd.to_numeric(x.time_off,errors="coerce").fillna(0)
    x["played"]=x.start | (x.time_on>0)
    end=x.time_off.where(x.time_off>0,90)
    x["mins"]=((end-x.time_on.where(~x.start,0))).clip(lower=0).where(x.played,0)
    gl["pen"]=(gl.play=="P").astype(int)
    g=gl.groupby("player_game_id").agg(goals=("goal_id","count"),pens=("pen","sum")).reset_index()
    a=asd.groupby("player_game_id").size().rename("assists").reset_index()
    x=x.merge(g,on="player_game_id",how="left").merge(a,on="player_game_id",how="left").fillna({"goals":0,"pens":0,"assists":0})
    x["og"]=pd.to_numeric(x.own_goals,errors="coerce").fillna(0)
    # team goals against per team_game
    gfor=x.groupby("team_game_id").goals.sum()
    ogs=x.groupby("team_game_id").og.sum()
    pair=gt.merge(gt,on="game_id",suffixes=("","_o")); pair=pair[pair.team_game_id!=pair.team_game_id_o]
    pair["ga"]=pair.team_game_id_o.map(gfor).fillna(0)+pair.team_game_id.map(ogs).fillna(0)
    x["ga"]=x.team_game_id.map(pair.set_index("team_game_id").ga).fillna(0)
    pair["gf"]=pair.team_game_id.map(gfor).fillna(0)+pair.team_game_id_o.map(ogs).fillna(0)
    pair["pts"]=(pair.gf>pair.ga)*3+(pair.gf==pair.ga)*1
    pair["team"]=pair.team_game_id.map(gt.set_index("team_game_id").team); pair["se"]=pair.team_game_id.map(tg.set_index("team_game_id").se)
    ttab=pair.groupby(["team","se"]).agg(pts=("pts","sum"),g=("pts","count"))
    TEAMTAB.update({("e",int(se),t):(float(r.pts),int(r.g)) for (t,se),r in ttab.iterrows()})
    x["cs"]=((x.mins>=60)&(x.ga==0)).astype(int)
    x["yc"]=x.caution.astype(str).str.strip().str.upper().isin(["Y","YY","YW"]).astype(int)
    x["rc"]=(x.caution.astype(str).str.strip().str.upper()=="R").astype(int)
    x["startn"]=x.start.astype(int); x["app"]=x.played.astype(int)
    s=x[x.played].groupby(["player_id","se","team"]).agg(apps=("app","sum"),starts=("startn","sum"),minutes=("mins","sum"),goals=("goals","sum"),
        pens=("pens","sum"),assists=("assists","sum"),cs=("cs","sum"),yc=("yc","sum"),rc=("rc","sum"),gc=("ga","sum")).reset_index()
    # player may have several clubs in a season: keep the club with most minutes in note, sum the rest
    s=s.sort_values("minutes",ascending=False)
    club=s.drop_duplicates(["player_id","se"]).set_index(["player_id","se"]).team
    tot=s.groupby(["player_id","se"]).sum(numeric_only=True).reset_index(); tot["team"]=[club[(p,e)] for p,e in zip(tot.player_id,tot.se)]
    tgames=gt.merge(gm[["game_id","se"]],on="game_id").groupby(["team","se"]).size().rename("tgames").reset_index()
    tot=tot.merge(tgames,on=["team","se"],how="left")
    pl["position2"]=pl.position.map(OLD_POS)
    tot=tot.merge(pl[["player_id","first_name","last_name","birth_date","position2"]],on="player_id",how="left")
    out=[]
    for r in tot.itertuples():
        if r.position2 is None or pd.isna(r.position2): continue
        by=r.birth_date.year if pd.notna(r.birth_date) else ""
        m=max(r.minutes,1)/90
        out.append(dict(tkey=("e",int(r.se),r.team),src="epl",pid=r.player_id,name=(str(r.first_name)+" "+str(r.last_name)).strip(),pos=r.position2,se=int(r.se),club=r.team,by=by,
            min=r.minutes,apps=int(r.apps),goals=int(r.goals),assists=int(r.assists),cs=int(r.cs),yc=int(r.yc),rc=int(r.rc),xg="",xa="",saves="",bonus="",
            m=dict(g=r.goals/m,a=r.assists/m,gi=(r.goals+r.assists)/m,cs=r.cs/max(r.apps,1),start=r.starts/max(r.tgames or 38,1),
                   gcinv=-r.gc/m,cardinv=-(r.yc+3*r.rc)/m)))
    return out

# ---------------------------------------------------------------- part B: FPL 2016-17 .. now
SUM_COLS=['minutes','goals_scored','assists','clean_sheets','saves','bonus','creativity','threat','influence','yellow_cards','red_cards','goals_conceded','penalties_saved','expected_goals','expected_assists','expected_goals_conceded']
def load_fpl():
    code2name={}
    for s in FPL_SEASONS:
        try:
            for t in fcsv(f"{s}/teams.csv"):
                if t.get("code"): code2name[t["code"]]=t["name"]
        except Exception: pass
    code2name.update({k:v for k,v in MANUAL_CODES.items() if k not in code2name})
    out=[]; current=set()
    for s in FPL_SEASONS:
        se=int(s[:4])+1
        players=fcsv(f"{s}/players_raw.csv")
        try: tnames={t["id"]:t["name"] for t in fcsv(f"{s}/teams.csv")}
        except Exception: tnames={}
        use_api=bool(API_BOOT and API_FIX and se==CURRENT and not CUT_GW)
        if use_api:
            import json
            boot=json.load(open(API_BOOT,encoding="utf-8")); fxl=json.load(open(API_FIX,encoding="utf-8"))
            players=[{k:("" if v is None else str(v)) for k,v in e.items()} for e in boot["elements"]]
            tnames={str(t["id"]):t["name"] for t in boot["teams"]}
        apps=collections.Counter(); el2team={p["id"]:p["team"] for p in players}; fx={}
        gwrows=[]
        try:
            for r in csv.DictReader(io.StringIO(fetch(FPL+f"{s}/gws/merged_gw.csv").decode("utf-8","replace"))):
                global LATEST_GW, REPO_GW
                if se==CURRENT:
                    REPO_GW=max(REPO_GW,int(f(r.get("GW"))))
                    if not use_api: LATEST_GW=max(LATEST_GW,int(f(r.get("GW"))))
                    if CUT_GW and int(f(r.get("GW")))>CUT_GW: continue
                    gwrows.append(r)
                if use_api: continue
                if f(r.get("minutes"))>0: apps[r["element"]]+=1
                hs,as_=r.get("team_h_score",""),r.get("team_a_score","")
                if hs not in ("","None") and as_ not in ("","None") and r["element"] in el2team and r.get("fixture"):
                    me,opp=el2team[r["element"]],r["opponent_team"]
                    home,away=(me,opp) if r.get("was_home")=="True" else (opp,me)
                    fx[r["fixture"]]=(home,away,int(float(hs)),int(float(as_)))
        except Exception: pass
        if use_api:
            LATEST_GW=max([int(x["event"]) for x in fxl if x.get("finished") and x.get("event")] or [0])
            for x in fxl:
                if x.get("finished") and x.get("team_h_score") is not None:
                    fx[str(x["id"])]=(str(x["team_h"]),str(x["team_a"]),int(x["team_h_score"]),int(x["team_a_score"]))
            for p in players: apps[p["id"]]=max(int(f(p.get("starts"))),int(-(-f(p["minutes"])//90)) if f(p["minutes"])>0 else 0)
        for home,away,hs,as_ in fx.values():
            for t,gf,ga in ((home,hs,as_),(away,as_,hs)):
                p0,g0=TEAMTAB.get(("f",se,t),(0.0,0)); TEAMTAB[("f",se,t)]=(p0+(3 if gf>ga else 1 if gf==ga else 0),g0+1)
        if se==CURRENT and CUT_GW:
            tot=collections.defaultdict(lambda: collections.defaultdict(float))
            for r in gwrows:
                for k in SUM_COLS: tot[r["element"]][k]+=f(r.get(k))
            for p in players:
                t=tot.get(p["id"],{})
                for k in SUM_COLS: p[k]=t.get(k,0.0)
        played=[p for p in players if f(p["minutes"])>0]
        if se==CURRENT: current={p["code"] for p in played if p["status"]!="u"}
        has_x="expected_goals" in players[0]
        rows=[]
        for p in played:
            m=max(f(p["minutes"]),1)/90
            xg,xa=f(p.get("expected_goals")),f(p.get("expected_assists"))
            gs,ast=f(p["goals_scored"]),f(p["assists"])
            xgc=f(p.get("expected_goals_conceded")) if has_x else f(p["goals_conceded"])
            sv=f(p["saves"])
            met=dict(xg=(xg if has_x else gs)/m,xa=(xa if has_x else ast)/m,xgi=((xg+xa) if has_x else (gs+ast))/m,g=gs/m,ga=(gs+ast)/m,
                cre=f(p["creativity"])/m,thr=f(p["threat"])/m,inf=f(p["influence"])/m,bon=f(p["bonus"])/m,cs=f(p["clean_sheets"])/m,xgc=-xgc/m,sav=sv/m,
                shot=((xgc-f(p["goals_conceded"]))/m if has_x else sv/max(sv+f(p["goals_conceded"]),1))+f(p["penalties_saved"])*0.8/m)
            club=tnames.get(p["team"]) or code2name.get(p.get("team_code",""),"")
            by=(p.get("birth_date") or "")[:4]
            rows.append(dict(tkey=("f",se,p["team"]),src="fpl",pid=p["code"],name=(p.get("known_name") or (p["first_name"]+" "+p["second_name"])).strip(),pos=POS[p["element_type"]],se=se,
                club=club,by=by if by.isdigit() else "",min=f(p["minutes"]),apps=apps.get(p["id"],0),goals=int(gs),assists=int(ast),cs=int(f(p["clean_sheets"])),
                yc=int(f(p["yellow_cards"])),rc=int(f(p["red_cards"])),xg=round(xg,1) if has_x else "",xa=round(xa,1) if has_x else "",saves=int(sv),bonus=int(f(p["bonus"])),m=met))
        mx=max(r["min"] for r in rows); score(rows,W_FPL_XG,min(450,0.5*mx)); out+=rows
    return out,current


# ---------------------------------------------------------------- honours (hand-entered from the official lists)
CHAMPIONS={1993:"mu",1994:"mu",1995:"bla",1996:"mu",1997:"mu",1998:"ars",1999:"mu",2000:"mu",2001:"mu",2002:"ars",2003:"mu",2004:"ars",2005:"che",2006:"che",
 2007:"mu",2008:"mu",2009:"mu",2010:"che",2011:"mu",2012:"mc",2013:"mu",2014:"mc",2015:"che",2016:"lei",2017:"che",2018:"mc",2019:"mc",2020:"liv",
 2021:"mc",2022:"mc",2023:"mc",2024:"mc",2025:"liv",2026:"ars"}
TITLE_MIN_APPS=10   # appearances needed for a season to count as a title for the player
GOLDEN_BOOT={1993:["Teddy Sheringham"],1994:["Andy Cole"],1995:["Alan Shearer"],1996:["Alan Shearer"],1997:["Alan Shearer"],1998:["Chris Sutton","Dion Dublin","Michael Owen"],
 1999:["Michael Owen","Dwight Yorke","Jimmy Floyd Hasselbaink"],2000:["Kevin Phillips"],2001:["Jimmy Floyd Hasselbaink"],2002:["Thierry Henry"],2003:["Ruud van Nistelrooy"],
 2004:["Thierry Henry"],2005:["Thierry Henry"],2006:["Thierry Henry"],2007:["Didier Drogba"],2008:["Cristiano Ronaldo"],2009:["Nicolas Anelka"],2010:["Didier Drogba"],
 2011:["Carlos Tevez","Dimitar Berbatov"],2012:["Robin van Persie"],2013:["Robin van Persie"],2014:["Luis Suarez"],2015:["Sergio Aguero"],2016:["Harry Kane"],2017:["Harry Kane"],
 2018:["Mohamed Salah"],2019:["Pierre-Emerick Aubameyang","Sadio Mane","Mohamed Salah"],2020:["Jamie Vardy"],2021:["Harry Kane"],2022:["Mohamed Salah","Son Heung-min"],
 2023:["Erling Haaland"],2024:["Erling Haaland"],2025:["Mohamed Salah"],2026:["Erling Haaland"]}
PLAYER_OF_SEASON={1995:"Alan Shearer",1996:"Peter Schmeichel",1997:"Juninho",1998:"Michael Owen",1999:"Dwight Yorke",2000:"Kevin Phillips",2001:"Patrick Vieira",
 2002:"Freddie Ljungberg",2003:"Ruud van Nistelrooy",2004:"Thierry Henry",2005:"Frank Lampard",2006:"Thierry Henry",2007:"Cristiano Ronaldo",2008:"Cristiano Ronaldo",
 2009:"Nemanja Vidic",2010:"Wayne Rooney",2011:"Nemanja Vidic",2012:"Vincent Kompany",2013:"Gareth Bale",2014:"Luis Suarez",2015:"Eden Hazard",2016:"Jamie Vardy",
 2017:"N'Golo Kante",2018:"Mohamed Salah",2019:"Virgil van Dijk",2020:"Kevin De Bruyne",2021:"Ruben Dias",2022:"Kevin De Bruyne",2023:"Erling Haaland",2024:"Phil Foden",
 2025:"Mohamed Salah",2026:"Bruno Fernandes"}
GOLDEN_GLOVE={2005:["Petr Cech"],2006:["Pepe Reina"],2007:["Pepe Reina"],2008:["Pepe Reina"],2009:["Edwin van der Sar"],2010:["Petr Cech"],2011:["Joe Hart"],2012:["Joe Hart"],
 2013:["Joe Hart"],2014:["Petr Cech","Wojciech Szczesny"],2015:["Joe Hart"],2016:["Petr Cech"],2017:["Thibaut Courtois"],2018:["David de Gea"],2019:["Alisson"],2020:["Ederson"],
 2021:["Ederson"],2022:["Alisson","Ederson"],2023:["David de Gea"],2024:["David Raya"],2025:["David Raya","Matz Sels"],2026:["David Raya"]}
def club_key(c):
    n=re.sub(r"[^a-z ]","",str(c).lower())
    if n.startswith("spurs"): return "tot"
    if n.startswith("man"): return "mu" if ("utd" in n or "united" in n) else "mc"
    return n[:3]
def find_player(rows,name,club=None):
    toks=norm(name); best=None; bs=0
    for r in rows:
        ct=norm(r["name"])
        if not ct or not toks: continue
        if toks[-1] not in ct and not any(difflib.SequenceMatcher(None,toks[-1],t).ratio()>0.85 for t in ct): continue
        sc=difflib.SequenceMatcher(None," ".join(toks)," ".join(ct)).ratio()
        if ct[0][0]==toks[0][0]: sc+=0.3
        sc+=0.001*r["min"]/90
        if sc>bs: bs=sc;best=r
    return best if bs>0.7 else None
def add_honours(rows):
    byse=collections.defaultdict(list)
    for r in rows: byse[r["se"]].append(r)
    for r in rows: r["titles"]=0; r["honours"]=[]
    for se,key in CHAMPIONS.items():
        for r in byse.get(se,[]):
            if club_key(r["club"])==key and r["apps"]>=TITLE_MIN_APPS: r["titles"]=1; r["honours"].append("League champions")
    miss=[]
    for label,table in (("Golden Boot",GOLDEN_BOOT),("Player of the Season",{k:[v] for k,v in PLAYER_OF_SEASON.items()}),("Golden Glove",GOLDEN_GLOVE)):
        for se,names in table.items():
            for nm in names:
                p=find_player(byse.get(se,[]),nm)
                if p: p["honours"].append(label)
                else: miss.append((label,se,nm))
    for r in rows: r["awards"]=sum(1 for h in r["honours"] if h!="League champions")
    return miss


FA_CUP={1993:"ars",1994:"mu",1995:"eve",1996:"mu",1997:"che",1998:"ars",1999:"mu",2000:"che",2001:"liv",2002:"ars",2003:"ars",2004:"mu",2005:"ars",2006:"liv",2007:"che",
 2008:"por",2009:"che",2010:"che",2011:"mc",2012:"che",2013:"wig",2014:"ars",2015:"ars",2016:"mu",2017:"ars",2018:"che",2019:"mc",2020:"ars",2021:"lei",2022:"liv",
 2023:"mc",2024:"mu",2025:"cry",2026:"mc"}
LEAGUE_CUP={1993:"ars",1994:"ast",1995:"liv",1996:"ast",1997:"lei",1998:"che",1999:"tot",2000:"lei",2001:"liv",2002:"bla",2003:"liv",2004:"mid",2005:"che",2006:"mu",
 2007:"che",2008:"tot",2009:"mu",2010:"mu",2011:"bir",2012:"liv",2013:"swa",2014:"mc",2015:"che",2016:"mc",2017:"mu",2018:"mc",2019:"mc",2020:"mc",2021:"mc",2022:"liv",
 2023:"mu",2024:"liv",2025:"new",2026:"mc"}
CHAMPIONS_LEAGUE={1999:"mu",2005:"liv",2008:"mu",2012:"che",2019:"liv",2021:"che",2023:"mc"}
EUROPA={1994:"ars",1998:"che",2001:"liv",2013:"che",2017:"mu",2019:"che",2025:"tot",2026:"ast"}   # UEFA Cup / Cup Winners' Cup / Europa League
PFA_POTY={1993:"Paul McGrath",1994:"Eric Cantona",1995:"Alan Shearer",1996:"Les Ferdinand",1997:"Alan Shearer",1998:"Dennis Bergkamp",1999:"David Ginola",2000:"Roy Keane",
 2001:"Teddy Sheringham",2002:"Ruud van Nistelrooy",2003:"Thierry Henry",2004:"Thierry Henry",2005:"John Terry",2006:"Steven Gerrard",2007:"Cristiano Ronaldo",
 2008:"Cristiano Ronaldo",2009:"Ryan Giggs",2010:"Wayne Rooney",2011:"Gareth Bale",2012:"Robin van Persie",2013:"Gareth Bale",2014:"Luis Suarez",2015:"Eden Hazard",
 2016:"Riyad Mahrez",2017:"N'Golo Kante",2018:"Mohamed Salah",2019:"Virgil van Dijk",2020:"Kevin De Bruyne",2021:"Ruben Dias",2022:"Mohamed Salah",2023:"Erling Haaland",
 2024:"Phil Foden",2025:"Mohamed Salah",2026:"Bruno Fernandes"}
FWA={1993:"Chris Waddle",1994:"Alan Shearer",1995:"Jurgen Klinsmann",1996:"Eric Cantona",1997:"Gianfranco Zola",1998:"Dennis Bergkamp",1999:"David Ginola",2000:"Roy Keane",
 2001:"Teddy Sheringham",2002:"Robert Pires",2003:"Thierry Henry",2004:"Thierry Henry",2005:"Frank Lampard",2006:"Thierry Henry",2007:"Cristiano Ronaldo",
 2008:"Cristiano Ronaldo",2009:"Steven Gerrard",2010:"Wayne Rooney",2011:"Scott Parker",2012:"Robin van Persie",2013:"Gareth Bale",2014:"Luis Suarez",2015:"Eden Hazard",
 2016:"Jamie Vardy",2017:"N'Golo Kante",2018:"Mohamed Salah",2019:"Raheem Sterling",2020:"Jordan Henderson",2021:"Ruben Dias",2022:"Mohamed Salah",2023:"Erling Haaland",
 2024:"Phil Foden",2025:"Mohamed Salah",2026:"Bruno Fernandes"}
SQUAD_MIN_APPS=15   # team honours: a regular squad member (proxy: no cup appearance data exists in the free sources)
HONOUR_PTS={"League champions":6.0,"Champions League":6.0,"European trophy":3.5,"FA Cup":2.5,"League Cup":1.5,"Golden Boot":3.0,"Player of the Season":3.0,
            "PFA Players' Player":3.0,"FWA Footballer":2.0,"Golden Glove":2.5}
HONOUR_CAP=15.0

def add_team_honours(rows):
    for label,table in (("FA Cup",FA_CUP),("League Cup",LEAGUE_CUP),("Champions League",CHAMPIONS_LEAGUE),("European trophy",EUROPA)):
        for r in rows:
            k=table.get(r["se"])
            if k and club_key(r["club"])==k and r["apps"]>=SQUAD_MIN_APPS: r["honours"].append(label)
    byse=collections.defaultdict(list)
    for r in rows: byse[r["se"]].append(r)
    miss=[]
    for label,table in (("PFA Players' Player",PFA_POTY),("FWA Footballer",FWA)):
        for se,nm in table.items():
            p=find_player(byse.get(se,[]),nm)
            if p: p["honours"].append(label)
            else: miss.append((label,se,nm))
    for r in rows:
        r["honpts"]=min(HONOUR_CAP,sum(HONOUR_PTS.get(h,0) for h in r["honours"]))
        r["hon"]=round(100*r["honpts"]/HONOUR_CAP,1)
        r["awards"]=sum(1 for h in r["honours"] if h!="League champions")
    return miss

def components(rows):
    """Match impact, team credit and availability, all from the season rows."""
    import statistics
    tgoals=collections.Counter(); tcs=collections.Counter()
    for r in rows:
        tgoals[r["tkey"]]+=r["goals"]; tcs[r["tkey"]]=max(tcs[r["tkey"]],r["cs"])
    for r in rows:
        tg=TEAMTAB.get(r["tkey"],(0,0))[1] or 38
        r["share"]=min(1.0,r["min"]/(tg*90))
        if r["pos"] in ("MID","FWD"): r["imp_raw"]=(r["goals"]+r["assists"])/max(tgoals[r["tkey"]],1)
        else: r["imp_raw"]=r["cs"]/max(tcs[r["tkey"]],1)+0.5*(r["goals"]+r["assists"])/max(tgoals[r["tkey"]],1)
    grp=collections.defaultdict(list)
    for r in rows: grp[(r["se"],r["pos"])].append(r)
    for g in grp.values():
        pool=[r for r in g if r["share"]>=0.2] or g
        mu=statistics.mean(r["imp_raw"] for r in pool); sd=statistics.pstdev([r["imp_raw"] for r in pool]) or 1
        for r in g: r["imp"]=round(max(0.0,min(100.0,50+16*max(-2.5,min(3.5,(r["imp_raw"]-mu)/sd)))),1)
    for r in rows:
        tz=r["tz"] if r["tz"]!="" else 0.0
        r["cred"]=round(max(0.0,min(100.0,50+22*max(-2.0,min(2.5,tz))*min(1.0,r["share"]/0.5))),1)
        r["avail"]=round(max(0.0,min(100.0,100*min(1.0,r["share"]/0.85)-1.2*r["yc"]-10*r["rc"])),1)
        r["perf"]=round(0.35*r["out"]+0.20*r["imp"]+0.20*r["cred"]+0.15*r["hon"]+0.10*r["avail"],1)

def team_context(rows):
    import statistics
    shr={}
    for (src,se,t),(pts,g) in TEAMTAB.items():
        shr[(src,se,t)]=(pts+1.37*5)/(g+5)
    byse=collections.defaultdict(list)
    for (src,se,t),v in shr.items(): byse[(src,se)].append(v)
    stat={k:(statistics.mean(v),statistics.pstdev(v) or 1) for k,v in byse.items()}
    for r in rows:
        k=r["tkey"]
        if k in shr:
            m,sd=stat[(k[0],k[1])]; r["tppg"]=round(shr[k],2); r["tz"]=round((shr[k]-m)/sd,2)
        else: r["tppg"]=""; r["tz"]=""

def main(cut=None,write=True):
    global CUT_GW
    if cut is None and write and os.environ.get('FPL_LIVE') and not API_BOOT: fetch_api()
    CUT_GW=cut; TEAMTAB.clear()
    old=load_epl(); new,current=load_fpl()
    # score epldata seasons (each season/position separately), 1993..2016 only; 2017-18 used for linking
    byse=collections.defaultdict(list)
    for r in old: byse[r["se"]].append(r)
    for se,rows in byse.items():
        mx=max(r["min"] for r in rows); score(rows,W_OLD,min(900,0.5*mx))
    link_old=[r for r in old if r["se"] in (2017,2018)]
    old=[r for r in old if r["se"]<=2016]
    # link epldata ids to FPL codes using 2017 and 2018 seasons: same season, matching surname and first initial
    fidx=collections.defaultdict(list)
    for r in new:
        if r["se"] in (2017,2018): fidx[r["se"]].append((r,norm(r["name"])))
    id2code={}
    for r in link_old:
        toks=norm(r["name"]); 
        if not toks: continue
        best=None;bs=0
        for c,ct in fidx[r["se"]]:
            if not ct or ct[0][0]!=toks[0][0]: continue
            if toks[-1] not in ct and not any(difflib.SequenceMatcher(None,toks[-1],t).ratio()>0.88 for t in ct): continue
            sc=difflib.SequenceMatcher(None," ".join(toks)," ".join(ct)).ratio()+(0.3 if c["club"][:4].lower()==r["club"][:4].lower() else 0)
            if sc>bs: bs=sc;best=c
        if best and bs>0.6: id2code[r["pid"]]=best["pid"]
    # second pass: players who left before 2017 and came back later (Ronaldo, etc.): same birth year and the epldata name tokens sit inside the FPL name
    linked=set(id2code)
    fby={}
    for r in new:
        if r["by"]!="" and r["pid"] not in fby: fby[r["pid"]]=(r["by"],set(norm(r["name"])))
    fpl_in_old=set(id2code.values())
    for r in old:
        if r["pid"] in linked or r["by"]=="": continue
        t=set(norm(r["name"]))
        if len(t)<2: continue
        for code,(by,ft) in fby.items():
            if code in fpl_in_old: continue
            if str(by)==str(r["by"]) and t<=ft: id2code[r["pid"]]=code; fpl_in_old.add(code); break
    MANUAL_LINK={"Cristiano Ronaldo dos Santos Aveiro":"Cristiano Ronaldo"}   # FPL record has no birth year and a long legal name
    for fname,oname in MANUAL_LINK.items():
        fc=next((r["pid"] for r in new if r["name"]==fname),None); oc=next((r["pid"] for r in old if r["name"]==oname),None)
        if fc and oc: id2code[oc]=fc
    print("late links:",len(id2code)-len(linked))
    rows=new+old
    for r in old:
        if r["pid"] in id2code: r["pid"]=id2code[r["pid"]]
    # canonical names / birth year per id (latest season wins)
    canon={}; birth={}
    DISPLAY_FIX={'Cristiano Ronaldo dos Santos Aveiro':'Cristiano Ronaldo'}
    for r in sorted(rows,key=lambda r:r["se"]):
        canon[r["pid"]]=DISPLAY_FIX.get(r["name"],r["name"])
        if r["by"]!="": birth[r["pid"]]=r["by"]
    names=collections.defaultdict(set)
    for pid,n in canon.items(): names[n].add(pid)
    def label(pid):
        n=canon[pid]
        return n if len(names[n])==1 else f"{n} ({birth.get(pid) or pid})"
    # a player must be unique within a season (same id twice would double count)
    seen=set(); final=[]
    for r in sorted(rows,key=lambda r:(label(r["pid"]),r["se"])):
        k=(r["pid"],r["se"])
        if k in seen: continue
        seen.add(k); final.append(r)
    for r in final: r["name"]=label(r["pid"])
    team_context(final)
    print('rows without team context:',sum(1 for r in final if r['tz']==''))
    miss=add_honours(final)
    miss+=add_team_honours(final)
    print("unmatched awards:",miss)
    components(final)
    prev={}
    if cut is None and API_BOOT and API_FIX and LATEST_GW>1:
        prev=load_state(LATEST_GW)
    elif cut is None and LATEST_GW>1:
        prev_cut=REPO_GW if (API_BOOT and API_FIX and REPO_GW<LATEST_GW) else LATEST_GW-1
        prev=main(cut=prev_cut,write=False)
        CUT_GW=None
    if not write:
        return {(label(r["pid"]),r["se"]):(r["perf"],round(r["min"]/90,1)) for r in final if r["se"]==CURRENT and r["min"]>0}
    hdr="athlete,sport,position,competition,tier,season,games,perf,stat,birth_year,note,apps,minutes,goals,assists,xg,xa,cs,saves,yc,rc,bonus,titles,awards,honours,team_ppg,team_z,current,out,imp,cred,avail,hon,honpts,prev_perf,prev_games".split(",")
    with open("pl_players.csv","w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh); w.writerow(hdr)
        for r in final:
            if r["min"]<=0 or r["apps"]==0 and r["src"]=="epl": continue
            cur=1 if (r["pid"] in current) else 0
            pp=prev.get((label(r["pid"]),r["se"])) if r["se"]==CURRENT else None
            w.writerow([label(r["pid"]),"Football",r["pos"],"Premier League",1,r["se"],round(r["min"]/90,1),r["perf"],"",birth.get(r["pid"],""),
                f'{r["club"]}',r["apps"],int(r["min"]),r["goals"],r["assists"],r["xg"],r["xa"],r["cs"],r["saves"],r["yc"],r["rc"],r["bonus"],r["titles"],r["awards"],"; ".join(r["honours"]),r["tppg"],r["tz"],cur,r["out"],r["imp"],r["cred"],r["avail"],r["hon"],r["honpts"],
                pp[0] if pp else "",pp[1] if pp else ""])
    if API_BOOT and API_FIX: save_state(final,label)
    print(len(final),"player-seasons,",len({r['pid'] for r in final}),"players, seasons",min(r['se'] for r in final),"-",max(r['se'] for r in final),"| latest GW",LATEST_GW)
if __name__=="__main__": main()
