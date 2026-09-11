# BRREG company discovery — 2026-09-11

Official public endpoint: `https://data.brreg.no/enhetsregisteret/api/enheter`. The adapter discovers entities by a rolling `fraRegistreringsdatoEnhetsregisteret` window and selected two-digit SN2025 industry divisions, then explicitly keeps only configured `AS` and/or `ASA` legal forms. The source date is registration in the Enhetsregisteret; it is not described as the company's foundation date.

The exact verified configuration was:

```toml
kind = "company_discovery"
industry_prefixes = ["58"]
organisation_forms = ["AS", "ASA"]
lookback_days = 30
page_size = 100
max_pages = 5
max_records = 500
max_bytes = 1000000
allow_empty = true
events = ["added", "changed"]
```

On 2026-09-11 this queried registrations from 2026-08-12. BRREG reported 19 total entities on one complete page. Their legal forms were five AS, five ENK, four FLI, three KBO, one DA and one NUF; no ASA was present. The adapter selected the five AS records and rejected any attempt to configure unrelated forms. Two final complete real reads returned five selected companies each, zero first-baseline alerts, zero repeat alerts, and identical full snapshots. The scope hash was `75c21a8340684a6a73716c0729a9ec46af62fe1cc8b8e4d94216353285f3f8c9`; the canonical snapshot SHA-256 was `1803ee75cb38695b8b1cad1007152a1b6a6cb1ffbfc441f7c2404e0ec0f47c9f`.

Stable identity is the validated organisation number. Monitored fields are name, legal form, selected matching industry codes and descriptions, Enhetsregisteret registration date, and business municipality. `published` is unset. BRREG exposes up to three industry-code fields. The adapter validates all present industry fields and requires the code requested for that page to match at least one of them before applying the AS/ASA form filter. The live division-58 sample used primary codes only, so the evidence does not claim that a secondary-code match was observed. If one entity is returned for two selected prefixes, an identical entity is kept once; conflicting versions fail closed.

SN2025 code `58.130` in the observed response is `Utgivelse av blader og tidsskrifter`. This evidence does not treat it as an older newspaper-industry code. Division `58` also includes books, software publishing and other publishing activities, so the proposed recipe is a division-wide discovery monitor rather than a newspaper-only monitor.

Page size, number, total elements, total pages and exact final page length are required. Totals must remain stable across pages. A separate live query for division `05` from 2026-09-10 returned the verified zero-result shape: BRREG omitted `_embedded` and supplied `page.totalElements = 0` and `page.totalPages = 0`. The adapter accepts omitted `_embedded` only for that internally consistent zero shape. It rejects malformed schemas, invalid organisation numbers, repeated entities within one query, incomplete pages, results outside the date/industry scope, and selections beyond page or record bounds. Removed events and complete-snapshot claims are unsupported because companies age out of the rolling window.
