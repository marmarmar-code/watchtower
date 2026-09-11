# Forskningsrådet result discovery — 2026-09-11

Official listing: `https://www.forskningsradet.no/utlysninger/?timeframe=2`, the site's own «Søknadsresultater» tab. It is a server-rendered one-page list. The verified HTML contained 99 proposal cards, 98 cards with the exact `status--result-is-published` / `Se Resultat` marker, and no duplicate call links. Eight marked calls belonged to the selected year 2026.

Discovery is opt-in and leaves the established explicit `urls` mode intact. The exact verified configuration is:

```toml
kind = "research_awards"
discover_results = true
discovery_years = [2026]
max_listing_pages = 1
max_discovery_calls = 10
max_records = 500
max_bytes = 500000
allow_empty = true
events = ["added", "changed"]
```

Every marked call in the selected year is fetched; the adapter rejects the selection before fetching result pages if it exceeds `max_discovery_calls`. It validates the original Forskningsrådet HTTPS host and `/utlysninger/<year>/<slug>/` path, duplicate URLs, card/status/link structure, response bytes, result block, table header, row types, project identities, amounts and publication dates. A structurally valid listing with no marked calls in the selected year is a valid empty selection; an absent or malformed listing fails.

The eight 2026 result pages contained 277 approved-project rows in total. Five used the established `Søkt beløp` table, two used the observed `Gradsgivende institusjon` variant, and one used `Tildelt beløp`. The existing explicit-page `Søkt beløp` fields remain unchanged. Source amounts containing grouping spaces are normalized to digits.

Two complete real discovery polls returned 277 records each across all eight selected result pages, zero first-baseline alerts, zero repeat alerts, and identical full snapshots. The configuration scope hash was `39502656c5ec256edd0e07231b279f9d9dca882e745e5ec3aa7852ea0da84811`.

The call URL plus project number remains the stable identity. Listing generation metadata is not stored. The first valid read is a quiet baseline, and removed events remain unsupported because result pages cannot prove project removal.

Root integration verifies each declared group count against every embedded card, including hidden cards, and rejects pagination. The nine declared counts are 22, 12, 1, 20, 8, 7, 2, 25 and 2 (99 total). The final exact recipe and the established explicit-page recipe were each read twice after this guard: 277 and 19 records respectively, quiet baseline/repeat and identical full state. Final hashes and public response hashes are recorded in the companion JSON.

When the discovery recipe is combined with an explicit-call source, `suppress_call_urls` can route an exact overlapping call to the explicit source. Every result is still read, validated and retained. Following the existing web-link routing pattern, this notification-only option is excluded from the snapshot scope. Tests compare the same full next state with and without routing and a simulated changed project emits only from the selected route. Two real routed discovery reads returned all 277 rows with zero baseline/repeat alerts. This is overlap control, not proof of a new delivered project event.
