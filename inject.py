"""Builds site/index.html from template.html and pl_players.csv, and writes status.json (used to flag a stalled update)."""
import os, re, json, csv, datetime
rows=list(csv.DictReader(open("pl_players.csv",encoding="utf-8")))
cur=[r for r in rows if r["season"]=="2027"]
# guard: refuse to publish a broken or half-empty build (the failed run makes GitHub email the owner)
assert len(rows)>15000, f"only {len(rows)} rows, expected 18,000+"
assert len(cur)>300, f"only {len(cur)} current-season rows"
gws=[int(m.group(1)) for fn in os.listdir("state") for m in [re.fullmatch(r"gw_(\d+)\.csv",fn)] if m]
gw=max(gws) if gws else 0
today=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
json.dump({"updated":today,"latest_gw":gw,"rows":len(rows),"current_rows":len(cur)},open("status.json","w"))
csvtxt=open("pl_players.csv",encoding="utf-8").read().strip("\n")
assert "`" not in csvtxt and "${" not in csvtxt
os.makedirs("site",exist_ok=True)
asof=f"Data through gameweek {gw}. Updated {today} (UTC). Rebuilt automatically every few hours."
html=open("template.html",encoding="utf-8").read().replace("__PLAYERS_CSV__",csvtxt).replace("__ASOF__",asof)
open("site/index.html","w",encoding="utf-8").write(html)
print("site/index.html",len(html),asof)
