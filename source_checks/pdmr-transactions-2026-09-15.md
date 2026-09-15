# Transaction forms and corrections

The public NewsWeb client resolves its API host through `/urls.json`. Category 1102 is checked through the public category POST endpoint. A bounded date query returned 64 messages with `overflow=false`; the five explicit issuer IDs selected 18 messages and 20 PDF attachments.

The verified MAR and Norwegian KRT1500 decoders read 14 forms containing 15 transaction sections. Three attachments are copies of announcements; three are scanned forms with no usable digital text. Two original messages have no attachments and link forward to corrections. These five messages remain visible with limited coverage. No OCR values or missing prices are invented.

Price/volume, aggregate information, lending, redelivery, zero price and transaction date/time remain literal source fields. MAR aggregate label/value columns remain separate from the price/volume table. KRT actor and related insider are separate from the reporter. A delayed submission retains its earlier transaction date and explanatory comment. Different source-provided LEIs are preserved. Form amendments can exist without API correction links.

Every PDF was decoded from actual bytes; representative layouts, the scanned form and multi-page transaction form were also reviewed visually. Two final live polls made 122 HTTP reads, returned 18 message records and identical complete state, with zero alerts on both polls. The index and attachment manifests were checked twice within each poll; PDF bytes were fetched once per poll. Sixty targeted tests cover actual PDF decoding, column boundaries, zero values, multiple pages, correction links, state preservation, limits and coverage warnings. Generic synthetic fixtures are not presented as real-data evidence.

Unsupported or scanned attachments receive an explicit warning. A previously parsed attachment that disappears or becomes unreadable rejects the update and preserves prior state. Unknown structure inside a recognized form also fails. Absence from the rolling query is not a cancellation. Later schedule and delivery require separate runtime evidence.

Machine-readable proof: [pdmr-transactions-2026-09-15.json](pdmr-transactions-2026-09-15.json).
