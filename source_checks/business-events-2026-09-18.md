# Bounded business-event sources, 18 September 2026

Six distinct capabilities were checked against their official public sources:

- Registered capital increases and correction notices: resulting registered nominal capital, not financing proceeds.
- Court reconstruction openings: separate ruling, claim-deadline and statutory-date fields.
- Pharmaceutical wholesale authorisations: 127 authorisations across 128 location rows; the register itself is dated 19 May 2026.
- Pharmaceutical quality-defect letters: all four listed product letters and their readable PDF text; this is not a complete recall register.
- Grid connection reservations and capacity queues: 746 published table rows grouped into 736 official cases. Simultaneous queue and reservation entries are preserved without addition.
- Aquaculture capacity auction allocations: 16 company rows and an announcement record in the selected 2024-and-later archive. The published 17,330 tonnes MTB and NOK 5,288,602,600 reconcile across company and area tables. These are capacity and consideration, not output or confirmed payments.

Each source completed two real adapter polls with silent initialisation and no repeated alert. Each poll verifies two consistent complete source reads. Contract tests separately cover revisions, missing or conflicting records, source limits, and actual notification-detail bounds. Synthetic changes are not evidence of a newly observed source event or delivery.

The inspection-report profile restores a narrower annual/main-industry view using the published report: 132 table rows, with three selected construction-sector annual aggregates. It is a coverage repair, not an additional event function and not a replacement for detailed industry-group history.

All adapters reuse the existing snapshot, state and notification mechanisms. Source disappearance is not interpreted as cancellation, withdrawal or closure. Initialisation, later scheduling, delivery and operational acceptance require separate runtime evidence.
