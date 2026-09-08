# Videre utvikling

Watchtower er under utvikling. Videre produktarbeid og interne kontroller kommer
før utprøving hos andre redaksjoner. Det finnes ingen påstand om at produktet er
ferdig, eller om dokumentert oppsettstid hos mottakere.

## Implementert i utviklingsforslaget for 0.5

- Lokal konfigurasjonsveiviser, startpakker og delt privat virksomhetsliste.
- `link-github` for kontroll og oppsett av eksisterende kodefork og privat runtime.
- Finanstilsynets virksomhetsregister med avgrenset utvalg og konkrete endringer.
- Privat leveringskø som gjenopptar usendte meldingspakker etter en feil.
- Utvidet RSS-katalog, dekningsstatus og lesbar privat varselhistorikk.

Disse leveransene må gjennomgås og utgis før de regnes som tilgjengelige i `main`.
GitHub-oppsettet er testet med simulerte API-svar; faktisk nøkkeloppsett på en
isolert utviklingsinstallasjon gjenstår. Webhooks gir ikke nøyaktig én levering.

## Neste utviklingsleveranser

| Prioritet | Leveranse | Kriterium før den regnes som klar |
| --- | --- | --- |
| 1 | Isolert installasjonsprøve og enklere opprettelse av repoer | Full fork–runtime–kanal-kjede prøvd med syntetiske data, forståelig feilretting og gjenkjøring etter avbrudd |
| 2 | Uavhengig kontroll av livstegn og bedre varig lagring | Fravær av kjøringer oppdages uten hjelp fra den stansede workflowen; gjenoppretting etter runner-tap og feil ved state-push avklart |
| 3 | eInnsyn-saksoppfølging | Offisiell lesetilgang bekreftet; nye dokumenter og saksendringer har stabile ID-er og kontrollerte pagineringsgrenser |
| 4 | Valgte dokumentlister og strukturerte filer | Konkrete kilder, stabile dokument-/radidentiteter, endringsvisning og kontrakttester |
| 5 | Kanalruting og samlevarsling | Kildevis kanalvalg og tidsstyrte sammendrag uten tap ved feil eller duplisering ved omstart |
| 6 | Tallovervåking i utvalgte SSB-tabeller | Endringer i verdier, revisjoner og nye perioder skilles; måleenheter og sammenlikningsgrunnlag følger varselet |

Etter dette vurderes om oppsett, drift og redaksjonell nytte er tilstrekkelig
modent til en avgrenset utprøving hos andre. Det er et senere steg, ikke en
forutsetning for å fortsette utviklingen. Mottakernes egne forks skal ha tydelig
lokalt eierskap og valgfrie oppdateringer, uten løpende supportplikt for upstream.

Den eksisterende scheduler-kjeden beholdes mens enklere alternativer vurderes mot
krav til intervall, kostnad og gjenoppretting. Rettighetshaver og formell lisens
må avklares før bred distribusjon. Maksimal funksjonsbredde oppnås gradvis gjennom
valgfrie kilder; grunnoppsettet skal fortsatt kunne forstås og driftes selvstendig.
