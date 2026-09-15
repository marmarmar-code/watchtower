# Public KOFA cases check — 2026-09-11

Original source: [Klagenemndssekretariatet, Innkomne/avgjorte saker](https://www.klagenemndssekretariatet.no/klagenemda-for-offentlige-anskaffelser-kofa/innkomne-avgjorte-saker), checked 2026-09-11.

The public page exposes a server-rendered table with the columns `Dato`, `Saknr.`, `Type`, `Innklaget`, `Saken gjelder`, `Avgjørelse`, and `Status`. The fetched HTML reported 4,997 results and contained exactly 10 table rows on the first page, including `2026/2039` (08.09.2026, Innkommet). The raw response is preserved at et privat kildearkiv.

The first vertical event function is the KOFA case register: stable case number identity, date and six selected fields, optional exact status filtering, duplicate rejection, empty/error fail-closed behavior, and SnapshotSource support for changed, added, and confirmed removed rows when configured with `complete_snapshot = true` and `events = ["added", "changed", "removed"]`. The site is paginated in the UI; this first adapter intentionally consumes the configured page only and therefore does not claim complete-register coverage.

Two live adapter reads against the original URL both returned 10 records with the same first identity/date/status (`2026/2039`, `08.09.2026`, `Innkommet`). No notifier or state store was used. The adapter rejects `complete_snapshot` and `removed` because a single UI page cannot prove that an omitted case was deleted. Pagination is ordinary query-string navigation in the page links; the current adapter intentionally does not follow it, so this is a first-page monitor and must not claim full-register coverage.

The adapter now accepts `max_pages` and follows the source's actual `a.next[href]` links within the configured host. A multi-page window is still only a window; changes and identities are meaningful inside that selected window. A live poll sequence over the first page produced 10 keys on both reads, identical content hashes, and zero second-poll alerts.

The same register shape is not shared by all boards. Live HTML checks found Medieklagenemnda at `/medieklagenemnda/innkomne-avgjorte-saker` (256 results, columns `Dato`, `Saknr.`, `Klager`, `Avgjørelse`, `Status`, `Saksbeh.`) and Markedsrådet at `/markedsradet/innkomne-avgjorte-saker` (92 results, columns `Dato`, `Saknr.`, `Part/klager`, `Saken gjelder`, `Avgjørelse`, `Regelverk`, `Status`, `Saksbeh.`). They are relevant public case feeds, but their differing headers/field counts mean the strict KOFA adapter must not be reused with an unvalidated `board=` switch. A future board-specific schema option or separate adapter is required.

The adapter now has explicit `board=kofa|media|marketing` schemas, fixed original URLs, board-prefixed stable keys, and board-specific title prefixes. Two live polls per related board returned stable windows and zero second-poll alerts: media 10/10 records, marketing 10/10 records. The page date is day-level (`DD.MM.YYYY`); it is the register's displayed received/decided date, not an observed update timestamp. The current sort is the site's default first-page order, and no claim is made that an old case status change will be surfaced without a new row/date or a selected page window.

The first sandbox shell attempt failed DNS; the subsequent approved network read succeeded and is the parser evidence.
