"""Diagnostic: how is job data embedded in Collective's page? Prints the shape of __NEXT_DATA__."""
import json, re, urllib.request
u = "https://www.collective.work/jobs/en"
req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (job-feed personal tracker)"})
b = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', b, re.S)
d = json.loads(m.group(1))
def walk(x, path="", depth=0):
    if depth > 7: return
    if isinstance(x, list) and x and isinstance(x[0], dict) and len(x) > 3:
        print("LIST", path, len(x), sorted(x[0].keys())[:40])
        print("  SAMPLE", json.dumps(x[0], ensure_ascii=False)[:900])
    if isinstance(x, dict):
        for k, v in x.items(): walk(v, f"{path}/{k}", depth + 1)
    elif isinstance(x, list):
        for i, v in enumerate(x[:2]): walk(v, f"{path}[{i}]", depth + 1)
walk(d)
