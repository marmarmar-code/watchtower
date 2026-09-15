# Isolert kilde- og adaptertest

`examples/catalog-test-runtime.yml` er en mal for periodisk testing i et eget
privat repository. Produksjonskonfigurasjon, historikk og hemmeligheter skal
holdes separat. Malen bruker testrepoets eget `GITHUB_TOKEN` og bare en egen
test-webhook. Detaljerte driftsrapporter og kvitteringer hører hjemme i testrepoet.

## Utvalg og oppsett

Katalogen har 64 oppsett. `examples/adapter-test-sources.toml` tilfører elleve
avgrensede, nøkkelfrie adapterkonfigurasjoner. Sammen gir disse 75 oppsett og
16 av 18 adaptertyper. `ADAPTER_COVERAGE.md` skiller typer, utvalg, faktisk henting
og kontrollerte hendelsestester. Doffin og Patentstyret krever egne tilgangsnøkler.

Malen har 15-minutters tidsplan. Tidsplan- og requestkjøringer respekterer
kildenes intervaller; en uttrykkelig workflow_dispatch-run kontrollerer alle kilder. Den er
bundet til ett bestemt privat testrepo og `main`, og validerer nøyaktig de
forventede kilde-ID-ene fra den festede offentlige katalog- og adapterversjonen.
Ny kildekode må gjennomgås og kontrolleres før revisjonen oppdateres.

Nye kilde-ID-er får stille førstegangsbaseline ved ordinær `run`. Eksisterende
kildekonfigurasjon og state skal bevares når nye kilder legges til. En endret
snapshot-selektor eller URL regnes som et nytt kildeomfang; det skal ikke brukes
som en skjult måte å førstegangsbaselines eksisterende kilder på nytt.

## Kø, sending og lagring

Kjøringer serialiseres. En request valideres mot sin opprinnelige commit før
jobben henter og fast-forwarder til nyeste `main`. En jobb som har ventet i kø
bruker dermed den siste lagrede historikken. Requestfilen kan bare inneholde
`operation`; pushen må være én direkte commit som bare endrer denne filen.
Sletting gjør ingen kildehenting eller sending.

Etter kodeinstallasjon og validering lagres et kontrollpunkt i
`state/_workflow_checkpoint.json` før `run`. Senderen og sluttlagringen krever
at samme jobb har fått kontrollpunktet lagret på GitHub. En jobb som finner et
eldre uløst kontrollpunkt stopper før sending og kan ikke fjerne det gjennom en
`always()`-gren. Kontrollpunktet fjernes sammen med resultatene og kvitteringene
i den avsluttende state-committen. Vanlige kilde- og leveringsfeil gjør jobben
rød etter at lagring er forsøkt.

Vedvarende pushfeil eller tapt runner lar kontrollpunktet bli stående. Den
berørte kjøringen må undersøkes og tilgjengelig kø/kvittering avstemmes før
uttrykkelig gjenoppretting. Kontrollpunktet skal aldri bare slettes, historikken
skal aldri erstattes av ny baseline, og testdata skal aldri kopieres til
produksjon. Ved fullstendig runner-tap kan lokale kvitteringer være borte. Dette
er et synlig stopp med usikker leveringsstatus, ikke garanti om full
gjenoppretting eller nøyaktig én webhooklevering.

## Verifikasjon

Daterte rapporter i `source_checks/` dokumenterer faktiske offentlige hentinger,
parserresultater, antall og identiteter. De beviser ikke at alle hendelsestyper
har inntruffet eller at alle kilder har sendt varsler.

`tests/test_catalog_workflow.py` kjører workflowens faktiske shellblokker mot
midlertidige lokale Git-repoer. Sju tester dekker kølagt checkout, requestgate,
kontrollpunkt-eierskap, kontrollpunkt etter tapt runner, feilet push og avvisning
av baseline over eksisterende state før noe kontrollpunkt opprettes.
56 øvrige målrettede kodetester dekket kildeoppsett, delvis kildefeil, timeout,
HTTP 429, køgjenopptaking og ugyldige kvitteringer. Kontrollerte feiltester er
ikke observerte driftsfeil.

En installasjon må føre egen, privat evidens for naturlige `schedule`-hendelser,
faktisk kildehenting, kjøretid, vedvarende state, kø og kvitteringer. Manuelle
starter er ikke bevis på at tidsplanen fungerer. Kildehelse viser siste
vellykkede henting og forsinkelser, ikke om kildeeieren burde ha publisert noe
nytt. En stille kilde er ikke automatisk en feil.

Løpende test og observasjon skal støtte videre relevant kildeutvidelse. Det
kreves ingen vilkårlig ventetid med feilfri drift før nytt kildearbeid.

## Operasjoner

`preview` henter alle aktive testkilder uten sending eller lagring. `run` bruker
historikken og køen. `baseline` krever tom kildehistorikk og avvises før noen
kontrollpunkt-commit dersom historikk allerede finnes. `test-notification` er
en uttrykkelig manuell kanaltest utenom den gjenopptakbare køen.

Den offentlige `catalog-delivery-test.yml` er en separat engangstest med
midlertidig runner-state og ingen push. Generatorens repository-/ref-argumenter
angir det faste isolerte testmålet. De er ikke et bevis på fjernrepositoryets
identitet; workflow-gatene håndhever faktisk repository og synlighet.

Malenes `example-owner` er en plassholder. Sett den eksplisitte testidentiteten og `REPLACE_WITH_REVIEWED_COMMIT_SHA` til en kontrollert koderevisjon i en privat kopi før bruk. Den offentlige kopien skal ikke inneholde en installasjonseiers konto eller private repository-navn.
