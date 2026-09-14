# Annual group relationships and broadcast records

Official source: https://www.medietilsynet.no/fakta/mediedatabasen/

Two actual polls through each final recipe and engine returned identical full state and zero alerts: 258 publication/group relationships, 621 radio content records, 39 transmitter records and 16 TV content records. These are two capabilities: group/publisher relationship revisions, and broadcast-record revisions with three dataset selections. The selections are not four distinct functions.

The page explicitly states that the information concerns 2025 and was updated in May 2026. The source supplies annual data, not per-record decision, ownership-change or expiry dates. The adapter derives the reference year from that statement, not the page-level modification date or attachment filename. Each alert shows its reference year. A year change alone is quiet; moving backwards in year with the same configuration fails and preserves prior state.

Publication records use the normalized publication name as identity; publisher and listed group are monitored fields. There is no stable organization ID or percentage stake in this contract. A publication rename cannot be distinguished from a new named record. Group labels are retained as supplied and do not establish shareholder-level control.

Broadcast records use positive source numbers as identity, so station, organization, distribution and area revisions remain changes to the same record. Thirteen radio content rows share number 0 and are distinguished by station name; one transmitter row with number 0 uses organization name. These fallback rows explicitly disclose that their number is not unique. A fallback-name change cannot be distinguished from a new named row. Contact/person columns are not monitored. Missing rows are not described as withdrawals.

The download link is discovered on the official page and restricted to its expected attachment directory and filename form. The bounded XLSX reader resolves the chosen sheet by name and relationship, validates archive paths/size, shared strings, cell references, required headers and column width, and rejects formulas, conflicting identities and interior missing rows. Trailing formatting rows are excluded from record counts. The column-index helper is shared with the existing price reader; its regression checks passed.

The source has no independent row-count manifest, so a syntactically valid truncated tail cannot be ruled out by local schema checks. First observation is not proof of a newly issued decision. Hosted initialization, later scheduled repetition and actual changed-record delivery have separate evidence requirements. Retrieval and code hashes are in the JSON companion.
