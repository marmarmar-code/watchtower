# SSB building permits

The final recipes each read two monthly observations twice through the existing SSB adapter. Both initial reads were quiet; repeated reads produced no alerts and identical complete state. Response hashes and final configuration hashes are recorded in the accompanying JSON. This proves public-source parsing and duplicate suppression, not scheduled execution or delivery of a new real event.

- [Table 05808](https://www.ssb.no/statbank/table/05808): unadjusted number of dwellings with building permits. June 2026: 1,727; July: 1,662.
- [Table 05809](https://www.ssb.no/statbank/table/05809): unadjusted utility floor space other than in dwellings, measured in 1,000 m². June 2026: 517; July: 263.1. This category is broader than commercial buildings.

Both selections use `top(2)` so they follow the two latest published months. Revisions outside that window are not observed. A permit does not establish that physical construction has started. Definitions and publication context: [SSB building statistics](https://www.ssb.no/bygg-bolig-og-eiendom/bygg-og-anlegg/statistikk/byggeareal).
