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
    r"cloud engineer|ing[ée]nieur cloud|cloud (architect|ops|infra)|terraform|aws|azure|gcp|"
    r"devsecops|mlops|infrastructure as code|ing[ée]nieur (infra|syst[eè]me)|platform|infrastructure engineer",
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


# --- Free remote-job feeds (no key) ----------------------------------------
# Moved here from the scanner so it needs no web access for them.
FREE_FEEDS = [
    ("Jobicy", "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops&geo=europe"),
    ("Jobicy", "https://jobicy.com/api/v2/remote-jobs?count=50&tag=sre"),
    ("RemoteOK", "https://remoteok.com/api?tag=devops"),
    ("Working Nomads", "https://www.workingnomads.com/api/exposed_jobs/"),
    ("We Work Remotely", "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"),
]


def _get_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/rss+xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _free_job(source, jid, title, company, location, contract, salary, published, url, desc, tags=""):
    return {
        "source": source,
        "id": f"{source.lower().replace(' ', '')}-{jid}",
        "title": title or "",
        "company": company or "",
        "location": location or "Remote",
        "contract": contract or "",
        "salary": salary or "",
        "remoteHint": True,
        "published": published or "",
        "url": url or "",
        "description": clean(desc),
        "country": "",
        "_tags": tags,
    }


def fetch_free_feeds(errors):
    out = []
    for source, url in FREE_FEEDS:
        try:
            raw = _get_text(url)
        except urllib.error.HTTPError as e:
            errors.append(f"{source}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source}: {type(e).__name__}")
            continue
        jobs = []
        try:
            if url.endswith(".rss"):
                import xml.etree.ElementTree as ET

                for it in ET.fromstring(raw).iter("item"):
                    t = (it.findtext("title") or "").strip()
                    company, _, title = t.partition(": ")
                    if not title:
                        company, title = "", t
                    link = (it.findtext("link") or "").strip()
                    jobs.append(_free_job(source, link.rstrip("/").split("/")[-1], title, company,
                                          it.findtext("region") or "Remote", "", "",
                                          it.findtext("pubDate") or "", link, it.findtext("description") or "",
                                          it.findtext("category") or ""))
            else:
                js = json.loads(raw)
                if source == "Jobicy":
                    for o in (js or {}).get("jobs", []):
                        sal = ""
                        if o.get("annualSalaryMin") or o.get("annualSalaryMax"):
                            sal = f"{o.get('annualSalaryMin', '')}–{o.get('annualSalaryMax', '')} {o.get('salaryCurrency', '')}"
                        jt = o.get("jobType")
                        jobs.append(_free_job(source, o.get("id"), o.get("jobTitle"), o.get("companyName"),
                                              o.get("jobGeo"), ", ".join(jt) if isinstance(jt, list) else jt, sal,
                                              o.get("pubDate"), o.get("url"),
                                              o.get("jobExcerpt") or o.get("jobDescription"),
                                              " ".join(o.get("jobIndustry") or []) if isinstance(o.get("jobIndustry"), list) else ""))
                elif source == "RemoteOK":
                    for o in js if isinstance(js, list) else []:
                        if not isinstance(o, dict) or not o.get("position"):
                            continue
                        sal = f"{o.get('salary_min')}–{o.get('salary_max')} USD" if o.get("salary_min") else ""
                        jobs.append(_free_job(source, o.get("id"), o.get("position"), o.get("company"),
                                              o.get("location"), "", sal, o.get("date"), o.get("url"),
                                              o.get("description"), " ".join(o.get("tags") or [])))
                else:  # Working Nomads
                    for o in js if isinstance(js, list) else []:
                        tags = o.get("tags") or ""
                        jobs.append(_free_job(source, (o.get("url") or "").rstrip("/").split("/")[-1], o.get("title"),
                                              o.get("company_name"), o.get("location"), "", "",
                                              o.get("pub_date"), o.get("url"), o.get("description"),
                                              (tags if isinstance(tags, str) else " ".join(tags)) + " " + str(o.get("category_name") or "")))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source}: parse {type(e).__name__}")
            continue
        n = 0
        for j in jobs:
            if not DEVOPS_RE.search(f"{j['title']} {j['_tags']}"):
                j.pop("_tags", None)
                continue
            j.pop("_tags", None)
            out.append(j)
            n += 1
        time.sleep(0.5)
    return out


# --- Public job-board pages (Free-Work, Freelance-Informatique, Collective) -
# Read from GitHub's runner so the scanner needs no web access. Only the public listing pages;
# no login, no forms. Data comes from the structured JSON the pages already embed.
BOARD_PAGES = [
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=devops&contracts=contractor&remote=full"),
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=sre&contracts=contractor&remote=full"),
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=kubernetes&contracts=contractor&remote=full"),
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=cloud%20devops&contracts=contractor&remote=full"),
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=devops%20sophia%20antipolis&contracts=contractor"),
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=devops%20nice&contracts=contractor"),
    ("Free-Work", "https://www.free-work.com/fr/tech-it/jobs?query=devops&contracts=permanent&remote=full"),
    ("Collective", "https://www.collective.work/jobs/en"),
    ("Collective", "https://www.collective.work/jobs/en?page=2"),
    ("Collective", "https://www.collective.work/jobs/en?page=3"),
    ("Freelance-Informatique", "https://www.freelance-informatique.fr/offres-freelance"),
    ("Freelance-Informatique", "https://www.freelance-informatique.fr/offres-freelance?page=2"),
    ("Freelance-Informatique", "https://www.freelance-informatique.fr/offres-freelance?page=3"),
]
BROWSER_UA = "Mozilla/5.0 (compatible; job-feed personal tracker)"


def _walk(x, found, kind):
    if isinstance(x, dict):
        t = x.get("@type")
        if t == kind or (isinstance(t, list) and kind in t):
            found.append(x)
        for v in x.values():
            _walk(v, found, kind)
    elif isinstance(x, list):
        for v in x:
            _walk(v, found, kind)


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return ""


def _jsonld_jobs(source, html_text):
    posts = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.S):
        try:
            _walk(json.loads(m.group(1)), posts, "JobPosting")
        except ValueError:
            continue
    out = []
    for o in posts:
        org = o.get("hiringOrganization")
        company = org.get("name", "") if isinstance(org, dict) else (org or "")
        loc = o.get("jobLocation")
        if isinstance(loc, list) and loc:
            loc = loc[0]
        addr = loc.get("address", {}) if isinstance(loc, dict) else {}
        place = ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x) if isinstance(addr, dict) else ""
        remote_type = str(o.get("jobLocationType", ""))
        sal = ""
        bs = o.get("baseSalary")
        if isinstance(bs, dict):
            v = bs.get("value")
            if isinstance(v, dict):
                lo, hi, unit = v.get("minValue"), v.get("maxValue"), v.get("unitText", "")
                if lo or hi:
                    sal = f"{lo or ''}–{hi or ''} {bs.get('currency', '')} / {unit}".strip()
                elif v.get("value"):
                    sal = f"{v.get('value')} {bs.get('currency', '')} / {unit}".strip()
            elif v:
                sal = f"{v} {bs.get('currency', '')}".strip()
        url = o.get("url") or o.get("@id") or ""
        et = o.get("employmentType")
        et = ", ".join(et) if isinstance(et, list) else (et or "")
        text = f"{o.get('title', '')} {o.get('description', '')} {place}"
        out.append(
            {
                "source": source,
                "id": f"{source.lower()}-{url.rstrip('/').split('/')[-1] or o.get('identifier', '')}",
                "title": o.get("title", ""),
                "company": company,
                "location": place or ("Remote" if "TELECOMMUTE" in remote_type else ""),
                "contract": et,
                "salary": sal,
                "remoteHint": "TELECOMMUTE" in remote_type or bool(REMOTE_RE.search(text)),
                "published": o.get("datePosted", ""),
                "url": url,
                "description": clean(o.get("description", "")),
                "country": "FR",
            }
        )
    return out


def _collective_jobs(html_text):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    projects = []
    for q in data["props"]["pageProps"]["dehydratedState"]["queries"]:
        res = ((q.get("state") or {}).get("data") or {}).get("results") or {}
        projects += res.get("projects", [])
    if projects and not os.environ.get("QUIET_KEYS"):
        print("Collective project keys:", sorted(projects[0].keys()))
    out = []
    for o in projects:
        slug = o.get("slug") or o.get("id")
        rate = _first(o, "dailyRate", "tjm", "averageDailyRate", "rate", "budget", "price")
        if isinstance(rate, dict):
            rate = " ".join(str(v) for v in rate.values() if v)
        loc = _first(o, "location", "city", "locations")
        if isinstance(loc, (list, dict)):
            loc = json.dumps(loc, ensure_ascii=False)[:120]
        remote = _first(o, "remote", "remotePolicy", "workMode", "teleworking")
        desc = o.get("description") or o.get("sumUp") or ""
        out.append(
            {
                "source": "Collective",
                "id": f"collective-{o.get('id') or slug}",
                "title": o.get("name") or o.get("sumUp") or "",
                "company": "",
                "location": str(loc),
                "contract": "Freelance",
                "salary": str(rate) if rate else "",
                "remoteHint": bool(remote) or bool(REMOTE_RE.search(desc)),
                "published": _first(o, "publishedAt", "createdAt", "startDate"),
                "url": f"https://www.collective.work/job/{slug}",
                "description": clean(desc),
                "country": "",
            }
        )
    return out


def fetch_pages(errors):
    out = []
    for source, url in BOARD_PAGES:
        try:
            page = _get_text_ua(url)
            jobs = _collective_jobs(page) if source == "Collective" else _jsonld_jobs(source, page)
        except urllib.error.HTTPError as e:
            errors.append(f"{source} page: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source} page: {type(e).__name__}")
            continue
        out += [j for j in jobs if DEVOPS_RE.search(f"{j['title']} {j['description']}")]
        time.sleep(1)
    return out


def _get_text_ua(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# --- JobServe / CareerBoard via Apify (paid per result, optional) ----------
# Actor: https://apify.com/fetchclub/jobserve-jobs-scraper (~$3 per 1,000 jobs).
# Runs once a day with small caps so it stays inside Apify's free monthly credit.
APIFY_ACTOR = "fetchclub~jobserve-jobs-scraper"
APIFY_EVERY_HOURS = 23
APIFY_MAX_USD_PER_SEARCH = 0.08
APIFY_SEARCHES = [
    {"website": "JobServe", "website_country": "United Kingdom", "keywords": "DevOps",
     "job_type": "Contract", "remote_working": True, "date_posted": "Within 1 day", "max_results": 20},
    {"website": "JobServe", "website_country": "United Kingdom", "keywords": "Kubernetes OR SRE",
     "job_type": "Contract", "remote_working": True, "date_posted": "Within 1 day", "max_results": 10},
    {"website": "JobServe", "website_country": "United States of America", "keywords": "DevOps",
     "job_type": "Contract", "remote_working": True, "date_posted": "Within 1 day", "max_results": 10},
    {"website": "CareerBoard", "website_country": "United Kingdom", "keywords": "DevOps",
     "job_type": "Contract", "remote_working": True, "date_posted": "Within 1 day", "max_results": 10},
]


def fetch_apify(errors):
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return []
    out = []
    for s in APIFY_SEARCHES:
        url = (
            f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
            f"?maxTotalChargeUsd={APIFY_MAX_USD_PER_SEARCH}&timeout=240"
        )
        label = f"{s['website']} {s['website_country']} {s['keywords']}"
        try:
            _, items = http(
                url,
                data=json.dumps(s).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST",
                timeout=290,
            )
        except urllib.error.HTTPError as e:
            try:
                msg = (json.loads(e.read() or b"{}").get("error") or {}).get("message", "")
            except Exception:  # noqa: BLE001
                msg = ""
            errors.append(f"Apify {label}: HTTP {e.code} {msg[:200]}")
            if e.code in (401, 403):
                break  # same auth problem for every search; don't repeat it
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"Apify {label}: {type(e).__name__}")
            continue
        for o in items or []:
            title = o.get("title", "")
            desc = o.get("description_text", "")
            text = f"{title} {desc}"
            if not DEVOPS_RE.search(text):
                continue
            ref = o.get("job_reference") or o.get("job_link") or title
            out.append(
                {
                    "source": s["website"],
                    "id": f"{s['website'].lower()}-{re.sub(r'[^A-Za-z0-9]+', '', str(ref))[:60]}",
                    "title": title,
                    "company": o.get("company_name") or o.get("posted_by") or "",
                    "location": o.get("location", "") or s["website_country"],
                    "contract": o.get("employment_type", "") or "Contract",
                    "salary": o.get("salary", "") or o.get("salary_detail", ""),
                    "remoteHint": True if s.get("remote_working") else bool(REMOTE_RE.search(text)),
                    "published": o.get("posted_at") or o.get("posted_date", ""),
                    "url": o.get("job_link", ""),
                    "duration": o.get("duration", ""),
                    "description": clean(desc),
                }
            )
    return out


# --- Indeed via the hosted omkar.cloud Indeed Scraper API (optional) --------
# Needs OMKAR_API_KEY and OMKAR_INDEED_URL (the full ".../jobs/search" address
# shown in the API playground). Runs at most once a day, like Apify.
INDEED_SEARCHES = [
    # France (14 days back)
    {"query": "devops", "country": "FR", "location": "remote", "date_posted": "last_14_days"},
    {"query": "devops", "country": "FR", "location": "Nice", "date_posted": "last_14_days"},
    {"query": "devops", "country": "FR", "location": "Monaco", "date_posted": "last_14_days"},
    {"query": "sre kubernetes", "country": "FR", "location": "remote", "date_posted": "last_14_days"},
    {"query": "terraform", "country": "FR", "location": "remote", "date_posted": "last_14_days"},
    {"query": "aws devops", "country": "FR", "location": "remote", "date_posted": "last_14_days"},
    {"query": "cloud engineer", "country": "FR", "location": "remote", "date_posted": "last_14_days"},
    {"query": "cloud engineer", "country": "FR", "location": "Nice", "date_posted": "last_14_days"},
    # Abroad: remote contracts
    {"query": "devops", "country": "GB", "work_setting": "remote", "job_type": "contract", "date_posted": "last_14_days"},
    {"query": "terraform", "country": "GB", "work_setting": "remote", "job_type": "contract", "date_posted": "last_14_days"},
    {"query": "devops", "country": "CA", "work_setting": "remote", "job_type": "contract", "date_posted": "last_14_days"},
    {"query": "devops", "country": "US", "work_setting": "remote", "job_type": "contract", "date_posted": "last_14_days"},
    {"query": "sre kubernetes", "country": "US", "work_setting": "remote", "job_type": "contract", "date_posted": "last_14_days"},
]


def fetch_indeed(errors):
    key = os.environ.get("OMKAR_API_KEY")
    base = os.environ.get("OMKAR_INDEED_URL") or "https://indeed-scraper.omkar.cloud/jobs/search"
    if not key:
        return []
    out = []
    for q in INDEED_SEARCHES:
        url = base + ("&" if "?" in base else "?") + urllib.parse.urlencode(q)
        try:
            _, js = http(url, headers={"API-Key": key})
        except urllib.error.HTTPError as e:
            errors.append(f"Indeed {q['query']} {q['country']}: HTTP {e.code}")
            if e.code in (401, 403, 429):
                break
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"Indeed {q['query']}: {type(e).__name__}")
            continue
        for o in (js or {}).get("jobs", []):
            title = o.get("title", "")
            text = title
            if not DEVOPS_RE.search(text):
                continue
            sal = o.get("salary") or {}
            loc = o.get("location") or {}
            out.append({
                "source": "Indeed",
                "id": f"indeed-{o.get('id')}",
                "title": title,
                "company": (o.get("company") or {}).get("name", ""),
                "location": loc.get("display_name", "") if isinstance(loc, dict) else str(loc),
                "contract": q.get("job_type", ""),
                "salary": sal.get("text", "") if isinstance(sal, dict) else "",
                "remoteHint": q.get("location") == "remote" or q.get("work_setting") == "remote",
                "published": o.get("date_published", ""),
                "url": o.get("link", ""),
                "description": "",
                "country": q["country"],
            })
        time.sleep(0.5)
    return out


def main():
    now = datetime.now(timezone.utc)
    errors = []
    old_doc = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                old_doc = json.load(f)
        except Exception:  # noqa: BLE001
            old_doc = {}
    old = {j["id"]: j for j in old_doc.get("jobs", [])}

    fresh = fetch_ft(errors) + fetch_reed(errors) + fetch_free_feeds(errors) + fetch_pages(errors)

    # Apify (paid per result): run at most once every APIFY_EVERY_HOURS.
    apify_at = old_doc.get("apifyAt", "")
    due = True
    if apify_at:
        try:
            due = now - datetime.fromisoformat(apify_at) >= timedelta(hours=APIFY_EVERY_HOURS)
        except ValueError:
            due = True
    if os.environ.get("FORCE_APIFY"):
        due = True
    if due and (os.environ.get("APIFY_TOKEN") or os.environ.get("OMKAR_API_KEY")):
        fresh += fetch_apify(errors)
        fresh += fetch_indeed(errors)
        apify_at = now.isoformat(timespec="seconds")

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
        "apifyAt": apify_at,
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
