# Adapterdekning i isolert test

Katalogen har 69 oppsett: 17 RSS-profiler og 52 oppskrifter. De bruker fem av
18 adaptertyper. `examples/adapter-test-sources.toml` tilfører elleve eksplisitt
avgrensede, nøkkelfrie testkilder. Samlet gir dette 80 oppsett og 16 adaptertyper.
Testutvalget er bredere enn en privat installasjons kuraterte produksjonsutvalg.

| Adapter | Katalog | Ekstra testkilde | Live bevis |
| --- | ---: | --- | --- |
| rss | 24 | – | Katalogdrift og levering |
| json_records | 1 | – | Katalogdrift |
| csv_records | 3 | – | Katalogdrift |
| ssb_data | 7 | – | Katalogdrift |
| web_links | 34 | – | 31 i tidligere drift; tre nye hentet to ganger |
| web_page | 0 | Medietilsynet, valgt sidetekst | To hentinger |
| regjeringen | 0 | RSS, medie- og presseord | To hentinger |
| stortinget | 0 | Høringer, medie- og presseord | To hentinger, 351 unike poster |
| konkurransetilsynet | 0 | Fusjonsliste, tre mediegrupper | To hentinger |
| euronext | 0 | Polaris Medias utstederside | To hentinger |
| hoyesterett | 0 | To siste avgjørelser | To hentinger |
| brreg | 0 | NRK, bare selskapsstatus | To hentinger |
| ssb | 0 | KPI-tabell 14700, metadata | To hentinger |
| stotte | 0 | NRK som mottaker siden 2020, inntil 2 × 100 poster | To hentinger |
| finanstilsynet_short_sale | 0 | Bouvet, eksakt ISIN | To hentinger; null prosent er gyldig resultat |
| finanstilsynet_registry | 0 | DNB Bank, eksakt organisasjonsnummer | To hentinger |
| doffin | 0 | Krever tilgangsnøkkel | Kontrollerte parsertester; ikke live-verifisert her |
| patentstyret | 0 | Krever tilgangsnøkkel | Kontrollerte parsertester; ikke live-verifisert her |

Rapportene `source_checks/2026-09-09-adapters.json` og
`source_checks/2026-09-09-marketing.json` inneholder tidsstemplede, faktiske
hentinger, antall, unike identiteter og kildeeksempler. Henting beviser ikke at
alle hendelsestyper har inntruffet eller at alle kilder har levert varsler.
Den løpende installasjonen og dens faktiske kjøringer beskrives i
`CATALOG_RUNTIME_TEST.md`.

## Påviste feil og kontrollerte hendelser

Stortingets XML inneholder representantens ID før spørsmålets egen ID.
Den gamle parseren ga bare 30 unike nøkler for 3656 spørsmål. Parseren prioriterer
nå direkte felt på posten. Det innhentede svaret gir 3656 unike spørsmål;
saker gir 665 og høringer 351 unike poster. Testutvalget bruker høringer.

Ødelagte kvitteringsrader i leveringskøen kunne bli oppdaget etter sending.
Valideringen avviser nå disse før senderen kalles. Kontrollerte tester dekker
også delvis kildefeil, avbrudd, HTTP-timeout, begrenset Retry-After ved HTTP 429,
gjenopptaking etter delvis sending og finalisering uten gjentatt sending.
Dette er syntetiske feiltester, ikke observerte produksjonsfeil.

## Gjenværende begrensninger

Doffin og Patentstyret mangler autoriserte testnøkler. Det opprettes ingen
kunstige vellykkede live-resultater for dem. Andre virksomheter, felt og
hendelsestyper enn de eksplisitte valgene er ikke dekket av disse elleve kildene.
Kildenes helsestatus måler siste vellykkede henting; den beviser ikke at en
kildeeier har publisert nye data. En stille kilde er ikke automatisk en feil.
Høyesteretts to siste avgjørelser er et begrenset adaptertestutvalg, ikke full
løpende domsdekning. Webhooks gir ingen garanti for nøyaktig én levering.

## Direkte børsmeldingslenker

Euronexts tomme meldingslenker på selskapssiden løses gjennom radens node-ID og det offisielle detaljsvaret. Tittel, dato i den kanoniske adressen og eksakt ISIN-tilhørighet kontrolleres, også når meldingen gjelder flere verdipapirer. Den publiserte NewsWeb-lenken brukes når den er entydig; ellers beholdes selskapssiden. Meldingsnøkkel og alle kompatible historikkfingeravtrykk bevares, slik at en forbedret lenke alene ikke utløser et nytt varsel. Detaljsvar hentes bare for nye eller endrede meldinger ved ordinær kjøring.

To ferske innlesinger av Polaris-eksemplet og kontroll av meldingstitler og HTTP-svar er dokumentert i `source_checks/2026-09-10-euronext-links.json`. Dette er kilde- og rutekontroll, ikke full nettleserakseptanse. Meldingsutvalget fra selskapssiden er fortsatt begrenset til den synlige listen; større historikk krever egen identitets-/overgangskontroll.


## Utvidet børsliste (valgfritt)

`include_listview = true` følger selskapssidens entydige offisielle utstederliste, med `max_items = 50` (1–50). Standardoppsettet er uendret. Listen må inneholde selskapssidens aktuelle meldinger; manglende, foreldet eller flertydig liste gir synlig feil. Listen er avgrenset, uten eldre sider. Full liste uten overlapp med forrige historikk gir en dekningsadvarsel.

Bruk en ny kilde-ID ved overgang fra selskapssiden til utvidet liste: listene har ulike historiske identiteter. Første ordinære innlesing bygger stille historikk; den gjør ingen detaljoppslag og sender ikke eldre meldinger. Senere kjøres detaljoppslag bare for nye/endrede meldinger. Eksisterende kilders historikk skal ikke nullstilles.

25 målrettede adapter-/kontraktstester dekker blant annet stille førstegangsinnlesing, uendret standardvalg, avvisning av gammel/feil liste, grenseverdier, manglende historikkoverlapp og lenkevalidering av fulle tidsstempler.
