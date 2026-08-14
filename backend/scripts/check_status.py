"""Quick snapshot count check."""
import urllib.request
import json

r = urllib.request.urlopen('http://localhost:8101/api/research/diagnostics', timeout=5)
d = json.loads(r.read())
print(f"Snapshots: {d['snapshot_count']} | Events: {d['event_count']}")
print(f"Version: {d['code_version']} | Integrity: {d['integrity_check']}")
print(f"Latest TS: {d['latest_snapshot_ts']}")
