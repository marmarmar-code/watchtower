# Industrial documents source check (2026-09-11)

Source: industrial_documents; official factsheet for Norske utslipp CompanyID 5447 (Yara Porsgrunn).

Suggested configuration:

    kind = "industrial_documents"
    urls = ["https://www.norskeutslipp.no/no/Diverse/Virksomhet/?CompanyID=5447&ComponentPageID=180"]
    company_id = 5447
    max_records = 100
    max_bytes = 4000000
    events = ["added", "changed"]
    complete_snapshot = false

The live factsheet returned 21 document rows. A real `fetch_with_state` plus `evaluate` poll was used twice: first poll baseline=true with 0 alerts, second poll baseline=false with 0 alerts and 21 rows. The adapter uses companyID + documentID as identity and monitors document type, year, label and official PDF URL. The permit's source `aar=0` is represented as unknown (`null`), not as year zero.

The contract is document discovery. It does not parse PDF content, infer approval dates, infer findings, or treat disappearance from this factsheet as removal. aar=0 is retained as the source document-year value for the current permit link.

Checks: `python3 -m unittest tests.test_industrial_documents` passed (5 tests). No engine, catalog, recipe or runtime files changed.
