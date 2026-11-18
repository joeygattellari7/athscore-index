# AthScore Index

Premier League player rating, rebuilt automatically every few hours from the official Fantasy Premier League feed plus 1992-93 history.

- build_pl_index.py makes pl_players.csv
- inject.py puts it into template.html to make site/index.html
- .github/workflows/update.yml runs both and deploys to GitHub Pages
- state/ holds one snapshot per gameweek so the page can show who moved since the last round

Data: community-scraped history (pssguy/epldata, vaastav/Fantasy-Premier-League) and the public FPL API. Check their terms before commercial use.
