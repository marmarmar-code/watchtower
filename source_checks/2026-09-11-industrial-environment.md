# Miljødirektoratet industrial-installation check — 2026-09-11

Official layer: `https://kart3.miljodirektoratet.no/arcgis/rest/services/industri/MapServer/0`, queried through its fixed `/query` endpoint. The adapter requires an explicit list of positive integer `anlegg_id` values. It sends an exact `IN` filter, a restricted nine-field selection, `returnGeometry=false`, and a bounded `resultRecordCount`.

The exact verified configuration selected `installation_ids=[5447]`, `events=["added","changed"]`, `max_records=10`, and `max_bytes=100000`. Two real complete polls returned one record each, zero baseline alerts, zero repeat alerts, identical full snapshots, and scope hash `04b521c8ea4ed92966bc9872a8319ef331a68370359c33846e65a26addd743f1`.

The observed record was Yara Porsgrunn: `anlegg_id=5447`, `driftsstatus=Aktiv`, `forurensningsmyndighet=Miljødirektoratet`, `siste_rapportering_aar=2023`, and source flags for reported emissions to air, reported emissions to water, and monitoring requirements all set. The 2023 reporting year is the source's observed metadata and is not claimed to be current annual emissions data. No actual emission values are present in this adapter.

The stable identity is `anlegg_id`. The selected IDs must be returned exactly once, the ArcGIS selected-field schema and types must match, and `exceededTransferLimit` must be absent or false. Name, operational status, authority, reporting year, and the three source flags form the monitored content. All dates and publication timestamps are absent; `published` is unset.

The facts URL is validated against the installation's `CompanyID`. A bounded public GET of `https://www.norskeutslipp.no/Templates/NorskeUtslipp/Pages/company.aspx?CompanyID=5447` returned HTTP 200, `text/html`, and a usable page. This monitors operational status and reporting metadata. It does not claim an emissions-value change, permit revision, breach, or enforcement decision. Removed events and complete-snapshot claims are unsupported.
