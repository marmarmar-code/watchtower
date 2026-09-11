# Automatisk oppdagelse av stortingsvoteringer — 2026-09-11

Kilden bruker bare Stortingets åpne data. Den leser hele kjeden `sesjoner` → `moter` → `dagsorden` → `voteringer`. Møtedatoen avgrenser et rullende vindu. Genereringstidene i XML påvirker ikke overvåkede felt.

Verifisert konfigurasjon:

```toml
kind = "parliament_vote_discovery"
lookback_days = 30
max_sessions = 2
max_meetings = 40
max_cases = 200
max_records = 1000
max_bytes = 5000000
allow_empty = true
events = ["added", "changed"]
urls = []
```

Vinduet 12. august–11. september 2026 lå i sesjonen 2025–2026. Det inneholdt ett møte med gyldig ID. Dagsordenen ga én saks-ID, og voteringslisten for saken var gyldig tom. To fullstendige avlesninger ga null poster, null varsler og identiske snapshots. Scope-hashen var `19e3b3cedf5e035b298dcb46e26b472ff5e2b1abf15583d6f837226457d32b23`.

Et separat offentlig historisk uttrekk for sak 200314 inneholder 31 voteringer, fra ID 28470 til 28500. Det brukes som portabel positiv test. Den siste voteringen er ikke personlig og bruker kildens verdi `-1` for stemmetall. Adapteren godtar denne verdien bare når `personlig_votering` er `false`, og lagrer tallene som ikke oppgitt. Andre negative stemmetall avvises.

Stabil identitet er `votering_id`. Offisielle uttrekk viste at samme ID kan være knyttet til flere saker. Adapteren samler derfor sorterte saker under én votering bare når tema, utfall, stemmetall, resultattype og tidspunkt er identiske. Ulikt faglig innhold for samme ID gir feil. Saks-ID-er og sakstitler er varselkontekst utenfor feltene som sammenlignes. Når en relatert sak faller ut av møtevinduet, endres derfor ikke voteringsfingerprintet og det sendes ikke et endringsvarsel.

Sesjonene må samlet dekke hele vinduet uten hull. Hele møtelisten leses for hver valgt sesjon, deretter hele dagsordenen for hvert valgt møte og hele voteringslisten for hver unik sak. Møtevinduet oppdager altså saker; adapteren beholder alle voteringene som Stortinget returnerer for disse sakene, også når selve voteringstidspunktet er eldre enn vindusstart. Grenser kontrolleres før neste oppslagsnivå. Feil ekko-ID, ugyldige typer eller datoer, duplikater innen én liste, manglende sesjonsdekning og overskredne grenser avviser uttrekket. Første snapshot er stille. Sletting støttes ikke fordi møter faller ut av det rullende vinduet. `published` er tomt; voteringstid beholdes som en kildeopplysning.

Testfilen normaliserer kildens CRLF-linjeskift til LF. JSON-beviset skiller mellom råsvarets hash og den lagrede testfilens hash; XML-innholdet er uendret.
