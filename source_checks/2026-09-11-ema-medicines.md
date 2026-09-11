# EMA human-medicine workbook check — 2026-09-11

Official source page: `https://www.ema.europa.eu/en/medicines/download-medicine-data`. The verified machine-readable file is `https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx`. It is EMA's centrally maintained EU product data. It is not a Norwegian reimbursement decision or a Norwegian national authorisation decision.

The exact verified configuration was:

```toml
kind = "ema_medicines"
urls = ["https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx"]
events = ["added", "changed"]
max_records = 3000
max_bytes = 2000000
max_unpacked_bytes = 8000000
```

The 2026-09-11 workbook was 897,597 compressed bytes. Its worksheet had 2,732 product rows after the header: 2,339 `Human` and 393 `Veterinary`. All 2,339 selected human rows had unique product numbers matching the observed `EMEA/H/<type>/<six digits>` contract. Two fresh complete downloads with the configuration above returned 2,339 records each, zero first-poll alerts, zero repeat alerts, and identical full record snapshots. The configuration scope hash was `0b974ac09f1bda5859e236de421426b20fc2f3798c87ca1c330d6e61be19ec02`.

The adapter validates the exact 39-column worksheet header and accepts only the observed `Human` and `Veterinary` categories. It selects all human records and monitors medicine name, Norwegian product-status and opinion-status labels, INN/common name, active substance, therapeutic area, therapeutic indication, marketing-authorisation developer/applicant/holder, and marketing-authorisation date. The stable identity is `EMA product number` and each public detail URL must remain on EMA's human EPAR path.

`First published date` is used as the source publication date when present. `Marketing authorisation date` remains a separate substantive field. The workbook generation timestamp and `Last updated date` are excluded from the fingerprint, so an overnight regeneration alone does not create an event. New alerts say that an EMA product was newly observed; long indication changes are presented as bounded before/after excerpts rather than full raw cells.

The reader only interprets bounded ZIP and XML data; it does not run spreadsheet formulas, macros, or embedded code. It rejects formulas, encrypted or duplicate ZIP members, excessive compressed or unpacked content, excessive worksheet rows, invalid shared-string indexes, repeated cell references, changed headers or row widths, unknown categories/statuses, invalid dates and URLs, duplicate product IDs, empty human selections, and selections above `max_records`. Removed events and complete-snapshot claims are disabled.
