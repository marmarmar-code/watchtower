# ClinicalTrials.gov Norway update-window check — 2026-09-11

Official public endpoint: `https://clinicaltrials.gov/api/v2/studies`. The adapter uses ClinicalTrials.gov API v2 with an exact Norway location expression and a rolling `LastUpdatePostDate` range. It requests only the fields needed for identity, Norwegian location validation, title, recruitment status, all study phases, lead sponsor, first-posted date, last-update date, and result availability.

The exact verified configuration was:

```toml
kind = "clinical_trials"
urls = ["https://clinicaltrials.gov/api/v2/studies"]
events = ["added", "changed"]
last_update_days = 14
page_size = 100
max_pages = 5
max_records = 500
max_bytes = 1000000
allow_empty = true
```

On 2026-09-11 the query covered the inclusive date range 2026-08-28 through 2026-09-11. `countTotal=true` reported 135 studies. The API returned 100 records and a page token, followed by 35 records and no further token. ClinicalTrials.gov supplies `totalCount` on the first page and omits it on token pages; the adapter requires it on page one, calculates the required page count before continuing, checks every page length against that total, and rejects a changed total if a later page supplies one.

Two complete real polls with the configuration above returned 135 records each, zero first-poll alerts, zero repeat alerts, and identical full record snapshots. The verified configuration scope hash was `9989c58f710fb9a099118c3b9d1b2a9978ed48c6cc31cd1b4878945233860516`.

The stable identity is the validated NCT ID. Monitored content is the title, Norwegian recruitment-status label, every reported phase, lead sponsor, and whether results are available. `LastUpdatePostDate` controls the query and is validated against the requested window, but is excluded from the content fingerprint. `StudyFirstPostDate` remains the public first-registration date in `published`; a newly discovered record is described as a newly observed study with a Norwegian location, not as a study first registered today.

Page tokens are URL-encoded, repeated tokens and NCT IDs are rejected, and the response, page, record, date, enum, type, location, and byte limits fail closed. The rolling window supports discovery and substantive changes but cannot establish deletion; removed events and complete snapshots are rejected. An empty valid window is accepted. This is a bounded recent-update monitor, not a complete historical register.
