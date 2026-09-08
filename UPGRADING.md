# Versjoner og oppgraderinger

`python -m watchtower --version` viser kodeversjon og støttet konfigurasjonsformat.
`[general] config_version = 1` er det første eksplisitte formatet. Eldre filer uten
feltet tolkes fortsatt som format 1. Ukjente fremtidige formatversjoner avvises.

## Fra 0.4 til 0.5

Eksisterende kilde-ID-er, filtre, eksplisitte identifikatorlister, baseline og
tidsstyring beholdes. Virksomhetslisten og oppsettsveiviseren er valgfrie tillegg.
Du trenger ikke kjøre veiviseren eller bytte konfigurasjon i en aktiv installasjon.

State får ekstra felt for hentet antall og dekningsmarkeringer. Audit får lesbare
metadata og en egen fil for siste varselrunde. Gammel state og gammel audit kan
fortsatt leses. Doffin viser nå begrensninger ved fulle resultatvinduer; RSS avviser
også svar der bare enkelte elementer mangler nødvendige felt.

Nye profiler aktiveres ikke i eksisterende runtimes. Velg dem uttrykkelig og
kontroller første stille baseline. Startpakkene er ikke migreringsverktøy.

## Slik oppgraderer en fork

1. Noter kjent fungerende kodecommit og runtime-configcommit. Behold privat state.
2. Hent ønskede upstream-endringer til en egen branch i din fork.
3. Les changelog og endringer i konfigurasjon, workflow og secrets.
4. Kjør prosjektets kontroller og valider din runtime lokalt. En `dry-run` med en
   kopi av state kan kontrollere henting uten sending eller state-endringer.
5. Merge gjennomgått kode til din forks `main` og følg de første kjøringene.

Ved problemer: lag en vanlig revert av kodeendringen på `main`. Ikke force-push
eller slett runtime-state. Hvis du har tatt i bruk `entity_refs`, må de utvides
til vanlige kildefelter og tekstfiltre før du går tilbake til 0.4; den gamle koden
forstår ikke referansene. Bruk eventuelt den bevarte konfigurasjonen som grunnlag.

## Utgivelsesrutine for vedlikeholder

Oppdater versjon i `pyproject.toml` og `watchtower/__init__.py`, skriv changelog,
kjør CI, og merge en gjennomgått PR. Opprett deretter en tag og GitHub Release fra
den konkrete godkjente commiten, for eksempel `v0.5.0`. Beskriv støttet
konfigurasjonsformat, migrering, kontrollert kildedekning og kjente begrensninger.

En versjon i kildekoden er ikke alene en publisert GitHub Release. Utgivelser er
valgfrie for mottakerne; ingen automatisk oppdatering eller sentral runtime-kobling
innføres. Formell lisens og rettighetshaver må avklares før bred distribusjon.
