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
| Ferdige valg | Ti oppskrifter, ti RSS-profiler og seks startpakker |
| Videre oppsett | Legg til kilder i aktiv runtime og forhåndsvis henting uten sending eller lagring |

Kode, kildeoppskrifter, mal og oppgraderingsveiledning hører til samme leveranse.
De ti oppskriftene er kontrollert mot faktiske kilder og gjentatt henting.
Automatiserte tester dekker hendelser, revisjoner, feil og bevaring av state.
Dette er funksjonsbredde med eksplisitte avgrensninger, ikke dekning av alle
institusjoner eller dokumentasjon på nytte hos andre redaksjoner.

## Gjenstående produktarbeid

| Prioritet | Leveranse | Kriterium |
| --- | --- | --- |
| 1 | Uavhengig kontroll av livstegn og sikrere varig kølagring | Stans oppdages uten den stansede workflowen; runner-tap og state-push-feil håndteres |
| 2 | Kanalruting og tidsstyrte sammendrag | Ulike kildegrupper kan leveres til ulike kanaler eller samles, med gjenopptaking ved feil |
| 3 | Flere verifiserte sektoroppskrifter | Konkrete energi-, sjømat-, transport- og industrikilder med operativ tilgang og kontrollerte utvalg |
| 4 | Isolert installasjonsprøve | Hele fork–privat runtime–kanal-kjeden prøves med syntetiske data og avbrutt oppsett |
| 5 | Bedre dokumentovervåking | Kontrollert paginering og endret dokumentinnhold bak samme lenke, med tydelige ressursgrenser |

Utvikling og interne kontroller fortsetter før eventuell utprøving hos andre.
GitHub-koblingen er testet med simulerte svar; ny nøkkelopprettelse i en helt isolert
installasjon gjenstår. Webhooks garanterer ikke nøyaktig én levering. Rettighetshaver
og formell lisens må avklares før bred distribusjon. Disse punktene skal ikke
omtales som ferdige på grunnlag av grønn CI alene.
