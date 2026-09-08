# Sett opp din egen installasjon

En installasjon består av din offentlige kodefork og ett privat runtime-repo.
Du eier kontoer, nøkler, kildevalg, drift og oppgraderinger. Velg en driftsansvarlig,
en redaksjonell ansvarlig og en stedfortreder før oppstart. Se [ansvarsmodellen](SUPPORT.md).
Bruksrett må være avklart med rettighetshaveren mens prosjektet mangler lisens.

## 1. Opprett repoene

Fork [Watchtower](https://github.com/marmarmar-code/watchtower) til din egen konto eller
organisasjon. Velg **Use this template** på
[runtime-malen](https://github.com/marmarmar-code/watchtower-runtime-template) og opprett
et separat **privat** repo. Gi hver installasjon sitt eget runtime-repo.

## 2. Lag overvåkingsoppsettet lokalt

Du trenger Git og Python 3.11 eller nyere. Klon kodeforken og den private runtimen
til **to separate kataloger ved siden av hverandre**, for eksempel `watchtower` og
`watchtower-runtime`. Kjør fra kodekatalogen:

```bash
python3 -m venv ../watchtower-venv
. ../watchtower-venv/bin/activate
python -m pip install .
python -m watchtower setup --runtime ../watchtower-runtime
```

På Windows aktiveres miljøet med `..\watchtower-venv\Scripts\Activate.ps1`.
Veiviseren spør etter startpakke, temaer, virksomheter og kanal. Teams er standard.
For engelske feeder må søkeordene også dekke engelske uttrykk.

| Startpakke | Innhold |
| --- | --- |
| `general` | Regjeringen, Stortinget og Konkurransetilsynet |
| `finance` | Grunnpakken, to separate Finanstilsynet-feeder og Norges Banks pressemeldinger |
| `health` | Grunnpakken, EMA-nyheter og nye humanlegemidler |

Finanspakken bruker én nyhetsfeed og rundskriv fra Finanstilsynet. Den andre
nyhetsfeeden finnes fortsatt i katalogen, men er utelatt fra startpakken fordi
den overlapper med den første. Separate kilder dedupliserer ikke mot hverandre.

Alle pakker legger til BRREG når du oppgir virksomheter med gyldig organisasjonsnummer.
Finanspakken legger da også til [Finanstilsynets virksomhetsregister](FINANSTILSYNET.md).
Pakkene er avgrensede utgangspunkt; de er ikke full sektorovervåking. Doffin og
Patentstyret legges til etter at du har fått egne kildenøkler.

For skript kan `--preset`, gjentatt `--topic`, gjentatt `--company ORGNR=NAME` og
`--channel` brukes. Skriv private overvåkingsverdier bare på en maskin du kontrollerer;
ikke bruk dem som offentlige workflow-inputs. Interaktivt oppsett unngår å legge
søkeord i shell-historikken.

Veiviseren skriver gyldig `config/watchtower.toml` og lager én kilde per RSS-feed.
Et tidligere oppsett med bare deaktiverte kilder bevares først som
`config/watchtower.before-setup.toml`. Aktive oppsett og runtimes med state avvises.
Veiviseren oppretter ikke GitHub-repoer, nøkler eller en varslingskanal.

Se over filen, og kjør:

```bash
python -m watchtower validate-runtime ../watchtower-runtime
python -m watchtower validate-config --config ../watchtower-runtime/config/watchtower.toml
```

Commit og push konfigurasjonen fra **den private runtime-katalogen**. Les
[virksomhetslisten](ENTITIES.md) hvis navn og organisasjonsnumre skal gjenbrukes i flere kilder.
Du kan også redigere den deaktiverte malen manuelt uten veiviseren.

## 3. Koble til privat runtime

Installer [GitHub CLI](https://cli.github.com/), og logg inn med `gh auth login`.
Du trenger administratortilgang til begge repoene. Kjør først kontrollen, og bruk
deretter samme kommando med `--apply` for å lagre koblingen:

```bash
python -m watchtower link-github --code-repo DIN_EIER/DIN_KODEFORK --runtime-repo DIN_EIER/DITT_RUNTIME_REPO
python -m watchtower link-github --code-repo DIN_EIER/DIN_KODEFORK --runtime-repo DIN_EIER/DITT_RUNTIME_REPO --apply
```

Oppsettet kontrollerer offentlig kodefork, privat runtime, nødvendige filer og
administratortilgang. Det setter `WATCHTOWER_RUNTIME_REPOSITORY` og
`WATCHTOWER_RUNTIME_REF`, lager en egen skrivbar deploy-nøkkel til runtimen og
lagrer privatnøkkelen som `RUNTIME_DEPLOY_KEY` i kodeforkens Actions Secrets.
Bruk `--runtime-ref BRANCH` hvis runtime-konfigurasjonen ligger på en annen branch.
Kommandoen leser ikke den private overvåkingslisten inn i offentlig kode.

Nøkkelfilene lages i en midlertidig mappe utenfor repoene og fjernes etterpå.
Secret-innhold sendes til `gh` på standard input og skrives ikke til konsollen.
[GitHub CLI krypterer secrets før opplasting](https://cli.github.com/manual/gh_secret_set).
En eksisterende nøkkel beholdes, og en eksisterende kobling til et annet repo
eller en annen branch avvises. Det er ingen automatisk nøkkelrotasjon.

Kjør kommandoen på nytt etter et avbrutt oppsett. Ved feil under secret-lagringen
forsøker den å fjerne bare deploy-nøkkelen den nettopp opprettet. Følg feilmeldingen
hvis GitHub ikke kunne bekrefte lagring eller opprydding. Siden private secrets
ikke kan leses tilbake, beviser ikke kontrollen at en eksisterende secret passer
til deploy-nøkkelen; det avklares av installasjonskontrollen i steg 5.

Manuelt alternativ: sett de to Actions-variablene i kodeforken, lag et eget
Ed25519-nøkkelpar uten passord utenfor repoene, legg offentlig nøkkel i runtimens
**Deploy keys** med **Allow write access**, og legg privatnøkkelen i kodeforkens
`RUNTIME_DEPLOY_KEY`. Ikke gjenbruk en nøkkel mellom installasjoner.
[GitHub dokumenterer deploy-nøkler og nødvendig tilgang her](https://docs.github.com/en/rest/deploy-keys/deploy-keys).
Nøkler opprettet med en brukertoken kan påvirkes når tokenen tilbakekalles;
kontroller runtime-tilgangen ved slike endringer.

## 4. Koble varslingskanalen

For Teams: opprett en Workflow med webhook-trigger for ønsket kanal, legg til
en medeier, og lagre adressen som `TEAMS_WEBHOOK_URL` i kodeforkens Actions Secrets.
For Slack: velg `provider = "slack"` og bruk `SLACK_WEBHOOK_URL`.
Se [Microsofts oppskrift](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook).

| Secret | Når den trengs |
| --- | --- |
| `RUNTIME_DEPLOY_KEY` | Alle installasjoner med privat runtime |
| `TEAMS_WEBHOOK_URL` eller `SLACK_WEBHOOK_URL` | Valgt kanal |
| `DOFFIN_API_KEY` | Når Doffin aktiveres |
| `PATENTSTYRET_API_KEY` | Når Patentstyret aktiveres |

## 5. Kontroller at installasjonen faktisk virker

Åpne **Actions** i kodeforken og aktiver workflows hvis GitHub ber om det.
Kjør monitor-workflowen manuelt på `main`, i denne rekkefølgen:

1. `test-notification`: se at testvarselet faktisk vises i riktig kanal.
2. `dry-run`: kontroller henting og konfigurasjon. Denne skriver ikke baseline.
3. `run`: etabler stille baseline. Historiske treff skal ikke sendes som nye varsler.
4. Start **Watchtower scheduler** manuelt dersom kjeden ikke allerede går.
5. Se at en senere automatisk monitor-kjøring oppdaterer privat kildestatus.
6. Kontroller også en naturlig `schedule`-hendelse, slik at cron-reserven er prøvd.

Et vellykket testvarsel beviser kanaltilgang, og en vellykket henting beviser at
kilden svarte. Ingen av delene beviser full redaksjonell dekning. Sjekk et lite
utvalg kjente publiseringer mot kildevalgene og filtrene.

## Drift og oppdateringer

Bruk [driftsveiledningen](OPERATIONS.md) ved kildefeil, begrenset dekning eller
manglende varsler. [Oppgraderinger](UPGRADING.md) beskriver kompatibilitet og retur
til kjent kode. Ingen fork får automatiske upstream-oppdateringer.

Intervallene er omtrentlige. GitHub kan forsinke eller droppe planlagte kjøringer,
og offentlige repoers planer kan deaktiveres etter inaktivitet. En installasjon som
trenger en garanti for manglende livstegn må ha en uavhengig kontroll eid av
installasjonseieren. Se [GitHubs schedule-dokumentasjon](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
