# Central-bank certificate auction results

The final adapter completed two real polls, each with two full discovery/detail sweeps. The observed 2026 index contained 39 entries over two pages, including 19 result documents and 20 invitations. The 19 result pages yielded 38 instrument results. Across 92 complete HTTP responses, both polls produced zero alerts and identical full state. Response and code hashes are in the adjacent JSON receipt.

The official frontend supplies the index page identifier and its public NewsList request. The adapter discovers the newest advertised year, checks the applied year filter, follows explicit load-more markers and validates every entry before distinguishing invitations from results. Page and detail bounds fail closed. Each detail validates its official URL, title, publication day and labelled instrument groups.

Auction date plus ISIN identifies a result. Allocation price, yield, allocated MNOK, bid MNOK, marginal allocation percentage, settlement and maturity remain separate fields. These are central-bank certificates, not government bonds or current market quotations. Publication clocks have no inferred timezone. Previous years are outside the default scope. The frontend markers do not constitute a server transaction or an independently verified global total. Absence is not cancellation.

The combined 63 targeted tests also cover malformed dates/units, page limits, truncation, duplicate identities, revised values and preservation of persisted state. Synthetic checks and real-source proof do not establish runtime initialization; that evidence is recorded separately.
