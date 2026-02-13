# python beammp_servers_to_csv.py

import json, csv

with open("beammp_servers.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Pick the columns you care about (the JSON has more)
cols = [
    "cversion",
    "featured",
    "guests",
    "ident",
    "ip",
    "location",
    "map",
    "maxplayers",
    "modlist",
    "modstotal",
    "modstotalsize",
    "official",
    "owner",
    "partner",
    "password",
    "players",
    "playerslist",
    "port",
    "sdesc",
    "sname",
    "tags",
    "version",
]

with open("beammp_servers.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for row in data:
        w.writerow({k: row.get(k, "") for k in cols})
