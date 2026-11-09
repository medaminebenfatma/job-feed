"""Diagnostic: how is job data embedded in Collective's page? Prints the shape of __NEXT_DATA__."""
import json, re, urllib.request
u = "https://www.collective.work/jobs/en"
req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (job-feed personal tracker)"})
b = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', b, re.S)
d = json.loads(m.group(1))
def walk(x, path="", depth=0):
    if depth > 9: return
    if isinstance(x, dict):
        for k, v in x.items():
            if isinstance(v, (dict, list)): print("PATH", path + "/" + k, type(v).__name__, len(v))
            walk(v, f"{path}/{k}", depth + 1)
    elif isinstance(x, list):
        if x and isinstance(x[0], dict): print("ITEM0", path, json.dumps(x[0], ensure_ascii=False)[:500])
        for i, v in enumerate(x[:1]): walk(v, f"{path}[0]", depth + 1)
walk(d)
