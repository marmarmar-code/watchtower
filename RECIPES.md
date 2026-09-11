# Ferdige kildeoppsett

Watchtower har valgfrie oppskrifter for konkrete hendelser og endringer.
`python -m watchtower list-recipes` viser hele listen og sektorene. Hver oppskrift
har egen kildelenke, kontrolldato og eksplisitt avgrensning. Kildefunn og faktiske
kontroller ligger i `source_checks/`; les oppgitt begrensning før aktivering.
En vellykket innlesing beviser ikke at et nytt relevant varsel er levert.

Nye saks- og produktkilder er forklart i [utvidelsesoversikten](SOURCE_EXPANSION.md).
Første ordinære innlesing er stille. Eksisterende oppsett endres ikke ved at
katalogen får en ny oppskrift.

| Oppskrift | Hva den følger | Standard |
| --- | --- | --- |
| `nb_policy_rate` | Endring av Norges Banks styringsrente | Daglig; samme rente på ny dato ignoreres |
| `nb_usd_nok` | Norges Banks USD/NOK-observasjon | Daglig; minst 1 % fra sist aksepterte verdi |
| `nb_eur_nok` | Norges Banks EUR/NOK-observasjon | Daglig; minst 1 % fra sist aksepterte verdi |
| `ssb_cpi` | KPI totalt, tabell 14700 | Tre siste måneder; nye verdier og revisjoner |
| `ssb_housing` | Boligprisindeks for landet, tabell 07221 | Fire siste kvartaler; nye verdier og revisjoner |
| `ssb_retail` | Sesongjustert volumindeks, tabell 07129 | Tre siste måneder; nye verdier og revisjoner |
| `ssb_bankruptcies` | Antall åpnede konkurser, tabell 09695 | Tre siste måneder; nye verdier og revisjoner |
| `met_severe_weather` | METs oransje og røde farevarsler | Hver time; gule varsler utelates |
| `riksrevisjonen_reports` | Nye rapportlenker på første listeside | Hver time; krever tema eller `--all` |
| `nkom_events` | Nkoms nye RSS-publiseringer | Hver time; krever tema eller `--all` |

SSB-oppskriftene hentes daglig. Dette er publiserte observasjoner, ikke kursdata i
sanntid. MET-utvalget var legitimt tomt ved kontrollen; overgang fra gult til
oransje og videre til rødt er i tillegg kontrollert med syntetiske hendelser.

Katalogen inneholder kildenes offisielle dokumentasjonsadresser, eksakte utvalg og
kontrolldato. Se [Norges Banks åpne data](https://www.norges-bank.no/en/topics/statistics/open-data/),
[SSBs API](https://www.ssb.no/api/pxwebapiv2),
[METs farevarsel-API](https://api.met.no/weatherapi/metalerts/2.0/documentation),
[Riksrevisjonens rapporter](https://www.riksrevisjonen.no/rapporter/) og
[Nkoms publiseringer](https://nkom.no/aktuelt).

## Legg til uten å bygge opp runtimen på nytt

Kjør fra kodekatalogen med oppgradert Watchtower:

```bash
python -m watchtower list-recipes
python -m watchtower list-recipes --sector finance
python -m watchtower add-source --runtime ../watchtower-runtime --recipe nb_policy_rate
```

Den siste kommandoen viser en ferdig konfigurasjonsblokk uten å skrive noe.
Legg til `--apply` når du vil lagre den i din lokale private runtime:

```bash
python -m watchtower add-source --runtime ../watchtower-runtime --recipe nb_policy_rate --apply
python -m watchtower preview --config ../watchtower-runtime/config/watchtower.toml --state-dir ../watchtower-runtime/state --source nb_policy_rate
```

`add-source` kontrollerer hele konfigurasjonen, legger til kildeblokken og bevarer
øvrig konfigurasjon og state. Den avviser en kilde-ID som allerede finnes.
`--source-id EGEN_ID` kan brukes for et annet avgrenset utvalg. Den endrer ikke en
allerede installert oppskrift; slike valg redigeres i privat konfigurasjon.

`preview` henter én kilde, viser et begrenset utvalg og beregner aktuelle varsler
mot eksisterende state. Den sender ingenting og skriver verken baseline eller
leveringskø. `--limit 20` viser flere poster, og `--redact-output` viser bare tellinger.

Se over resultatet, og commit og push fra **det private runtime-repoet**. Først da
kan neste ordinære kjøring ta kilden i bruk. Den etablerer en stille baseline;
etterfølgende hendelser følger de valgte reglene. Kommandoene administrerer ikke
GitHub eller kanaltilgang på nytt.

For nyhetskilder velges relevans eksplisitt:

```bash
python -m watchtower add-source --runtime ../watchtower-runtime --recipe nkom_events --topic REPLACE_ME_TOPIC
python -m watchtower add-source --runtime ../watchtower-runtime --recipe riksrevisjonen_reports --all
python -m watchtower add-source --runtime ../watchtower-runtime --rss-profile met_farevarsler --topic REPLACE_ME_TOPIC
```

Erstatt plassholderen med eget tema før kjøring. `--all` betyr alle treff i
oppgitt utvalg. METs RSS-profil er bredere enn oppskriften for alvorlige varsler.
Velg én tilnærming for samme behov; separate kilder dedupliserer ikke på tvers.
Riksrevisjonen-oppskriften filtrerer titler, ikke rapportenes fulle innhold.

## Startpakker og lokalt eierskap

Nye installasjoner kan velge `general`, `finance`, `health`, `digital`, `property`
eller `retail` i `setup`. Se [installasjonsveiledningen](INSTALL.md). Startpakkene
er utgangspunkt, og kan utvides med oppskrifter og RSS-profiler etter oppsett.

Alle oppskriftene kopieres til privat konfigurasjon. Senere katalogendringer
endrer ikke en aktiv installasjon automatisk. Eieren velger kilder, intervaller,
terskler og kodeoppgraderinger. Det opprettes ingen sentral runtime eller supportplikt.

Ved avbrudd under `add-source`: kontroller at ingen slik kommando fortsatt kjører.
Hvis `config/.watchtower-config.lock` ligger igjen, fjern den lokalt før ny kjøring.
Ikke commit låsen eller midlertidige filer. Se [endringsreglene og begrensningene](EVENT_MONITORING.md)
for egne JSON-, CSV-, nettside- og statistikkutvalg.

## Flere næringslivskilder

NHO, Finans Norge, Sjømat Norge og Forbrukertilsynet er tilgjengelige som valgfrie RSS-oppsett. Velg tema eller eksplisitt alle saker. NHO kan også publisere arrangementer; Finans Norge inkluderer bransjearrangementer. Rekrutteringsfraser hos Forbrukertilsynet og ikoninnhold hos NHO utelates også når alle saker velges. To ferske hentinger per kilde er kontrollert; dette dokumenterer henting og filtrering, ikke at nye varsler er levert.

Se [30 nye hendelsesfunksjoner](EVENT_FUNCTIONS.md) for konservativ telling og minst to anvendelser per brukermiljø.
