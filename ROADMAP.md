# Videre utvikling

Watchtower skal varsle om konkrete hendelser og endringer med forståelig relevans.
eInnsyn-saksoppfølging er tatt ut av prioritert utvikling. Flere funksjoner skal
kunne velges uten sentral runtime, automatiske upstream-oppgraderinger eller
løpende supportplikt for prosjektets opprinnelige utvikler.

## Levert i 0.5

Oppsettsveiviser, private virksomhetslister, GitHub-kobling, Finanstilsynets
virksomhetsregister, gjenopptakbar leveringskø, kildehelse, dekningsstatus og privat
varselhistorikk er merget til main og kontrollert i eksisterende produksjonskjøring.
Det bekrefter denne installasjonen; en helt ny installasjons fulle kjede er ikke
bevist ved å kjøre eksisterende produksjon.

## Leveransen i 0.6

| Område | Implementert |
| --- | --- |
| Strukturerte data | Valgte JSON-poster og CSV-rader med stabile identiteter og før–etter-endringer |
| Nettsider | Utvalgt tekst med bekreftelser og nye lenker fra dokumentlister |
| Faktiske tall | SSB-observasjoner, nye perioder, revisjoner, enheter og beregningsgrunnlag |
| Relevans | Feltvalg, eksakte radvalg, hendelsestyper, terskler og kontrollerte forsvinningsvarsler |
| Ferdige valg | 43 oppskrifter, 17 RSS-profiler og tolv startpakker |
| Videre oppsett | Legg til kilder i aktiv runtime og forhåndsvis henting uten sending eller lagring |

Kode, kildeoppskrifter, mal og oppgraderingsveiledning hører til samme leveranse.
De nye oppskriftene er kontrollert mot faktiske kilder med to separate hentinger.
Automatiserte tester dekker hendelser, revisjoner, feil og bevaring av state.
Dette er funksjonsbredde med eksplisitte avgrensninger, ikke dekning av alle
institusjoner eller dokumentasjon på nytte hos andre brukermiljøer.

## Gjenstående produktarbeid

| Prioritet | Leveranse | Kriterium |
| --- | --- | --- |
| 1 | Flere verifiserte sektoroppskrifter | Flere relevante kilder tas inn først etter to stabile parserhentinger og eksempelkontroll |
| 2 | Utvide hendelses- og adapterdekning i løpende test | 71 oppsett og 16 adaptertyper er klargjort; ekte drift og kontrollerte feil følges separat |
| 3 | Automatisert drift og vedlikehold | Privat testtidsplan er aktiv; offentlig kildekatalog kontrolleres fast og brudd følges opp |
| 4 | Bedre gjenoppretting etter runner-tap | Kontrollpunkt stopper usikre automatiske gjentakelser; varig lagring av hver leveringskvittering ved runner-tap gjenstår |
| 5 | Kanalruting og tidsstyrte sammendrag | Ulike kildegrupper kan leveres til ulike kanaler eller samles, med gjenopptaking ved feil |

Utvikling og interne kontroller fortsetter før eventuell utprøving hos andre.
GitHub-koblingen er testet med simulerte svar; ny nøkkelopprettelse i en helt isolert
installasjon gjenstår. Webhooks garanterer ikke nøyaktig én levering. Rettighetshaver
og formell lisens må avklares før bred distribusjon. Disse punktene skal ikke
omtales som ferdige på grunnlag av grønn CI alene.

Løpende isolert testing med mange aktive kilder skal gå samtidig med kildeutvidelsen.
Antall katalogoppsett og antall adaptertyper må rapporteres separat. Ingen vilkårlig
venteperiode for stabil drift skal stanse relevante kildeutvidelser.
