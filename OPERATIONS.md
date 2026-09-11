# Drift av en selvstendig installasjon

Installasjonseieren følger opp Actions, nøkler og kildefeil. Den faglige eieren
vedlikeholder overvåkingslisten og vurderer treff. Ha en stedfortreder med nødvendig
GitHub-tilgang og en medeier av Teams Workflow. Upstream overvåker ikke installasjonen.

## Les status lokalt

Kjør fra kodekatalogen med en oppdatert lokal kopi av privat runtime:

```bash
python -m watchtower --version
python -m watchtower status --config ../watchtower-runtime/config/watchtower.toml --state-dir ../watchtower-runtime/state
```

Status viser sist vellykkede henting, intervall, antall hentede elementer og
registrerte begrensninger. Den kontakter ingen eksterne kilder.

| Status / kode | Betydning og tiltak |
| --- | --- |
| `IKKE STARTET` | Ingen registrert vellykket henting. Fullfør oppsett/baseline. |
| `FORSINKET` | Sist henting er eldre enn intervallet pluss 15 minutters slingringsmonn. Se Actions og scheduler. |
| `FEIL` | Siste forsøk feilet. Undersøk privat `_status.json`, tilgang og kildeformat. |
| `result_window_full` | Doffin fylte siste tillatte side. Eldre treff kan ligge utenfor vinduet. Avgrens søket eller øk `max_pages` bevisst, opptil 5. |
| `no_overlap_with_previous_window` | Et fullt Doffin-søk gjenfant ingen tidligere lagret ID. Undersøk mulig hull etter avbrudd eller endret søk. |

Doffin-markeringene stanser ikke varsling av hentede treff. De vises også i den
anonymiserte Actions-oppsummeringen som `coverage_limited`. En slik markering er
et dekningsvarsel, ikke en HTTP-feil, og gir ikke gjentatte driftsmeldinger til
kanalen. `status` returnerer fortsatt 0 når den tekniske driften er frisk, men
viser `LIMITED COVERAGE`. Feil eller forsinkelse gir returkode 2.

`ingen registrert begrensning` er ikke en garanti for fullstendighet. RSS-feeder har
ofte korte historiske vinduer, Doffin kontrollerer bare konfigurert vindu, Euronext
følger valgte utstedersider, og SSB-adapteren leser tabellbeskrivelser uten selve tallene.
En vellykket henting beviser heller ikke at kildens publiserte data er ferske.

## RSS-feil

Nye startpakker bruker én kilde per feed. I eksisterende konfigurasjoner med flere
feeder i samme blokk vil én feil fortsatt stoppe hele blokken. En oppdeling lager
nye kilde-ID-er og dermed nye, stille baselines. Gjør dette som en kontrollert endring.

En tom feed er som standard feil. Hvis en bestemt feed lovlig er tom, sett
`allow_empty = true` på denne RSS-kilden. Ugyldig XML og elementer uten nødvendig
identitet eller tittel blir fortsatt avvist. Første gyldige, tomme baseline gjør
at senere publiseringer kan varsles.

## Les varselhistorikken

```bash
python -m watchtower history --state-dir ../watchtower-runtime/state --limit 20
python -m watchtower history --state-dir ../watchtower-runtime/state --latest
```

`_alert_audit.json` beholder de siste 500 registrerte varslene. `_latest_alerts.json`
beholder hele den siste runden med varsler, også når detaljutsending ble erstattet
med en oppsummering. Listen byttes først ut ved neste varselrunde, ikke ved en tom kjøring.

Fra 0.5 lagres tittel, original lenke, publiseringsdato når tilgjengelig, trefford,
endringsdetaljer og en stabil `alert_id`. `delivery` angir `detail` eller `summary`.
`sent_at` betyr at webhook-kallet returnerte uten feil, ikke at et menneske har lest
varselet. Eldre audit-rader beholdes og mangler de nye feltene.

## Gjenoppretting etter varslingsfeil

Fra 0.5 lagres hele leveringsplanen i privat `state/_outbox.json` før utsending.
Hver meldingspakke får en kvittering etter at webhooken har svart uten feil.
Ved neste ordinære `run` behandles en uferdig kø først: pakker med kvittering
hoppes over, resten sendes, og kilde-state og historikk ferdigstilles. Denne
kjøringen henter ikke nye kildeelementer; neste kjøring gjenopptar overvåkingen.
`dry-run` sender ikke køen og endrer ingen filer.

`status` viser `pending_batches` og `pending_delivery`. Sistnevnte kan være 1
selv når alle pakkene er sendt, hvis lagring av state/historikk gjenstår. Status
er da `NEEDS ATTENTION` og returkode 2. Bruk siste Actions-kjøring til å finne
om feilen gjelder kanaltilgang eller lagring. Etter retting kjører du `run` igjen.
Det kreves ingen separat database eller manuell flytting av meldinger.

Ikke slett eller rediger en ventende kø som generell feilretting. Behold samme
varslingskanal til køen er tømt. Bytte av leverandør (Teams/Slack) avvises mens
køen er aktiv; bytte av webhook innen samme leverandør kan sende resten til den
nye kanalen. Stans og vurder køinnholdet lokalt hvis en kilde deaktiveres mens
meldinger fra den allerede ligger klare. Kjør bare én skriver mot samme runtime.

Dette gir gjenoppretting etter ordinære delvise feil, ikke nøyaktig én levering.
Hvis webhooken mottar meldingen, men svaret går tapt, kan et nytt forsøk gi en
kopi. Det samme gjelder krasj etter sending og før lokal kvittering, eller tap av
GitHub-runneren før privat state er pushet. Workflowen forsøker å lagre state
også etter en vanlig kjøringsfeil; kansellering, timeout eller push-feil kan
fortsatt hindre det. En stabil `alert_id` hjelper med å kjenne igjen kopier.
`delivery_id` gjør ferdigstilling av samme kø idempotent i den lokale historikken.

Køen inneholder private varseltekster og planlagt state. Den hører bare hjemme i
den private runtimen. Historikken beholder de siste 500 varslene og hele siste
varselrunde; dette er ikke et komplett dokumentarkiv. Git-historikken kan beholde
eldre private køversjoner selv etter at den aktive køen er tømt.

## Når tidsplanen stopper

Kontroller siste monitor, siste scheduler og privat `last_checked_at`. En kansellert
scheduler kan være normal når en etterfølger overtar. Start scheduler manuelt ved
behov, og kontroller også at en senere naturlig cron-hendelse virker.

En statuskommando inne i samme workflow kan ikke varsle om at workflowen aldri
starter. Installasjoner med krav til dette må ha en uavhengig kontroll som lokal
driftsansvarlig eier. Den eksisterende scheduler-kjeden bruker runner-tid også mens
den venter; vurder kostnader før den eventuelt flyttes til et privat koderepo.

## Før du ber om hjelp

Kontroller kanal, secrets, runtime-kobling og siste kildefeil. Del bare versjon,
anonymiserte totaltall, feiltype og et syntetisk eksempel i offentlige issues.
`--redact-output` kan brukes på `status`, `history`, `run` og `dry-run`. Søkeverdier,
private kilde-ID-er og urenset varselhistorikk skal ikke deles offentlig.
