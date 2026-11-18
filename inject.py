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
    txt="\n".join(lines).strip("\n"); assert "`" not in txt and "${" not in txt and "</script" not in txt.lower(); return txt
hdr=open("pl_players.csv",encoding="utf-8").readline().rstrip("\n")
data={"Premier League":chunk(open("pl_players.csv",encoding="utf-8").read().splitlines())}
info={"Premier League":{"round":gw,"rows":len(rows),"current_rows":len(cur)}}
problems=[]
if os.path.exists("top5_players.csv"):
    by={}
    for r in csv.DictReader(open("top5_players.csv",encoding="utf-8")): by.setdefault(r["competition"],[]).append(r)
    raw=open("top5_players.csv",encoding="utf-8").read().splitlines()[1:]
    import io
    for name in ("La Liga","Serie A","Bundesliga","Ligue 1"):
        rs=by.get(name,[]); c=[r for r in rs if r["season"]=="2027"]
        if len(rs)<3000 or len(c)<150:
            problems.append(f"{name}: only {len(rs)} rows, {len(c)} current"); continue
        buf=io.StringIO(); w=csv.writer(buf,lineterminator="\n"); w.writerow(hdr.split(","))
        for r in rs: w.writerow([r[k] for k in hdr.split(",")])
        data[name]=chunk(buf.getvalue().splitlines())
        rnd=0
        for fn in os.listdir("state"):
            m=re.fullmatch(r"top5_"+name.replace(" ","_").replace("La_Liga","La_liga")+r"_r(\d+)\.csv",fn)
            if m: rnd=max(rnd,int(m.group(1)))
        info[name]={"round":rnd,"rows":len(rs),"current_rows":len(c)}
else:
    problems.append("top5_players.csv missing")
json.dump({"updated":today,"latest_gw":gw,"rows":len(rows),"current_rows":len(cur),"leagues":info,"problems":problems},open("status.json","w"))
os.makedirs("site",exist_ok=True)
lines=" | ".join(f"{k}: round {v['round']}" for k,v in info.items())
asof=f"Data through {lines}. Updated {today} (UTC). Rebuilt automatically every few hours."+(" Not available right now: "+"; ".join(problems) if problems else "")
leaguejs="{"+",".join(json.dumps(k)+":`"+v+"`" for k,v in data.items())+"}"
html=open("template.html",encoding="utf-8").read().replace("__LEAGUE_DATA__",leaguejs).replace("__LEAGUE_INFO__",json.dumps(info)).replace("__ASOF__",asof)
open("site/index.html","w",encoding="utf-8").write(html)
print("site/index.html",len(html),asof)
