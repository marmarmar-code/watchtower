# Watchtower

Deterministisk overvåkingsmotor for offentlige datakilder.

Watchtower skiller offentlig programkode fra privat konfigurasjon og state. Ingen språkmodell er nødvendig i drift.

## Struktur

```text
watchtower           offentlig kode, adaptere, tester og workflow
watchtower-runtime   privat konfigurasjon, overvåkingsverdier og state
```

Opprett en privat runtime fra [watchtower-runtime-template](https://github.com/marmarmar-code/watchtower-runtime-template). Malen inneholder full oppstarts- og kontrollprosedyre.

## Kilder

Motoren inneholder adaptere for:

```text
regjeringen
stortinget
konkurransetilsynet
euronext
doffin
hoyesterett
brreg
rss
ssb
```

Hver kilde er valgfri. Aktivitet, URL-er, kildespesifikke valg og filterregler angis i privat runtime.

Vis den offentlige kildekatalogen med status, tilgangskrav og vedlikeholdsansvar:

```bash
python -m watchtower list-sources
```

Statusen beskriver hvor moden adapteren er i prosjektet. Den er ikke en bekreftelse på at den eksterne kilden fungerer akkurat nå; hver fork må følge opp sine aktive kilder.

### RSS og Atom

`rss` gjør vanlige offentlige RSS- og Atom-feeder tilgjengelige uten ny adapterkode. Flere feeder kan samles i én kilde, og ordinære private filterregler avgjør hva som varsles.

Følgende offisielle profiler følger med og var kontrollert 27. august 2026:

| Profil | Innhold |
| --- | --- |
| `politiloggen` | Operative meldinger fra Politiloggen |
| `finanstilsynet` | Nyhetsarkiv, rundskriv og nyheter |
| `mattilsynet` | Offentlig RSS-innhold fra Mattilsynet |
| `norges_bank_pressemeldinger` | Pressemeldinger fra Norges Bank |

Vis den maskinlesbare profillisten med `python -m watchtower list-rss-profiles`. Profilene gjør oppsettet enklere, men hver fork må fortsatt følge med på om den eksterne eieren endrer eller avvikler en feed.

```toml
[[source]]
id = "politiloggen"
kind = "rss"
label = "Politiloggen"
enabled = false
profiles = ["politiloggen"]
interval_minutes = 15

[source.filter]
include_any = ["REPLACE_ME_TOPIC_1"]
exclude_any = []
```

Egne feed-URL-er kan fortsatt legges i `urls` i stedet for eller sammen med profiler.

### SSB

`ssb` følger nye perioder og strukturendringer i en eksplisitt liste med femsifrede tabellnumre fra Statistikkbanken. Adapteren bruker SSBs åpne PxWebApi v2 og henter bare tabellbeskrivelsen, ikke selve tallmaterialet.

```toml
[[source]]
id = "ssb"
kind = "ssb"
label = "Statistisk sentralbyrå"
enabled = false
tables = ["REPLACE_ME_SSB_TABLE_1"]
interval_minutes = 360

[source.filter]
match_all = true
exclude_any = []
```

### BRREG

`brreg` kan overvåke en eksplisitt liste med organisasjonsnumre for:

- nye årsregnskap;
- navn, organisasjonsform og næringskode;
- konkurs, avvikling, sletting og fjerning;
- daglig leder, styreleder, nestleder og styremedlemmer.

Første kjøring etablerer en stille baseline. Adapteren lagrer kompakte snapshots i privat state for å beskrive konkrete endringer. Den laster ikke ned PDF-er og inneholder ingen database eller rapporteringsflate.

Eksempel:

```toml
[[source]]
id = "brreg"
kind = "brreg"
label = "Brønnøysundregistrene"
enabled = false
alert_on_update = true
companies = ["REPLACE_ME_ORGNR_1"]
events = ["annual_accounts", "company", "roles"]

[source.filter]
match_all = true
exclude_any = []
```

## Filtrering

En aktiv kilde må ha positive filterregler:

```toml
[source.filter]
include_any = ["eksempel"]
include_all = []
exclude_any = []
match_mode = "smart"
```

eller eksplisitt:

```toml
[source.filter]
match_all = true
```

`match_all` bør bare brukes når adapteren allerede er begrenset av en konkret liste. Aktive kilder med `REPLACE_ME` eller uten positive regler blir avvist før overvåking starter.

## Varsling

Støttede providere:

```toml
[notifications]
provider = "teams"
```

eller:

```toml
[notifications]
provider = "slack"
```

Teams bruker en Workflow-webhook lagret som Actions-secret `TEAMS_WEBHOOK_URL`. Slack bruker `SLACK_WEBHOOK_URL`.

Varsler presenteres separat for hver provider. Teams får Adaptive Cards med native lenkeknapper; Slack får Slack-formatert tekst. Store treffmengder deles i avgrensede meldinger.

## Runtime-kontrakt

En runtime skal bare inneholde:

```text
README.md
.gitignore
config/watchtower.toml
state/
```

Credentials skal ligge i GitHub Actions Secrets, ikke i runtime. Workflowen maskerer private konfigurasjonsverdier, kontrollerer dem mot den offentlige kodebasen og nekter å committe filer utenfor `state/`.

Dersom fork og privat runtime har samme eier og runtime heter `watchtower-runtime`, finner workflowen repositoryet automatisk. Andre plasseringer angis med Actions-variabelen:

```text
WATCHTOWER_RUNTIME_REPOSITORY=<eier>/<repository>
```

## Kommandoer

```bash
python -m watchtower validate-runtime <runtime-katalog>
python -m watchtower validate-config --config <watchtower.toml>
python -m watchtower list-sources
python -m watchtower list-rss-profiles
python -m watchtower status --config <watchtower.toml> --state-dir <state-katalog>
python -m watchtower test-notification --config <watchtower.toml>
python -m watchtower dry-run --config <watchtower.toml> --state-dir <state-katalog>
python -m watchtower run --config <watchtower.toml> --state-dir <state-katalog>
```

Første ordinære kjøring av en ny kilde er en stille baseline. En `dry-run` sender ikke ordinære varsler og skriver ikke state.

`status` kontakter ingen eksterne kilder og endrer ikke state. Den viser hvilke aktive kilder som nylig er kontrollert, er forsinket, har feil eller ennå ikke er startet. Workflowen skriver bare en anonymisert totalsum til den offentlige Actions-oppsummeringen; kilde-ID-er og private filtre blir ikke publisert.

## Lokal kontroll

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m pip check
python -m compileall -q watchtower tests scripts
python scripts/check_public_safety.py
python scripts/check_source_catalog.py
python -m unittest discover -s tests -v
```

## Fork-eid drift og oppdateringer

Hver redaksjon forker den offentlige koden og eier deretter sin egen kode, GitHub Actions, secrets, private runtime, adapterendringer, drift og support. Upstream er et startpunkt, ikke en sentral tjeneste: det finnes ingen SLA, sentral avhengighet eller garanti for at en kildekodeendring passer i din installasjon.

Det skjer ingen automatiske oppdateringer fra upstream. Redaksjonen velger selv om og når den vil hente inn en endring, vurderer den i sin fork og ruller den ut på eget ansvar. Lokale adapterendringer bør normalt bli i forken, med mindre de er generelle og har en navngitt vedlikeholder.

Generelle endringer kan foreslås som pull requests. Se `CONTRIBUTING.md` og `SUPPORT.md` før en endring sendes.

Se [FORKING.md](FORKING.md) for en kort ansvars- og oppdateringsmodell.

## Lisensstatus

Det er foreløpig ikke lagt inn en programvarelisens. Offentlig tilgjengelig kildekode gir derfor ikke i seg selv generell tillatelse til bruk, endring eller videre distribusjon.

En ny installasjon må ha uttrykkelig tillatelse fra rettighetshaveren fram til rettighetshaver og lisens er formelt avklart.
