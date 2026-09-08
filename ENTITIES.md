# Én privat virksomhetsliste

Legg virksomheter én gang i `config/watchtower.toml`, før kildeblokkene. Listen
brukes bare av kilder som uttrykkelig refererer til den. Den henter ikke automatisk
datterselskaper, adresser eller andre relasjoner.

```toml
[[entity]]
id = "example"
name = "Eksempelvirksomhet"
aliases = ["Eksempelmerke"]
# orgnr = "et gyldig ni-sifret organisasjonsnummer"
isins = []
```

`id` er en privat referanse, `name` er navnet som skal finnes i tekst, og `aliases`
er alternative navn. `orgnr` og `isins` er valgfrie, men kreves når en valgt
registeradapter trenger dem. Organisasjonsnumre kontrolleres med kontrollsiffer;
ISIN kontrolleres for format. Ingen av kontrollene beviser at virksomheten eller
verdipapiret finnes i det aktuelle registeret.

## Navn i nyheter, RSS og kunngjøringer

```toml
[[source]]
id = "government"
kind = "regjeringen"
interval_minutes = 60
[source.filter]
entity_refs = ["example"]
include_any = ["eksempeltema"]
exclude_any = []
```

`filter.entity_refs` føyer navn og aliaser til `include_any`. Treff på tema **eller**
virksomhetsnavn er nok, mens `include_all` og `exclude_any` fortsatt gjelder.
Organisasjonsnummeret blir ikke automatisk et tekstfilter. Tekstfiltre søker bare
i innholdet adapteren faktisk henter; de utvider ikke kildens søkevindu.

## Identifikatorer i registre

Legg `entity_refs = ["example"]` direkte i kildeblokken for følgende koblinger:

| Adapter | Fra virksomhetslisten | Effektivt kildefelt |
| --- | --- | --- |
| `brreg` | `orgnr` | `companies` |
| `patentstyret` | `orgnr` | `companies` |
| `stotte` | `orgnr` | `recipient_orgnrs` – mottakere, ikke støttegivere |
| `finanstilsynet_short_sale` | `isins` | `isins` |

```toml
[[source]]
id = "company_register"
kind = "brreg"
enabled = false
# Fyll inn gyldig orgnr på entity før disse linjene aktiveres:
# entity_refs = ["example"]
events = ["company", "roles", "annual_accounts"]
[source.filter]
match_all = true
```

Eksisterende eksplisitte identifikatorlister beholdes og kombineres med referansene.
Ingen automatisk kobling fra navn til børsmeldinger, Doffin-kjøpere eller
Finanstilsynets virksomhetsregister er implisitt i dette formatet.

Ukjente referanser, duplikate ID-er og manglende nødvendige identifikatorer avvises
ved validering. Behold eksisterende kilde-ID-er ved migrering, så samme state brukes.
Endret kildeutvalg eller utvidede lister kan gi nye treff; kontroller med `dry-run`.

Listen ligger i den samme private filen som filtrene og omfattes av workflowens
maskering og kontroll mot den offentlige kodebasen. Bruk syntetiske verdier i offentlige feilrapporter.
