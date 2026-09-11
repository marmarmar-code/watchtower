# Nye metoder decision workbook check — 2026-09-11

Official decision page: `https://www.nyemetoder.no/innforing-av-nye-metoder/beslutning/`. The adapter reads that page on every poll and requires exactly one `.xlsx` link on `www.nyemetoder.no`, so a new meeting workbook replaces the August file without a code or configuration change.

Verified configuration: `events=["added","changed"]`, `max_records=1500`, `max_bytes=500000`, `max_unpacked_bytes=3000000`, with the official decision page as the sole URL. Two complete real polls returned 1,004 records each, zero baseline alerts, zero repeat alerts, identical full snapshots, and scope hash `2d1633aa67670647bfc2b180ec2d95b3eff69286f1080f0334c49b911716a93b`.

The downloaded workbook was 222,850 bytes and contained 1,005 data rows under the exact nine-column header. There were 993 distinct raw method IDs and 12 repeated IDs. Eleven repeated IDs had different indications and remain separate decisions through the stable identity `method ID + normalized indication`. For `2013_036`, two rows shared the same method and indication but contained distinct full decision texts; these are combined as a sorted set in one monitored record. Conflicting duplicate metadata fails closed. Outcome and decision date are never part of identity.

Decision date is decoded from the Excel serial using the workbook's declared 1900/1904 date system, validated, and stored as a substantive field. Zero represents an unavailable date. `published` is unset because this is a decision date, not a publication timestamp. Alerts are Norwegian and distinguish a newly observed national decision from a new method submission. Long indication and decision changes use bounded excerpts.

The source covers national decisions about specialist-health-service methods. It is not an EMA marketing authorisation or a general Norwegian reimbursement register. The bounded ZIP/XML reader executes no workbook code and rejects formulas, duplicate/encrypted ZIP members, unsafe sizes, excessive rows, schema changes, invalid shared strings, duplicate cells, invalid method types, identities and dates. Removed events and complete-snapshot claims are unsupported.
