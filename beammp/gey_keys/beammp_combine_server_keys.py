# This script reads the beammp_servers.json file, extracts all unique keys from the server objects, and writes them to a text file called beammp_keys.txt, one key per line.
"""
python C:\\Users\\Administrator\\projects\\BeamNG\\beammp\\beammp_combine_server_keys.py
"""

import json

with open("C:\\Users\\Administrator\\projects\\BeamNG\\beammp\\beammp_servers.json", "r", encoding="utf-8") as f:
    data = json.load(f)

keys = set()

for server in data:
    keys.update(server.keys())

sorted_keys = sorted(keys)

with open("C:\\Users\\Administrator\\projects\\BeamNG\\beammp\\beammp_keys.txt", "w", encoding="utf-8") as f:
    for key in sorted_keys:
        f.write(key + "\n")

print(f"Wrote {len(sorted_keys)} keys to beammp_keys.txt")
