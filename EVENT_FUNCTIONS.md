# Hendelsesfunksjoner og videre utvidelse

Den første funksjonsutvidelsen
inneholder 32 nye konfigurerbare oppskrifter, som konservativt telles som
30 ulike funksjoner. Katalogen øker fra 55 til 87 oppskrifter. KOFA,
Medieklagenemnda og Markedsrådet teller samlet én gang. Gjenbruk for flere
brukermiljøer, nye organisasjonsnumre og RSS-varianter gir ingen ekstra telling.

Detaljert hendelsesinnhold og kildegrenser står i [SOURCE_EXPANSION.md](SOURCE_EXPANSION.md).
To ekte offentlige avlesninger per endelig oppskrift ga stille førstegangsinnlesing,
null gjentakelsesvarsler og identisk full historikk. Dette beviser kildeinnlesing
og duplikatvern i lokal kontroll, ikke drift eller faktisk levering.
TED-kontraktsendringer hadde et tomt, gyldig vindu; denne funksjonen har ennå
ikke en positiv kildehendelse som bevis. Stortingseksemplet er historisk fra 2021.
Industrianleggets siste rapporteringsår var 2023. Ingen av disse omtales som ferske hendelser.

| Nr. | Ulik funksjon | Oppskrift(er) |
| --- | --- | --- |
| 1 | Mattilsynet – tilbakekallinger og endret produktomfang | `mattilsynet_recall_products` |
| 2 | NVE – konsesjonssaker og prosjektendringer | `nve_solar_cases` |
| 3 | DMP – legemiddelmangel og endrede mangelperioder | `dmp_shortage_periods` |
| 4 | Klagenemndenes saksforløp og avgjørelser | `kofa_case_progress`, `marketing_appeal_cases`, `media_appeal_cases` |
| 5 | eInnsyn – journalposter om valgt prosjekt | `einnsyn_project_journal` |
| 6 | TED – kontraktsresultater, vinnere og verdi | `ted_contract_awards` |
| 7 | TED – kunngjorte kontraktsendringer | `ted_contract_modifications` |
| 8 | Oslo PBE – saks- og dokumentaktivitet | `oslo_project_cases` |
| 9 | Finansklagenemnda – avgjørelser om valgt selskap | `finkn_company_decisions` |
| 10 | Sodir – gjeldende operatører for utvinningstillatelser | `sodir_licence_operator` |
| 11 | Sodir – feltstatus | `sodir_field_status` |
| 12 | Akvakulturlokalitet – kapasitet og registerendringer | `aquaculture_site_capacity` |
| 13 | Forskningsrådet – innvilgede prosjekter | `research_approved_projects` |
| 14 | Sodir – letebrønners status og boredatoer | `sodir_exploration_progress` |
| 15 | Norges Bank – daglig NOWA-rente | `nb_nowa_observations` |
| 16 | NVE – magasinfylling NO1 | `nve_reservoir_no1_weekly` |
| 17 | BRREG – tilgjengelige årsregnskapskopier | `brreg_account_document_years` |
| 18 | PFU – avgjørelser og utfall | `pfu_decision_outcomes` |
| 19 | CISA – utnyttede sårbarheter | `cisa_known_exploited` |
| 20 | Kliniske studier – norsk studiested | `clinical_trials_norway_updates` |
| 21 | Arbeidstilsynet – tilsynsreaksjoner per næring | `arbeidstilsynet_inspection_reactions_by_industry` |
| 22 | Smilefjes – tilsynsresultater for serveringssted | `smilefjes_restaurant_inspections` |
| 23 | EMA – produktstatus og indikasjon | `ema_medicine_authorisations` |
| 24 | Mattilsynet – plantevernmiddelgodkjenninger | `mattilsynet_pesticide_products` |
| 25 | Mattilsynet – midlertidige plantevernmiddeltillatelser | `mattilsynet_pesticide_temporary_permits` |
| 26 | Fiskehelse – ILA-mistanke og påvisning | `fishhealth_ila_outbreaks` |
| 27 | ILA-beskyttelsessoner – forskrift og gyldighet | `fishhealth_ila_zone_validity` |
| 28 | Nye metoder – innføringsbeslutninger | `nye_metoder_treatment_decisions` |
| 29 | Industrianlegg – driftsstatus og rapporteringsår | `industrial_installation_status` |
| 30 | Stortinget – voteringsresultater i valgt sak | `storting_case_vote_outcomes` |

Maskinlesbart kildebevis: [final-verification.json](source_checks/2026-09-11-final-verification.json).
Alle kildeadaptere er merket beta; første innlesing er stille, og mangelfulle
svar skal gi feil fremfor å endre historikken. Ingen ny adapter varsler sletting.

## Neste leveranse: automatisk oppdagelse

`research_result_discovery` utvider prosjektfunksjonen med automatisk oppdagelse av resultatmerkede utlysninger fra valgte år. To endelige avlesninger fant åtte utlysninger fra 2026 med 277 innvilgede prosjekter. Dette erstatter behovet for å legge til hver resultat-URL manuelt, men årets utvalg må vedlikeholdes. Forskjellen mellom søkt beløp, tildelt beløp og gradsgivende institusjon bevares fra kilden. Manglende sider, ufullstendige grupper eller overskredet grense gir feil før historikk lagres. Se [kildebevis](source_checks/research-discovery-2026-09-11.json).

Dette er en vesentlig utvidelse av funksjon 13, ikke en ekstra telling for hvert prosjekt eller hver utlysning. Senere milepæler må dokumentere faktisk funksjonsbredde, relevante mål og drift hver for seg.

`industrial_permit_inspection_documents` gir én ny dokumentfunksjon: oppdagelse av tillatelses- og tilsynsdokumenter og endringer i deres metadata. 21 lenker fra Yara Porsgrunn ble lest to ganger. PDF-innholdet overvåkes ikke; uendret lenke til en overskrevet PDF utløser derfor ikke varsel. Dette løfter den konservative funksjonstellingen fra 30 til 31.

`brreg_publishing_company_discovery` gir ytterligere én ny funksjon: automatisk oppdagelse av nylig registrerte AS/ASA i valgte næringsdivisjoner. Det verifiserte 30-dagersvinduet for divisjon 58 hadde fem AS. Dette er registrering i Enhetsregisteret, ikke en påstand om stiftelsesdato. Den konservative tellingen er dermed 32 ulike nye funksjoner, med prosjektoppdagelse som en vesentlig utvidelse av en eksisterende funksjon. Ved denne milepælen hadde katalogen 90 oppskrifter og 17 RSS-profiler.

Ved samtidig bruk av automatisk forskningsoppdagelse og en eksplisitt utlysning kan `suppress_call_urls` rute den eksakte overlappende utlysningens varsler til det eksplisitte oppsettet. Alle prosjekter leses og beholdes fortsatt; valget endrer bare varsling og bevarer eksisterende scope/historikk.

## Månedlige byggetillatelser

`ssb_housing_permits_monthly` følger antall boliger med igangsettingstillatelse. `ssb_nonres_floor_permits_monthly` følger bruksareal til annet enn bolig, målt i 1000 m². Begge er ujusterte og følger de to siste publiserte månedene, inkludert revisjoner innenfor vinduet. Tillatelse dokumenterer ikke faktisk byggestart, og arealserien omfatter mer enn næringsbygg.

To ekte avlesninger per oppskrift ga to observasjoner, stille førstegangsinnlesing og identisk historikk uten gjentakelsesvarsel. Se [kildekontroll](source_checks/ssb-building-permits-2026-09-11.md). De to oppskriftene telles konservativt som én ny funksjonsfamilie for byggetillatelser: 33 ulike funksjoner og totalt 92 oppskrifter.

## Automatisk voteringsoppdagelse

`storting_recent_vote_discovery` finner saker gjennom Stortingets sesjoner, møter og dagsordener. Den overvåker voteringsresultatene uten manuell liste over saks-ID-er. En votering knyttet til flere saker behandles én gang; endring i hvilke saker som omfattes av tidsvinduet lager ikke et resultatvarsel. Motstridende resultater eller manglende sesjonsdekning avviser uttrekket.

To ekte avlesninger ga et gyldig tomt vindu. Et separat historisk uttrekk med 31 voteringer verifiserer positiv parsing, men er ikke bevis på en ny hendelse eller levering. Se [kildekontroll](source_checks/parliament-discovery-2026-09-11.md). Dette er en vesentlig utvidelse av eksisterende voteringsfunksjon og øker katalogen til 93 oppskrifter; den konservative funksjonstellingen forblir 33.

## Flaggepliktvedtak

`finanstilsynet_flaggeplikt_gebyrvedtak` oppdager nye lenker i den særskilte listen over gebyrvedtak. To ekte avlesninger ga 13 daterte lenker, stille baseline og identisk historikk uten gjentakelsesvarsel. Varslet viser kildens listetekst og lenke; gebyrbeløp, klageutfall og dokumentrevisjoner inngår ikke. Se [kildekontroll og begrensninger](source_checks/flagging-decisions-2026-09-11.md).

Den særskilte vedtakslisten gir ett nytt konfigurerbart hendelsesutvalg. Konservativ telling er 34 funksjoner og totalt 94 oppskrifter. Ved parallell RSS-overvåking rutes samme vedtakstype til listen uten å slette RSS-historikk.

## Tinglyste hjemmelsoverføringer

`ssb_dwelling_property_transfers` følger kvartalsvise hjemmelsoverføringer av boligeiendom, med total og delmengden fritt salg. Dette gir faktisk overføringsaktivitet utover prisindekser og byggetillatelser. De to siste publiserte kvartalene ga fire observasjoner i to stille, identiske avlesninger. Se [kildekontroll](source_checks/property-transfers-2026-09-11.md). Katalogen har nå 95 oppskrifter og konservativt 35 ulike funksjoner.
