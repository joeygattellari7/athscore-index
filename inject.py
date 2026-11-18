"""Builds site/index.html from template.html, pl_players.csv and top5_players.csv, and writes status.json (used to flag a stalled update)."""
import os, re, json, csv, datetime
rows=list(csv.DictReader(open("pl_players.csv",encoding="utf-8")))
cur=[r for r in rows if r["season"]=="2027"]
# guard: refuse to publish a broken or half-empty Premier League build (the failed run makes GitHub email the owner)
assert len(rows)>15000, f"only {len(rows)} rows, expected 18,000+"
assert len(cur)>300, f"only {len(cur)} current-season rows"
gws=[int(m.group(1)) for fn in os.listdir("state") for m in [re.fullmatch(r"gw_(\d+)\.csv",fn)] if m]
gw=max(gws) if gws else 0
today=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

def chunk(lines):
    """Escape the text so it is safe inside a JavaScript template literal inside the page."""
    txt="\n".join(lines).strip("\n")
    txt=txt.replace("\\","\\\\").replace("`","\\`").replace("${","\\${").replace("</script","<\\/script").replace("</SCRIPT","<\\/SCRIPT")
    return txt
hdr=open("pl_players.csv",encoding="utf-8").readline().rstrip("\n")
H=hdr.split(",")
import io, unicodedata
def norm(n): return re.sub(r"[^a-z]","",unicodedata.normalize("NFKD",re.sub(r"\s*\(\d+\)$","",n)).encode("ascii","ignore").decode().lower())
legends={}
try:
    if os.path.exists("legends_players.csv"):
        for r in csv.DictReader(open("legends_players.csv",encoding="utf-8")): legends.setdefault(r["competition"],[]).append(r)
except Exception as e:
    problems_l=f"Legends: {type(e).__name__} {str(e)[:80]}"
def to_chunk(rs,name):
    """Rows for one league, plus earlier careers from the Wikidata layer for seasons before this league's own data starts."""
    extra=[]
    lg=legends.get(name,[])
    if lg and rs:
        first=min(int(r["season"]) for r in rs)
        ex={}
        for r in rs: ex.setdefault(norm(r["athlete"]),{}).setdefault(r["birth_year"] or "",r["athlete"])
        for r in lg:
            if int(r["season"])>=first: continue
            hit=ex.get(norm(r["athlete"]))
            r=dict(r)
            if hit:
                bys=[b for b in hit if b and r["birth_year"] and abs(int(b)-int(r["birth_year"]))<=1]
                if bys: r["athlete"]=hit[bys[0]]; r["birth_year"]=bys[0]
            extra.append(r)
    buf=io.StringIO(); w=csv.writer(buf,lineterminator="\n"); w.writerow(H)
    for r in rs+extra: w.writerow([r[k] for k in H])
    return chunk(buf.getvalue().splitlines()),len(extra)
data={}; info={}; problems=[]
pl_chunk,n_pl=to_chunk(rows,"Premier League")
data["Premier League"]=pl_chunk
info["Premier League"]={"round":gw,"rows":len(rows)+n_pl,"current_rows":len(cur),"earlier_rows":n_pl}
CODES={"La Liga":"esp.1","Serie A":"ita.1","Bundesliga":"ger.1","Ligue 1":"fra.1"}
if os.path.exists("top5_players.csv"):
    by={}
    for r in csv.DictReader(open("top5_players.csv",encoding="utf-8")): by.setdefault(r["competition"],[]).append(r)
    for name in ("La Liga","Serie A","Bundesliga","Ligue 1"):
      try:
        rs=by.get(name,[]); c=[r for r in rs if r["season"]=="2027"]
        if len(rs)<3000 or len(c)<150:
            problems.append(f"{name}: only {len(rs)} rows, {len(c)} current"); continue
        data[name],n_x=to_chunk(rs,name)
        rnd=0
        for fn in os.listdir("state"):
            m=re.fullmatch(r"top5_"+re.escape(CODES[name])+r"_r(\d+)\.csv",fn)
            if m: rnd=max(rnd,int(m.group(1)))
        info[name]={"round":rnd,"rows":len(rs)+n_x,"current_rows":len(c),"earlier_rows":n_x}
      except Exception as e:
        problems.append(f"{name}: {type(e).__name__} {str(e)[:80]}"); data.pop(name,None); info.pop(name,None)
else:
    problems.append("top5_players.csv missing")
if not legends: problems.append("earlier seasons (Wikidata) not built yet")
json.dump({"updated":today,"latest_gw":gw,"rows":len(rows),"current_rows":len(cur),"leagues":info,"problems":problems},open("status.json","w"))
os.makedirs("site",exist_ok=True)
lines=" | ".join(f"{k}: round {v['round']}" for k,v in info.items() if k!="Legends")
asof=f"Data through {lines}. Updated {today} (UTC). Rebuilt automatically every few hours."+(" Not available right now: "+"; ".join(problems) if problems else "")
leaguejs="{"+",".join(json.dumps(k)+":`"+v+"`" for k,v in data.items())+"}"
html=open("template.html",encoding="utf-8").read().replace("__LEAGUE_DATA__",leaguejs).replace("__LEAGUE_INFO__",json.dumps(info)).replace("__ASOF__",asof)
open("site/index.html","w",encoding="utf-8").write(html)
print("site/index.html",len(html),asof)
