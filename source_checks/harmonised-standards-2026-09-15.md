# Harmonised standard-reference changes

The [Commission's medical-device page](https://single-market-economy.ec.europa.eu/single-market/goods/european-standards/harmonised-standards/medical-devices_en) supplies an informational summary under Regulation 2017/745. It contains both current and withdrawn reference sets; the summary itself has no legal effect. The function follows reference sets and their published dates, not individual product approval or compliance.

The page's spreadsheet link redirects to the Commission's public document store. Both hosts and the observed download path shapes are validated explicitly. The downloaded filename mentions October 2025, but the actual workbook says it was generated on 17 June 2026. Filename dates are not used as freshness evidence.

The observed worksheet has 71 entries, including six with an explicit end-of-effect date. The same base standard may appear with different amendment sets. Identity uses the complete set of EN references, while the source title, standards body, publication references and effect/withdrawal dates remain change fields. Missing dates remain absent. No inferred product prohibition or regulatory outcome is added.

The existing bounded XLSX reader validates archive size, unique parts, worksheet relationships, cell identities, text sizes and formulas. The adapter checks exact headers, legislation, reference syntax, duplicate reference sets, generation-date regression and two complete matching reads before persistence. Exact reference selection is optional. The full source provision remains available in state; initial observations are quiet, and absence does not prove withdrawal.

Adjacent JSON records the final real adapter reads and code/configuration hashes. Raw workbooks and pages are kept outside this repository. Local source checks, synthetic failure tests, isolated runtime initialization and later delivery are separate evidence levels. Standard texts and product-specific conformity have not been assessed.
