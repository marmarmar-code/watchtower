# Hendelser og konkrete endringer

Fra Watchtower 0.6 kan offentlige JSON-data, CSV-filer, valgte deler av nettsider
og faktiske SSB-observasjoner overvåkes uten å skrive en ny adapter. Hvert oppsett
velger hva som har en stabil identitet, hvilke felt som betyr noe, og hvilke
hendelser som skal varsles. Første ordinære henting etablerer en stille baseline.

Se [ferdige kildeoppsett](RECIPES.md) før du lager egne. De dekker blant annet
styringsrente, valuta, statistikk, alvorlige farevarsler og nye rapporter.

## JSON og CSV

Dette syntetiske eksemplet følger status og beløp for identifiserbare poster:

```toml
[[source]]
id = "selected_records"
kind = "json_records"
label = "Valgte registreringer"
enabled = false
urls = ["https://example.test/public/data.json"]
records_path = "results"
id_fields = ["id"]
fields = ["status", "amount"]
numeric_fields = ["amount"]
title_field = "title"
url_field = "url"
events = ["added", "changed"]
interval_minutes = 60
max_records = 1000
[source.where]
category = ["REPLACE_ME_CATEGORY"]
[source.thresholds.amount]
absolute = 100000
percent = 5.0
[source.field_labels]
status = "Status"
amount = "Beløp"
[source.filter]
match_all = true
```

`records_path` peker på en liste med objekter; utelat feltet når hele JSON-svaret
er listen. Punktum angir nøstede felt, eksempelvis `properties.id`. En bokstavelig
nøkkel med punktum har forrang. `where` krever eksakt samsvar mot ett av de oppgitte
valgene i hvert felt. Vanlige tekstfiltre brukes i tillegg.

`id_fields` skal identifisere samme post over tid. Legg ikke hentetid eller
publiseringsdato i identiteten hvis du vil følge samme løpende måling. Bare `fields`
utløser endring; omstokking av rader og endrede transportstempler ignoreres.
`numeric_fields` normaliserer tallformat, slik at `4.25` og `4.2500` er like.
Tomme tall blir manglende verdi, mens ugyldige tall avvises.

Bytt til `kind = "csv_records"`, bruk filens adresse og utelat `records_path` for
CSV. Sett `delimiter = ";"` ved semikolon. UTF-8 med eventuell BOM er standard;
`encoding = "latin-1"` er også tilgjengelig. Filen må ha entydige kolonnenavn og
fullstendige rader. Feil, duplikate identiteter og manglende valgte felt stanser
kildehentingen og bevarer forrige state.

For JSON med paginering kan `next_path`, `total_path` og `max_pages` settes. Neste
side må ligge på samme vert. Gjentatte sider, skiftende totaltall, avkortede uttrekk
og overskredet sidegrense avvises. Uten `total_path` kan ikke adapteren bevise at et
ellers gyldig svar inneholder alle poster.

## Grenser og bekreftelser

| Valg | Virkning |
| --- | --- |
| `events = ["added"]` | Bare nye identiteter |
| `events = ["changed"]` | Bare endring av valgte felt på kjente identiteter |
| `thresholds.FELT.absolute` | Minste absolutte endring |
| `thresholds.FELT.percent` | Minste prosentvise endring |
| `change_confirmations = 2` | Samme nye feltverdier må observeres ved to vellykkede hentinger |
| `allow_empty = true` | Tillat legitimt tomt utvalg |

Når begge tersklene er satt, må begge nås. Små bevegelser akkumuleres fra sist
aksepterte verdi; den verdien er ikke nødvendigvis sist leverte varsel dersom et
tekstfilter har stoppet leveringen. Overgang til eller fra manglende verdi varsles
uavhengig av tallterskelen. Fra null regnes enhver ikke-nullverdi som å passere
prosentgrensen; en eventuell absolutt grense gjelder fortsatt.

`changed` krever også `alert_on_update = true`, som er standard. Endringer i
adaptervalg, URL, feltutvalg eller terskler gir ny stille baseline for kilden.
Behold kilde-ID-en for vanlig videre drift. Endring av bare tekstfilter utvider
ikke historikken og sender ikke tidligere registrerte treff på nytt.

Forsvinning er av som standard. Bare når eieren vet at uttrekket er komplett,
kan `complete_snapshot = true` og `events = ["added", "changed", "removed"]`
brukes. Standard er to vellykkede uttrekk uten posten før varsel; sett
`removal_confirmations` fra 2 til 10. Kildesvikt teller ikke. Varselet sier at
posten ikke lenger finnes i utvalget, og er ikke bevis på sletting eller tilbakekall.

## Utvalgt innhold på nettsider

```toml
[[source]]
id = "selected_page"
kind = "web_page"
label = "Valgt publisert informasjon"
enabled = false
urls = ["https://example.test/public/information"]
selector = "main .publication-body"
ignore_selectors = [".updated-clock", ".related-content"]
change_confirmations = 2
interval_minutes = 60
[source.filter]
match_all = true
```

CSS-selektoren må finnes. Skript, navigasjon og bunntekst fjernes, og mellomrom
normaliseres. En nettsideendring krever som standard to like observasjoner.
Varslet viser korte før–etter-utdrag ved endringen, også når den ligger langt ned
på siden. Manglende selektor blir kildefeil, og nullstiller ikke baseline.

Bruk `web_links` for en rapport- eller dokumentliste. Selektoren må velge lenker,
for eksempel `main a.report`. Valgfri `title_selector = "h2"` henter bare tittelen
inne i lenken. Absolutt URL uten fragment er identiteten. `events = ["added"]`
gir nye lenker uten varsler om tittelrettinger.

Dette henter HTML, ikke JavaScript-renderte sider. Det følger ikke automatisk
paginering og undersøker ikke endret binærinnhold i en PDF med uendret lenke.
Lister er avgrensede vinduer: publiseringer som kommer og forsvinner mellom
hentinger kan bli oversett. Lokal eier må vedlikeholde selektorer når nettsider endres.

## Faktiske SSB-tall

`ssb_data` bruker [SSBs PxWebApi v2](https://www.ssb.no/api/pxwebapiv2) og
[JSON-stat 2](https://json-stat.org/format/). Den eldre `ssb`-adapteren følger
fortsatt bare tabellbeskrivelsen.

```toml
[[source]]
id = "selected_cpi"
kind = "ssb_data"
label = "Konsumprisindeksen"
enabled = false
table = "14700"
interval_minutes = 1440
[source.value_codes]
VareTjenesteGrp = ["00"]
ContentsCode = ["KpiIndMnd"]
Tid = ["top(3)"]
[source.filter]
match_all = true
```

Velg verdier i **alle** dimensjonene i tabellen. Eksemplet bruker tre siste måneder
fra gjeldende KPI-tabell. En ny periode gir «Ny statistikkobservasjon»; endring av
en allerede hentet celle gir «Revidert statistikkobservasjon» med før–etter-verdi.
Enhet og indeksbasis følger varselet. Endret beregningsgrunnlag eller datastatus
behandles også som en endring. Manglende verdi er ikke null.

Terskler for `value` gjelder revisjoner av samme celle, ikke bevegelsen mellom to
perioder. Nye perioder varsles uansett denne revisjonsterskelen. Revisjoner utenfor
det valgte tidsvinduet oppdages ikke. Eldre perioder som faller ut av et rullerende
vindu gir ikke forsvinningsvarsel. Velg større vindu bare når behov og hentegrense
forsvarer det.

## Henting, privat innhold og feilsøking

Standardgrensene er 1 000 poster og 4 MB dekomprimert innhold per svar. Maksimalt
kan 10 000 poster, 10 MB per svar og 20 JSON-sider velges. SSB-celler regnes som
poster. Overskridelser bevarer forrige state og vises som kildefeil.

Disse fem adapterne krever offentlige HTTPS-adresser uten innebygde credentials.
De avviser lokale IP-adresser angitt direkte i URL og kontrollerer omdirigeringer;
dette er ikke en full nettverksisolasjon eller DNS-basert tilgangskontroll.
API-er som krever egne autentiseringsprotokoller trenger en særskilt adapter.

Konfigurasjon, utvalgte feltverdier, utdrag og historikk lagres privat. `preview`
viser privat innhold lokalt; bruk `--redact-output` i delte logger. Sensitive egne
valg i `where` og `value_codes` bør også oppgis i `[privacy] protected_values` for
å omfattes av kontrollen mot offentlig kode. Vanlige filtre og virksomhetslister
er allerede omfattet. Ikke legg private uttrekk i offentlige feilrapporter.
