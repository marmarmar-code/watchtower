# Ferdige kildeoppsett

Watchtower 0.6 har ti valgfrie oppskrifter for konkrete hendelser og endringer.
De er hentet med de faktiske adapterne fra offisielle kilder 8. september 2026.
Hver oppskrift ble også hentet på nytt uten falske endringsvarsler. Dette er en
kontroll på denne datoen, ikke en garanti for fremtidig tilgjengelighet eller full dekning.

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
