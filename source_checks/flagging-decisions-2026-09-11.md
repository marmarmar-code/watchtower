# Dated flagging decisions

The [official decision list](https://www.finanstilsynet.no/tilsyn/markedsatferd/vedtak-om-overtredelsesgebyr---flaggeplikt/) yielded 13 dated links on two final recipe reads. First initialization was quiet, the repeated read produced no alerts, and the complete saved states were identical. The response body changes outside the selected links; this does not affect tracked fields. The final configuration and source module hashes are recorded in the accompanying JSON.

The existing `web_links` adapter tracks the absolute decision URL as stable identity. This recipe alerts only on a newly observed link; changes to the listed title, fine amount, decision body, appeal status or overwritten documents are not reported. Some titles omit the company name. The date is preserved as visible source text rather than asserted as a machine-readable publication timestamp.

The current selector matched all 13 dated decision links on the fetched list. It depends on the current `flaggeplikt-vedtak-om-overtredelsesgebyr` URL naming convention; a future new naming scheme could leave old matching links while new links are missed. No completeness claim extends to the authority's full decision archive. Missing selectors, conflicting titles for one URL and oversized responses fail before saving history.

When this list is used alongside an RSS feed carrying the same decisions, route the matching path-segment prefix away from RSS notifications with `exclude_url_path_segment_prefixes = ["flaggeplikt-vedtak-om-overtredelsesgebyr"]`. This keeps the RSS items and history while the specific list owns those notifications.
