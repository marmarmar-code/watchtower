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
```

Hver kilde er valgfri. Aktivitet, URL-er, kildespesifikke valg og filterregler angis i privat runtime.

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
enabled = true
alert_on_update = true
companies = ["999999999"]
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
python -m watchtower test-notification --config <watchtower.toml>
python -m watchtower dry-run --config <watchtower.toml> --state-dir <state-katalog>
python -m watchtower run --config <watchtower.toml> --state-dir <state-katalog>
```

Første ordinære kjøring av en ny kilde er en stille baseline. En `dry-run` sender ikke ordinære varsler og skriver ikke state.

## Lokal kontroll

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m pip check
python -m compileall -q watchtower tests scripts
python scripts/check_public_safety.py
python -m unittest discover -s tests -v
```

## Oppdateringer og bidrag

Hver fork eier sin egen drift, secrets, runtime og lokale kodeendringer. Upstream gir ingen sentral driftsgaranti eller plikt til å utvikle særtilpasninger.

Generelle endringer kan foreslås som pull requests. Se `CONTRIBUTING.md` og `SUPPORT.md` før en endring sendes.

## Lisensstatus

Det er foreløpig ikke lagt inn en programvarelisens. Offentlig tilgjengelig kildekode gir derfor ikke i seg selv generell tillatelse til bruk, endring eller videre distribusjon.

En ny installasjon må ha uttrykkelig tillatelse fra rettighetshaveren fram til rettighetshaver og lisens er formelt avklart.
