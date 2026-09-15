# Nkom E.164 number-range changes

Official page: https://nkom.no/telefoni-og-telefonnummer/telefonnummer-og-den-norske-nummerplan/alle-nummerserier-for-norske-telefonnumre/den-norske-nummerplanen-for-telefoni-med-mer-e.164

The page links to the exact public E164.csv export. Two final-recipe engine polls read the page and export, returning 2163 rows, zero alerts and identical full state. The page identifies the list as updated 2026-07-13. Both CSV responses were 174712 bytes with identical SHA-256; see the JSON companion for all retrieval hashes.

The eight CSV columns are Fra, Til, Tilbyder, Status, Kommentar, Antall, Kategori and Punktkode. All 2163 inclusive interval quantities match their endpoints, with no duplicate or overlapping ranges of the same length. There are 2097 eight-digit ranges and 66 twelve-digit ranges. The statuses observed are 1239 Tildelt, 881 Ledig and 43 Blokkert. Blank holders occur in available ranges; comment and point-code fields are currently blank. Synthetic checks cover future nonblank field changes, and are not proof of an actual later change.

The adapter uses normalized start/end strings as identity and preserves leading zeros. Holder, status, quantity, category, comment and point code are monitored fields. Category filtering is optional; without it the complete retrieved file is selected. All raw rows undergo identity, arithmetic, overlap and schema checks before selection. Missing selected categories fail closed. Display formatting, row ordering and CSV column ordering do not cause changes.

Every row on the visible HTML page must match its CSV counterpart. This is a partial cross-check: the HTML is paginated and supplies no independent full export count. A syntactically valid missing range outside that page cannot be ruled out by these checks. Absence is therefore never reported as a withdrawal. Range splits and merges produce newly observed ranges, not an inferred allocation decision. There is no per-row decision date, and the range holder does not establish the current provider of a ported individual number. The observed contract covers eight- and twelve-digit ranges; shorter service numbers are not covered by this adapter.

One daily recipe covers the retrieved register within the default history capacity. This is one function, distinct from news discovery. Hosted execution and later actual delivery require separate evidence.
