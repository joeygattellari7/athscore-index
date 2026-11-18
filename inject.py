"""Builds site/index.html from template.html and pl_players.csv"""
import os
csv=open("pl_players.csv",encoding="utf-8").read().strip("\n")
assert "`" not in csv and "${" not in csv
os.makedirs("site",exist_ok=True)
html=open("template.html",encoding="utf-8").read().replace("__PLAYERS_CSV__",csv)
open("site/index.html","w",encoding="utf-8").write(html)
print("site/index.html",len(html))
