#!/usr/bin/env python3
"""Fetch DevOps / SRE job offers from official APIs and write jobs.json.

Sources:
  - France Travail "Offres d'emploi v2" (needs FT_CLIENT_ID / FT_CLIENT_SECRET)
  - Reed.co.uk Jobseeker API (needs REED_API_KEY, optional)

Only the Python standard library is used. Secrets come from environment
variables (GitHub Actions secrets) and are never written to disk or logs.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.json")
KEEP_DAYS = 14
DESC_LEN = 700
UA = "job-feed/1.0 (personal job tracker)"

REMOTE_RE = re.compile(
    r"t[ée]l[ée]travail|full[ -]?remote|100\s*%\s*(remote|t[ée]l[ée]travail)|remote|"
    r"work from home|wfh|home[- ]based|distanciel",
    re.I,
)
DEVOPS_RE = re.compile(
    r"devops|dev ops|sre\b|site reliability|kubernetes|k8s|platform engineer|"
    r"cloud engineer|ing[ée]nieur cloud|terraform|devsecops|mlops|infrastructure as code",
    re.I,
)

# --- France Travail -------------------------------------------------------
FT_TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
)
FT_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
FT_QUERIES = [
    # Local (Alpes-Maritimes: Nice, Sophia Antipolis): keep everything relevant.
    {"motsCles": "devops", "departement": "06", "publieeDepuis": "14", "_local": True},
    {"motsCles": "sre", "departement": "06", "publieeDepuis": "14", "_local": True},
    {"motsCles": "kubernetes", "departement": "06", "publieeDepuis": "14", "_local": True},
    {"motsCles": "cloud", "departement": "06", "publieeDepuis": "14", "_local": True},
    # National: keep only offers that mention remote work.
    {"motsCles": "devops", "publieeDepuis": "7", "_local": False},
    {"motsCles": "sre", "publieeDepuis": "7", "_local": False},
    {"motsCles": "kubernetes", "publieeDepuis": "7", "_local": False},
]

# --- Reed (UK) ------------------------------------------------------------
REED_SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
REED_QUERIES = [
    {"keywords": "devops", "contract": "true"},
    {"keywords": "site reliability engineer", "contract": "true"},
    {"keywords": "kubernetes", "contract": "true"},
    {"keywords": "platform engineer", "contract": "true"},
]


def http(url, data=None, headers=None, method=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return r.status, (json.loads(body) if body else None)


def clean(text, n=DESC_LEN):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n]


def ft_token(cid, secret):
    last = None
    for scope in ("api_offresdemploiv2 o2dsoffre", "api_offresdemploiv2"):
        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": cid.strip(),
                "client_secret": secret.strip(),
                "scope": scope,
            }
        ).encode()
        try:
            _, js = http(
                FT_TOKEN_URL,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            return js["access_token"]
        except urllib.error.HTTPError as e:
            # The error body holds only an OAuth error code, never the secret.
            try:
                body = json.loads(e.read() or b"{}")
                last = f"HTTP {e.code} {body.get('error', '')}: {body.get('error_description', '')}"
            except Exception:  # noqa: BLE001
                last = f"HTTP {e.code}"
    raise RuntimeError(last)


def fetch_ft(errors):
    cid, secret = os.environ.get("FT_CLIENT_ID"), os.environ.get("FT_CLIENT_SECRET")
    if not cid or not secret:
        errors.append("France Travail: secrets missing")
        return []
    try:
        token = ft_token(cid, secret)
    except Exception as e:  # noqa: BLE001
        errors.append(f"France Travail token: {e}")
        return []
    out = []
    for q in FT_QUERIES:
        local = q["_local"]
        params = {k: v for k, v in q.items() if not k.startswith("_")}
        params["range"] = "0-149"
        url = FT_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        try:
            status, js = http(url, headers={"Authorization": f"Bearer {token}"})
        except urllib.error.HTTPError as e:
            errors.append(f"France Travail {params['motsCles']}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"France Travail {params['motsCles']}: {type(e).__name__}")
            continue
        for o in (js or {}).get("resultats", []) if status in (200, 206) else []:
            title = o.get("intitule", "")
            desc = o.get("description", "")
            text = f"{title} {desc}"
            if not DEVOPS_RE.search(text):
                continue
            remote = bool(REMOTE_RE.search(text))
            if not local and not remote:
                continue
            origin = (o.get("origineOffre") or {}).get("urlOrigine") or (
                f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}"
            )
            sal = o.get("salaire") or {}
            out.append(
                {
                    "source": "France Travail",
                    "id": f"ft-{o.get('id')}",
                    "title": title,
                    "company": (o.get("entreprise") or {}).get("nom", ""),
                    "location": (o.get("lieuTravail") or {}).get("libelle", ""),
                    "contract": o.get("typeContratLibelle") or o.get("typeContrat", ""),
                    "salary": sal.get("libelle", "") or sal.get("commentaire", ""),
                    "remoteHint": remote,
                    "published": o.get("dateCreation", ""),
                    "url": origin,
                    "description": clean(desc),
                }
            )
        time.sleep(0.4)  # stay well under the API rate limit
    return out


def fetch_reed(errors):
    key = os.environ.get("REED_API_KEY")
    if not key:
        return []  # optional source
    auth = base64.b64encode(f"{key}:".encode()).decode()
    out = []
    for q in REED_QUERIES:
        params = dict(q, resultsToTake="100")
        url = REED_SEARCH_URL + "?" + urllib.parse.urlencode(params)
        try:
            _, js = http(url, headers={"Authorization": f"Basic {auth}"})
        except urllib.error.HTTPError as e:
            errors.append(f"Reed {q['keywords']}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"Reed {q['keywords']}: {type(e).__name__}")
            continue
        for o in (js or {}).get("results", []):
            title = o.get("jobTitle", "")
            desc = o.get("jobDescription", "")
            text = f"{title} {desc} {o.get('locationName', '')}"
            if not DEVOPS_RE.search(text):
                continue
            lo, hi = o.get("minimumSalary"), o.get("maximumSalary")
            cur = o.get("currency") or "GBP"
            salary = ""
            if lo or hi:
                salary = f"{lo or ''}–{hi or ''} {cur}".strip("–")
            out.append(
                {
                    "source": "Reed",
                    "id": f"reed-{o.get('jobId')}",
                    "title": title,
                    "company": o.get("employerName", ""),
                    "location": o.get("locationName", ""),
                    "contract": "Contract",
                    "salary": salary,
                    "remoteHint": bool(REMOTE_RE.search(text)),
                    "published": o.get("date", ""),
                    "url": o.get("jobUrl", ""),
                    "description": clean(desc),
                }
            )
        time.sleep(0.4)
    return out


def main():
    now = datetime.now(timezone.utc)
    errors = []
    fresh = fetch_ft(errors) + fetch_reed(errors)

    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                old = {j["id"]: j for j in json.load(f).get("jobs", [])}
        except Exception:  # noqa: BLE001
            old = {}

    jobs = {}
    for j in fresh:
        prev = old.get(j["id"])
        j["firstSeen"] = prev["firstSeen"] if prev else now.isoformat(timespec="seconds")
        jobs[j["id"]] = j
    cutoff = now - timedelta(days=KEEP_DAYS)
    for jid, j in old.items():
        if jid in jobs:
            continue
        try:
            seen = datetime.fromisoformat(j.get("firstSeen", ""))
        except ValueError:
            continue
        if seen >= cutoff:
            jobs[jid] = j

    ordered = sorted(jobs.values(), key=lambda j: j.get("firstSeen", ""), reverse=True)
    counts = {}
    for j in ordered:
        counts[j["source"]] = counts.get(j["source"], 0) + 1
    doc = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "counts": counts,
        "errors": errors,
        "jobs": ordered,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"{len(ordered)} jobs {counts}; errors: {errors or 'none'}")
    # Fail the run only if every source failed, so problems are visible in Actions.
    if errors and not fresh and not os.environ.get("ALLOW_EMPTY"):
        sys.exit(1)


if __name__ == "__main__":
    main()
