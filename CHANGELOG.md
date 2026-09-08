# Changelog

## 0.6.0 – hendelser, tall og valgte nettsider

- Fem nye adaptertyper: `json_records`, `csv_records`, `web_page`, `web_links` og
  `ssb_data`. Totalt 18 adaptertyper; alle tillegg er valgfrie.
- Stabil postidentitet, eksplisitte feltvalg, nøyaktig radutvalg, tallnormalisering,
  før–etter-visning, absolutte/prosentvise terskler og bekreftelser mot kortvarige endringer.
- SSB-tall følger nye perioder, revisjoner, manglende verdier, enheter og indeksbasis
  i eksplisitt valgte celler. Den eldre metadataadapteren beholdes.
- Ti ferdige kildeoppskrifter for Norges Bank, SSB, MET, Riksrevisjonen og Nkom.
  Alle ti er hentet og gjentatt uten falske endringsvarsler 8. september 2026.
- `list-recipes`, lokal `add-source` med forhåndsvisning og kontrollert tilføying,
  og `preview` av én kilde uten sending eller lagring.
- Seks startpakker: generell, finans, helse, digital infrastruktur, eiendom og
  handel. Finanspakken får styringsrente og KPI; eiendom og handel får valgte SSB-tall.
- RSS-kilder i nye startpakker varsler nye publiseringer som standard; varsling
  av rettinger må aktiveres uttrykkelig. Eksisterende oppsett endres ikke.
- Nkom og MET som nye RSS-profiler; totalt ti profiler.
- Hentegrenser, duplikatkontroll og validering av JSON-paginering. Feil bevarer
  tidligere snapshot. Forsvinning krever eksplisitt komplett uttrekk og bekreftelser.
- Konfigurasjonsformat 1 og eksisterende aktive kildevalg beholdes. Nye kilder
  etablerer stille baseline. eInnsyn er tatt ut av prioritert produktutvikling.

Se [RECIPES.md](RECIPES.md), [EVENT_MONITORING.md](EVENT_MONITORING.md) og
[UPGRADING.md](UPGRADING.md). Oppskriftenes avgrensning er ikke full sektordekning.

## 0.5.0 – enklere selvstendige installasjoner

- Lokal `setup`-veiviser med generelle, finans- og helserettede startpakker. Aktive
  runtimes beskyttes; et deaktivert oppsett bevares før erstatning.
- Lokal `link-github` kobler eksisterende repoer med egne deploy-nøkler,
  Actions-secrets og eksplisitte runtime-variabler. Kontroll uten endringer er standard.
- Finanstilsynets virksomhetsregister følger aktive tillatelser, tjenester og
  merknader for valgte organisasjonsnumre. Ufullstendige svar bevarer tidligere state.
- Privat leveringskø med kvittering per meldingspakke. Neste kjøring fortsetter
  usendte pakker før ny henting; status viser uferdig levering.
- Privat `[[entity]]`-liste med eksplisitte referanser til navn/aliaser eller
  registeridentifikatorer. Eksisterende konfigurasjon fungerer fortsatt.
- Fire nye RSS-profiler fra EMA og Skatteetaten, kontrollert med faktisk henting
  og parsing 8. september 2026. Nye pakker bruker én kilde per feed.
- Doffin markerer fulle resultatvinduer og manglende overlapp med tidligere treff.
  Hentede treff behandles fortsatt; begrensninger vises i privat og redigert status.
- RSS støtter eksplisitt tillatt tom feed og avviser ufullstendige elementer.
- Lesbar privat historikk med stabil varsel-ID, leveringsform og hele siste
  varselrunde. `history` kan lese både gamle og nye audit-rader.
- Kildevarsler og feil beholdes gjennom kjøringer der kilden venter på sitt intervall.
- `--version`, konfigurasjonsformat 1, samlet installasjonsoppskrift og rutine for
  oppgraderinger. Scheduler og produksjonskobling er uendret.

Se [UPGRADING.md](UPGRADING.md) før utrulling. Denne oppføringen er ikke i seg selv
en publisert GitHub Release.

## Production safety maintenance — 2026-08-28

### Fixed

- Corrupt or malformed state for one source is isolated instead of stopping all source checks or silently rebaselining that source.
- Doffin credentials can only be sent to the official API endpoint, and malformed notice rows now fail closed.
- Enabling BRREG annual-account monitoring for an existing source no longer emits a historical account as a new alert.
- Notification transport failures no longer retain webhook URLs in displayed exception chains.
- Runtime validation rejects symbolic links that could escape the reviewed runtime boundary.
- Production jobs are restricted to `main`, early workflow failures can still send a dependency-free operational alert, and source-health pipeline failures are visible.
- Self-dispatched scheduler runs queue their successor instead of cancelling the run before a failed monitor dispatch becomes visible.

### Changed

- Common source IDs, booleans, arrays and polling intervals are validated strictly before source setup.
- Production-flow documentation now describes the scheduler, monitor, private-state commit and cron-recovery chain.

## Business-source expansion — 2026-08-27

### Added

- Scoped monitoring of public allocations in Støtteregisteret.
- Aggregate and holder-level short-position monitoring for selected issuers.
- Credential-backed beta monitoring of Patentstyret portfolios.
- Optional BRREG group-structure and registry-update events.

### Changed

- Package version is now `0.4.0`.
- The runtime template includes disabled examples for the new business sources.

## Adoption and source expansion — 2026-08-27

### Added

- Ready-to-use official RSS profiles for Politiloggen, Finanstilsynet, Mattilsynet and Norges Bank press releases.
- SSB table monitoring through the official PxWebApi v2 metadata surface without downloading statistics data.
- Read-only source-health reporting with redacted GitHub Actions summaries.

### Changed

- Package version is now `0.3.0`.
- Scheduled runs use an off-peak five-minute cadence while respecting each source interval.
- The runtime template documents the current schedule and includes disabled RSS-profile and SSB examples.

## Fork-owned distribution baseline — 2026-08-27

### Added

- Explicit fork ownership, support boundaries and independent update policy.
- A machine-readable public source catalog with fork-owned maintenance metadata.
- A safe generator for unregistered source-adapter skeletons and contract tests.
- A generic RSS and Atom adapter for configuration-only feed monitoring.

### Changed

- Package version is now `0.2.0`.
- CI validates that every bundled adapter is represented in the source catalog.

## Alert-storm hotfix — 2026-08-27

### Fixed

- Notification-only details no longer change persisted item fingerprints.
- State written by the short-lived transition fingerprint is accepted and silently migrated.
- More than 32 alerts in one run are replaced by one safety summary instead of detailed notification batches.

## Distribution-ready baseline — 2026-08-26

### Added

- Microsoft Teams notifications through Adaptive Cards.
- Provider-specific Slack and Teams formatting.
- Bounded notification batches.
- Optional BRREG monitoring for annual accounts, company status and roles.
- Fork-local private runtime discovery with optional repository variables.
- Runtime preflight validation and representative notification tests.
- Parser contract tests for public source adapters.
- Generic setup, support, contribution and security documentation.

### Changed

- All third-party GitHub Actions are pinned to reviewed commit SHAs.
- Enabled sources must have complete configuration and positive filter rules.
- Configured company identifiers are included in private/public leak checks.
- Setup placeholders are excluded from leak deny-lists until they are replaced.
- Monitoring runs fail when no source is enabled.
- CI includes patch, package, compilation, security, unit and CLI checks.

### Fixed

- BRREG state changes no longer generate repeat alerts on the following unchanged run.
- BRREG organisation numbers are validated before monitoring starts.
- Missing BRREG entities fail closed instead of becoming apparent changes.
- Removed BRREG entities produce a focused status alert without synthetic form or industry changes.
- Teams alerts no longer contain Slack link syntax.
- Teams and Power Automate webhook URLs are detected as secret-like content.

### Pending before general open-source distribution

- Confirm the correct rights holder and add an explicit software license.
