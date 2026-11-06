# job-feed

Personal DevOps / SRE job feed. A GitHub Action runs every 4 hours, calls the
official **France Travail "Offres d'emploi v2"** and **Reed.co.uk** APIs, and
writes the matching offers to [`jobs.json`](jobs.json) (kept 14 days).

- API keys live in repository secrets (`FT_CLIENT_ID`, `FT_CLIENT_SECRET`,
  optional `REED_API_KEY`) and are never committed.
- Run it now: Actions → **Fetch jobs** → Run workflow.
- Edit the searches in `FT_QUERIES` / `REED_QUERIES` in `fetch_jobs.py`.
