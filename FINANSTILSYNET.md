# Finanstilsynets virksomhetsregister

`finanstilsynet_registry` følger endringer for 1–100 eksplisitte norske
organisasjonsnumre i det offentlige Registry API v2. Ingen kildenøkkel kreves.
Adapteren er beta. En faktisk henting og en uendret gjentatt henting ble kontrollert
8. september 2026; dette er ikke en bekreftelse på alle registerets virksomhetstyper.

```toml
[[source]]
id = "financial_registry"
kind = "finanstilsynet_registry"
label = "Finanstilsynets virksomhetsregister"
enabled = false
interval_minutes = 1440
alert_on_update = true
companies = ["REPLACE_ME_ORGNR"]
max_pages = 3
[source.filter]
match_all = true
```

Erstatt plassholderen med gyldig organisasjonsnummer før aktivering. Alternativt:
bruk `entity_refs` til virksomheter i den [private virksomhetslisten](ENTITIES.md).
Finanspakken legger til denne koblingen når virksomheter oppgis i `setup`.

Første henting er stille baseline. Nye virksomheter som legges til senere får
også hver sin stille baseline. Bruk `alert_on_update = true` for senere endringer.

| Overvåket felt | Varsling |
| --- | --- |
| Virksomhetsnavn og register-ID | Før og etter |
| Aktiv tillatelsesliste | Tilkommet eller ikke lenger i listen |
| Rolle og innehaver | Egen tillatelse, agent eller filial holdes atskilt |
| Tjenester og instrumenter | Endring i registrert utvalg; tjenesteliste før og etter |
| Sikkerhetsstillelse, registreringsdato og merknader | Endret verdi |
| Klassifisering | Endring i registerets klassifisering |
| Virksomhet ikke funnet | Observasjon fra et fullført søk, med forbehold i varselet |

En aktiv tillatelse kan tilhøre en annen virksomhet som den overvåkede virksomheten
er agent eller filial for. Varslet viser derfor både rolle og innehaver. At en
virksomhet eller tillatelse ikke lenger finnes i svaret er **ikke i seg selv et
vedtak om tilbakekall**. Journalisten må kontrollere årsaken hos originalkilden.
Lenken i varselet åpner det aktuelle, maskinlesbare registersvaret.

Alle sider i det avgrensede søket leses før et resultat godtas. Organisasjonsnummer
må samsvare nøyaktig; fritekstsøkets øvrige treff regnes ikke som den valgte
virksomheten. Endret total under paginering, duplikater, ugyldige felt, avbrudd og
overskredet sidegrense gir kildefeil og bevarer tidligere state. `max_pages` kan
settes fra 1 til 20, med 50 treff per side. En tom eller manglende tillatelsesliste
skilles: `[]` er et eksplisitt tomt utvalg; `null` eller et manglende felt avvises.
Rekkefølgeendringer alene gir ikke varsler.

Adapteren følger ikke hele registeret, utenlandske virksomheter uten norsk
organisasjonsnummer, nye selskaper utenfor utvalget, personroller, adresser,
agentnettverk eller grensekryssende aktivitet. Den er heller ikke et historisk
vedtaksarkiv. Registeret viser aktive tillatelser under hvilke virksomheten kan
tilby tjenester. Finansnyheter og rundskriv følges separat gjennom RSS-profiler.

Grunnlag: [Finanstilsynets API-oversikt](https://www.finanstilsynet.no/analyser-og-statistikk/api-for-apne-data/)
og [den offisielle API-beskrivelsen](https://api.finanstilsynet.no/registry/index.html).
