"""Diagnostic: can GitHub's runner read the public job-board pages? Prints status, size and data hints."""
import re, urllib.request
PAGES = [
    "https://www.free-work.com/fr/tech-it/jobs?query=devops&contracts=contractor&remote=full",
    "https://www.collective.work/jobs/en",
    "https://www.freelance-informatique.fr/offres-freelance",
]
for u in PAGES:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (job-feed personal tracker)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read().decode("utf-8", "replace")
        hints = [k for k in ("__NEXT_DATA__", "application/ld+json", "JobPosting") if k in b]
        print(u, "->", r.status, len(b), "bytes", hints)
    except Exception as e:  # noqa: BLE001
        print(u, "->", type(e).__name__, str(e)[:120])
