"""Check diagnostics endpoint."""
import urllib.request
import json

r = urllib.request.urlopen('http://localhost:8101/api/research/diagnostics', timeout=5)
d = json.loads(r.read())
print(json.dumps(d, indent=2))
