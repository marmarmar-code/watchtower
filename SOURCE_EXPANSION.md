# Kildeutvidelse

Denne utvidelsen bruker Watchtowers eksisterende RSS-, JSON-, CSV-, nettside- og
SSB-adaptere. Kilder tas bare inn i katalogen når offentlig endepunkt, parseresultat
og kildeeier er kontrollert. `verified_on` viser katalogkontrollen som lå til grunn
da kilden ble tatt inn. Fremtidsdatert metadata avvises. En ny, datert live-rapport
er beviset for at kilden fortsatt svarer; katalogen må ikke omskrives bare fordi
kalenderen går.

Første kontrollgrunnlag ligger i `source_checks/2026-09-09-media.json` og
`source_checks/2026-09-09-sectors.json`. Rapportene inneholder antall elementer,
stabile nøkler og faktiske eksempeloverskrifter fra begge hentinger.

## Levert

- Startpakker for medier, energi, mat og matindustri, jus og regelverk samt
  digital kommunikasjon, nett og sikkerhet. Den siste pakken dekker offentlige
  nett-, personvern- og sikkerhetskilder; den er ikke en kommunikasjon-/PR-bransjepakke.
- Eksisterende verifiserte Mattilsynet-, Skatteetaten-, Skatteklagenemnda- og
  Nkom-profiler er gjenbrukt i de nye pakkene.
- Mediepakken har 15 sektorkilder fra Medietilsynet, MBL, IJ, Fritt Ord, Amedia,
  Polaris Media, TV 2, NRK, LLA, Schibsted Media, Reuters Institute, Nordicom,
  EFJ og Journalismfund Europe.
- Energipakken har sju standardkilder fra NVE, RME, Statnett og Havtil. Nye oppskrifter
  dekker også mat, fiskeri, teknologi, helse, jus, eiendom, finans og arbeidsliv.
- Utvidelsen gir 37 nye katalogoppsett: 30 oppskrifter og sju RSS-profiler. Dette
  er ikke 37 kildeeiere; flere oppsett er avgrensede strømmer fra samme eier,
  blant annet NVE, Havtil, Fiskeridirektoratet og Medietilsynet.
- `scripts/check_public_sources.py` henter alle katalogførte RSS-profiler og
  kildeoppskrifter direkte fra offentlige endepunkter. Den bruker to parallelle
  hentere som standard, skriver bare en kort status og oppretter verken state,
  secrets eller varsler.

```bash
python scripts/check_public_sources.py
python scripts/check_public_sources.py --section rss --workers 2 --timeout 15
python scripts/check_public_sources.py > source-check-$(date +%F).txt
```

Mediepakken behandler bransjefeedene som et eksplisitt kildeomfang. Dermed blir
relevante bransjehendelser som redaktørskifter ikke borte fordi overskriften mangler
et privat søkeord. De eldre startpakkenes emnefiltrering er beholdt.

## Isolert kontroll før aktivering

Lag konfigurasjonen i en ny, tom mappe utenfor kode-repoet. Kjør deretter
`validate-runtime`, og bruk `preview` på hver ny kilde med en tom midlertidig
state-mappe. `preview` henter og parser, men sender ikke og lagrer ikke. En senere
aktivering må gjøres i privat runtime med stille førstegangsbaseline før ordinære
varsler vurderes. Ingen webhook eller Slack-test inngår i kildekontrollen.

## Kandidater

Kandidater som mangler stabilt offentlig endepunkt, entydig eier eller vellykket
parserkontroll blir stående utenfor TOML-katalogene. De skal dokumenteres her først
når kontrollen viser hvorfor de ennå ikke kan leveres.

Neste kildejobb beholder alle 15 avviste kandidater med en konkret vei videre:

- PFU krever offentlig, autentiseringsfri saksliste eller dokumentert API-tilgang.
- NJ, Bonnier News, Aller, Mentor Medier og DN Media Group trenger en stabil
  serverrendret nyhetsliste eller gyldig RSS-feed.
- Norsk Redaktørforenings nyhetsliste mangler brukbare synlige titler; PDF-listen
  må koble hvert dokument til sakstittel før den kan gi forståelige varsler.
- Fritt Ord-, NRK Info- og Polaris-feedene ga ugyldig eller tom XML. De leverte
  web_links-kildene brukes mens feedendepunktene undersøkes på nytt.
- Digdir og Nye metoder trenger nye, avgrensede selektorer som tåler dagens HTML.
  Nye metoders beslutningsdokumenter er allerede dekket separat.
- KOFA og Konkurransetilsynets nyhetsliste ga motstridende titler for samme URL;
  neste forsøk må identifisere ett kanonisk lenkeelement per sak.
- Den første egne markedsførings-, kommunikasjons- og PR-pakken har nå tre
  verifiserte oppsett: Kommunikasjonsforeningen (nyheter) og Kreativt Forum
  (nyheter og overganger). Dette er en avgrenset start, ikke full bransjedekning.

Noen leverte katalogvalg er bevisst ikke standard. `nve_news` overlapper de smalere
energi- og kraftsituasjonslistene. `fiskeridir_aquaculture` og
`fiskeridir_fisheries` overlapper den brede `fiskeridir_news`; velg enten bred eller
smal strøm i privat runtime. `nye_metoder_decision_docs` følger bare nye PDF-lenker
til møtepapirer med generiske titler. Den leser ikke møtedato, saker eller vedtak bak
PDF-en og inngår derfor ikke i standard helsepakke.

Kandidater prøves på nytt når endepunkt eller parseravgrensning er løst. Kilder må
også følges opp dersom eieren senere endrer URL eller HTML.

## Fortsettelse 9. september

Tre nye `web_links`-oppskrifter gir 43 oppskrifter og 17 RSS-profiler, totalt
60 katalogoppsett og tolv startpakker. Kommunikasjonsforeningen ga 30 entydige
lenker; Kreativt Forums nyheter og overganger ga 20 hver. To separate hentinger
per oppsett ga like identiteter og forståelige sakstitler uten kategori- eller
bylinetekst. Rapport: `source_checks/2026-09-09-marketing.json`. Pakken `marketing`
er separat fra `communications`, som fortsatt dekker nett, personvern og sikkerhet.

NJs forside ga bare tre saker; artikkellisten ga ingen serverrendret saksliste,
og det offentlig annonserte WordPress-API-et svarte HTTP 403. NJ beholdes derfor
som kandidat fremfor å gi forsidens korte utvalg inntrykk av full nyhetsdekning.

I tillegg er elleve avgrensede adaptertestkilder hentet og parset to ganger med
eksakte konfigurasjoner. Samlet testomfang blir 71 oppsett og 16 adaptertyper.
Dette er testdekning; de elleve spesialvalgene er ikke nye generelle katalogoppskrifter
og heller ikke en utvidelse av en privat installasjons standardpakke. Se `ADAPTER_COVERAGE.md`.

## Kontroll av årsskifte

Havtil-kilden og Datatilsynets nyhetskilde har årsbundne valg. Havtil har en
årsuavhengig kandidat (`main a[href*='/utforsk-fagstoff/fagstoff/']`) med de samme
ti artikkelidentitetene i to kontroller; rapporten er
`source_checks/2026-09-09-havtil-evergreen.json`. Datatilsynets årsuavhengige
indeks har vesentlig flere historiske saker enn dagens utvalg. Ingen av
endringene er aktivert over eksisterende state: snapshot-adapteren behandler
endret selektor eller URL som et nytt kildeomfang med stille baseline. En senere
overgang må derfor planlegges med bevart historikk og uttrykkelig avgrensning,
ikke ved å bytte URL eller selektor ukritisk. Disse valgene fungerer i 2026,
men må følges opp før neste årsskifte; en vellykket HTTP-henting alene vil ikke
oppdage en frosset årsside.

## Fire flere næringslivskilder

NHO, Finans Norge, Sjømat Norge og Forbrukertilsynet er lagt til som valgfrie oppsett, slik at katalogen nå har 47 oppskrifter og 17 RSS-profiler. To ferske hentinger per kilde er kontrollert med de faktiske filtrene; se `source_checks/2026-09-09-business-feeds.json`. Filteret hos Forbrukertilsynet bruker konkrete rekrutteringsfraser, slik at ord som «undersøker» ikke fjerner tilsynssaker. Katalogvalg av alle saker eller egne temaer bevarer oppsettets utelukkelser.


## Virke: pressemeldinger

Virkes egen presseside peker til organisasjonens presserom hos NTB, som annonserer en egen RSS-strøm. Oppsettet `virke_pressemeldinger` bruker den eksisterende RSS-adapteren og krever tema eller et uttrykkelig valg av alle saker. Det dekker nye meldinger om blant annet handel og arbeidsliv. Strømmen returnerte 25 meldinger ved verifisering; eldre arkiv og endringer i kjente meldinger er ikke dekket.

To faktiske hentinger ga stabile, unike meldinger med titler og datoer. Alle GUID-er viste Virkes utgiver-ID og samsvarte med de enkelte meldingslenkene. Tre meldingssider ble kontrollert mot RSS-titlene. Se `source_checks/2026-09-10-virke.json`. Kontrollen sender ingen varsler og dokumenterer ikke en fremtidig levering. Katalogen har nå 48 oppskrifter og 17 RSS-profiler.


## SSB: samlet produksjon

`ssb_production_total` følger tabell 07095, P101: utvinning, bergverk, industri og kraft samlet. Det er en sesongjustert indeks med basis 2021, ikke et mål på bare industri, omsetning eller prosentvis endring. Oppsettet bruker den eksisterende observasjonsadapteren, velger alle dimensjoner eksplisitt og henter de siste to månedene med `top(2)`. Nye perioder og revisjoner innenfor dette vinduet kan varsles; eldre revisjoner er ikke dekket.

Ferske offisielle metadata og to faktiske hentinger med det endelige utvalget er kontrollert for kodevalg, datoer, enheter, stabile nøkler og uendret historikk. Første lokale innlesing var stille, og neste ga ingen endring. Bevis finnes i `source_checks/2026-09-10-ssb-production.json`; dette er kilde- og kontraktbevis, ikke en observert fremtidig levering. Katalogen har nå 49 oppskrifter og 17 RSS-profiler.


## Presis filtrering av stillingsannonser

RSS-oppsett kan angi `exclude_url_hosts = ["emp.jobylon.com"]`. Bare nøyaktig vertsnavn i den enkelte meldingslenken treffes, uten jokertegn eller automatisk utelukking av underdomener. Store/små bokstaver og avsluttende punktum normaliseres. NHO-oppsettet bruker regelen for å stanse annonser fra denne jobbportalen, samtidig som nyheter om direktører og ansettelser beholdes.

Postene leses og identifiseres fortsatt, men varsling undertrykkes. Nøkler, fingeravtrykk og historikk bevares. En gyldig strøm med bare utelukkede annonser regnes fortsatt som en gyldig henting; dette betyr ikke at den ga varslingsverdige nyheter. Virkelig tom eller feilformet strøm beholder vanlig feilkontroll. Standardoppførselen er uendret uten vertsregelen.

To faktiske NHO-hentinger bekreftet at bare den spesifikke Jobylon-annonsen ble undertrykt, og at samtlige nøkler og kompatible fingeravtrykk var uendret. Se `source_checks/2026-09-10-nho-jobs.json`. Målrettede tester dekker også Atom, feilaktige vertsregler, vertsnavn i URL-parametre, lignende domener og bevaring av nyheter med samme stillingstittel.


## SSB: industri som eget utvalg

`ssb_manufacturing` følger den presise industrigruppen P105 i tabell 07095. Dette er et separat oppsett fra den brede P101-serien. Valget henter sesongjustert produksjonsindeks for de siste to månedene, med nye perioder og revisjoner i dette vinduet. Enhet og basis følger SSB-dataene; indeksen er ikke omsetning eller prosentvis vekst.

Ferske metadata og to faktiske hentinger av det endelige utvalget er kontrollert gjennom eksisterende observasjonsadapter, med stabile nøkler, enheter og fullstendige snapshots. Se `source_checks/2026-09-10-ssb-manufacturing.json`. Første lokale evaluering er stille; fremtidig levering er ikke dokumentert av kildehentingen. Katalogen har 50 oppskrifter og 17 RSS-profiler.


## SSB: lakseeksport

`ssb_salmon_exports` følger tabell 03024: ukentlig eksportmengde i tonn og gjennomsnittlig kilopris i kroner, separat for fersk/kjølt og frossen oppdrettslaks. Dette er åtte observasjoner over de siste to ukene. Nye uker og rettelser i dette vinduet kan varsles; eldre revisjoner, samlet sjømateksport og eksportverdi er ikke dekket.

Alle dimensjoner og enheter er kontrollert mot offisielle metadata. To faktiske hentinger gjennom eksisterende SSB-adapter ga stabile, unike poster og uendrede snapshots. Første lokale evaluering var stille. Se `source_checks/2026-09-10-ssb-salmon.json`; dette er kildebevis, ikke observert fremtidig levering. Katalogen har 51 oppskrifter og 17 RSS-profiler.

### Presis varsling fra overlappende nettsidelister

`web_links` støtter valgfri `exclude_url_prefixes`: absolutte HTTPS-adresser
til mapper, med avsluttende `/`, uten spørring, fragment eller jokertegn.
Bare varslingen undertrykkes. Innlesing, validering, observasjonsnøkler,
fingeravtrykk og eksisterende snapshot-omfang bevares. Standardoppsett uten
valget er uendret. Vert må være lik og stien må ligge under angitt mappe;
undervert og lignende mappenavn blir ikke automatisk undertrykt.

Når både NVE-hovedarkivet og de spesialiserte energi-/kraftlistene brukes,
kan hovedarkivet få følgende tillegg. Bruk bare dette sammen med aktive
spesiallister; hovedarkivet alene skal beholde alle sine varsler.

```toml
exclude_url_prefixes = [
  "https://www.nve.no/nytt-fra-nve/nyheter-energi/",
  "https://www.nve.no/nytt-fra-nve/rapporter-kraftsituasjonen/",
]
```

Ferske kildeuttrekk ga åtte poster i hver liste, med tre energi- og to
kraftposter i hovedarkivet. Samlet dekning beholdt alle 19 ulike adresser
etter partisjoneringen. Dette er snapshot-bevis, ikke en garanti for
framtidig publiseringsrekkefølge eller uendret kildeoppsett. Se
`source_checks/2026-09-10-nve-overlap.json`.

### Lesbar SSB-varseltekst

SSB-varsler viser verdi og enhet uten rå JSON eller tom datastatus.
Basisår beholdes, og kjente justeringskoder vises med norske navn.
Navnene er vår norske gjengivelse av sesong-/kalenderjustering i
[SSBs API-veiledning](https://www.ssb.no/api/pxwebapiv2). Ukjente koder
beholdes synlig med «justering:», og øvrige kildeopplysninger skjules ikke.
Endringer i beregningsgrunnlaget vises før/etter; der beholdes også
opplysninger om antall desimaler. Rå observasjoner, nøkler, hasher og
snapshot-omfang er uendret.

Ferske uttrekk av industri og lakseeksport ga ti observasjoner. Sammenlikning
med tidligere visning beholdt hele neste tilstand og ga null varsler om
allerede kjente tall. Forhåndsvisningen bruker den vanlige varselformateringen,
men ble ikke sendt. Se `source_checks/2026-09-10-ssb-message-quality.json`
og `source_checks/2026-09-10-ssb-message-preview.md`.

### Valgfritt arrangementsfilter for RSS

`exclude_url_path_segments` undertrykker varsler når nettadressens sti
inneholder et oppgitt helt segment. Regelen gjelder alle verter i den
konfigurerte RSS-kilden, er tegnfølsom og bruker verken tittel, omtale eller
spørringsparametre. Nøkler, fingeravtrykk og alle innleste poster beholdes.
Tom eller ødelagt strøm gir fortsatt feil; en gyldig strøm der alle
varsler er undertrykt er frisk. Ingen regel brukes som standard.

For NHO kan disse tre observerte arrangementsstiene utelates fra varsling:

```toml
exclude_url_path_segments = ["arrangementer", "arrangementer2", "kurs-og-arrangementer"]
```

To identiske faktiske NHO-uttrekk inneholdt 50 poster, 19 med disse stiene.
Med eksisterende ikon- og stillingsannonsefilter ble varslingsutvalget
redusert fra 47 til 28; alle 50 poster og deres identiteter beholdes.
RSS-en hadde ingen kategorifelt som kunne brukes til presis sortering.
Nyhetsartikler som omtaler arrangementer, men har vanlig artikkelsti,
beholdes. Klassifiseringen følger adressestrukturen og må kontrolleres
ved framtidige endringer hos utgiveren. Se
`source_checks/2026-09-10-nho-events.json`.

### Konkurransetilsynets nyheter

`konkurransetilsynet_news` følger den offisielle RSS-strømmen med eksisterende
RSS-adapter. To faktiske hentinger ga ti identiske, unike og daterte
publikasjoner om blant annet dagligvare, tjenestepensjon og oppkjøp.
Kronikker, podkast, forskningsmidler og andre faglige nyheter inngår også.
Valget krever et tema eller uttrykkelig valg av alle saker.

Oppsettet sjekker hver time og varsler bare nye URL-er. Første innlesing er
stille. De ti siste publikasjonene er et rullerende utvalg, ikke hele
nyhetsarkivet eller et komplett vedtaksregister. Eventuelle nye
rekrutteringsoppføringer må vurderes hvis de kommer inn i strømmen.

Ingen av de ti nyhetsadressene var identiske med adressene i eksisterende
fusjonsliste (30 poster i kontrollen). En fusjonsmelding og en separat
nyhetsartikkel kan likevel handle om samme selskap eller transaksjon.
Stabile nøkler og hasher, stille første innlesing og uendret ny henting ble
kontrollert lokalt uten varsling. Se
`source_checks/2026-09-10-competition-news.json`. Katalogen har nå
52 oppskrifter og 17 RSS-profiler, totalt 69 valg.

### Gamle meldinger som dukker opp i utvidede børslister

Utvidede Euronext-lister kan få nye rader med gamle publiseringsdatoer.
Når tidligere vellykket sjekktid finnes, lagres ukjente meldinger fra før
foregående UTC-dato stille i historikken. Den foregående dagen tillates
for tidssoner og kort publiseringsforsinkelse. Grensen følger siste
vellykkede henting, så en feilkjøring flytter den ikke framover.

Bare `include_listview = true` omfattes; de opprinnelige korte listene og
endringer i allerede kjente meldinger beholder oppførselen. Manglende
forrige sjekktid gir ingen datogrense. En ugyldig sjekktid eller ukjent
publiseringsdato for en ny rad stanser henting med synlig feil. Nøkler,
hasher og alle innleste rader bevares. Ekte publikasjoner som først blir
tilgjengelige etter denne tidsmarginen vil også regnes som eldre innhold.

To faktiske Bouvet-uttrekk og gjenspilling av eksisterende historikk
reproduserte en gammel publikasjon fra november 2025 som ny kandidat før
rettingen og null etter. Alle 50 rader, hasher og neste historikk var
uendret. Ingen ekstra varsler ble sendt i kontrollen. Se
`source_checks/2026-09-11-euronext-backfill.json`.

## Samme nye lenke i flere lister

Når samme nye `web_links`-post finnes i flere overvåkede lister i én kjøring,
sendes den én gang med navnet på det første utvalget. URL, postnøkkel og
innhold må stemme; kilde-ID utelates bare fra sammenligningen. Hver kildes
historikk og opprinnelige fingeravtrykk beholdes. Filtrering skjer før
sammenslåing, så et filtrert treff i hovedlisten skjuler ikke et treff i en
temaliste. Revisjoner, annet innhold og andre adaptere behandles uendret.

Dette gjelder bare samme kjøring, ikke dubletter mellom forskjellige
kjøringer. Faktisk Fiskeridirektoratet-innlesing og historikk fra før en ny
oppdrettssak ga to kandidater før og én etter; se
`source_checks/2026-09-11-web-link-overlap.json`. Kontrollen sendte ingen varsler.

## ESA: statsstøtte og konkurranse

To oppsett bruker nettsidens offentlige JSON-endepunkt gjennom eksisterende
`json_records`. Hvert følger 24 siste engelske temaoppdateringer. Standardfilter
velger omtale av Norway/Norwegian eller guidelines/framework i tittel og omtale.
Dette dekker utvalgte norske saker og felles regelverk, ikke alle norske saker
eller komplette vedtaksregistre. Nye adresser varsles; første innlesing er stille.
Kildens numeriske dato vises foreløpig ikke som publiseringsdato.

To faktiske adapterhentinger per endelig oppsett ga 24 stabile poster hver,
19/15 filtertreff, stille initialisering og null endringer ved gjenhenting.
Se `source_checks/2026-09-11-esa.json`. Katalogen har nå 54 oppskrifter og
17 RSS-profiler. Kontrollene er uten sending eller endring av runtime-historikk.

## NAV: arbeidsmarked og sykefravær

Oppsettet følger NAVs nasjonale presseliste gjennom eksisterende `web_links`,
med dato som sortering og de 20 første treffene. Standardfilteret velger
arbeidsliv, ledighet, sykefravær og rekrutteringsbehov etter tittel. Det følger
nyhetspubliseringer, ikke komplette tallserier eller egne fylkeslister.
Første innlesing er stille; nye lenker varsles. Publiseringsdato hentes ikke
ut i varselteksten av denne adapteren.

To faktiske innlesinger av det endelige oppsettet ga 20 stabile poster og
10 filtertreff, stille initialisering og null endringer ved gjenhenting.
Søkets innbakte metadata bekreftet pressefilter, første side og datosortering.
Ingen varslingskall eller runtime-historikk ble skrevet. Se
`source_checks/2026-09-11-nav.json`. Katalogen har nå 55 oppskrifter og
17 RSS-profiler.

## RSS-kategorier skiller kalender fra nyheter

RSS og Atom kan bruke valgfri `exclude_categories = ["Kalenderen"]`.
Regelen sammenligner hele kategorietiketter uten hensyn til store/små
bokstaver. Tittel, brødtekst og delstrenger brukes ikke. Originale
kategorier, poster, nøkler og fingeravtrykk beholdes; bare varslingen
undertrykkes. En liste der alle poster er utelatt, er fortsatt gyldig,
mens ødelagte poster fortsatt gir synlig feil.

To faktiske Sjømat Norge-uttrekk hadde ti poster: seks var merket Kalenderen
og fire var tariff-, politikk- og tollnyheter. Re-evaluering av ekte historikk
fra før to kalenderinvitasjoner ga to kandidater før og null etter, med
uendret full neste historikk. Se `source_checks/2026-09-11-rss-categories.json`.
Ingen varsler ble sendt i kontrollen. Merking hos kilden avgjør om framtidige
arrangementer blir utelatt. Standardoppskrifter endres ikke automatisk.


## Saks- og produktfunksjoner, september 2026

32 nye oppsett gir 30 ulike funksjoner, konservativt telt i [EVENT_FUNCTIONS.md](EVENT_FUNCTIONS.md).
Nye oppsett følger konkrete saks- og produktopplysninger. Antall oppsett er ikke
et mål på antall ulike journalistiske hendelser. Felles funksjoner kan brukes av
flere brukermiljøer uten å telle på nytt.

| Oppskrift | Hva kan utløse varsel | Avgrensning |
| --- | --- | --- |
| `einnsyn_project_journal` | Ny journalpost eller endret offentlig tittel/type/journaldato | Valgt prosjekt/selskap, rullende publiseringsvindu; ingen påstand om vedtak |
| `ted_contract_awards` | Kontraktsresultat, vinner og oppgitt samlet verdi | Norske resultatkunngjøringer i komplett sjudagersvindu; beløpet fordeles ikke på vinnere |
| `ted_contract_modifications` | Publisert kontraktsendringskunngjøring | Tomt vindu ved kontroll; ingen positiv endringshendelse observert ennå |
| `kofa_case_progress` | Innkommet klage og endret status/avgjørelse | De 50 sist viste KOFA-sakene |
| `media_appeal_cases` | Ny eller endret medieklage/avgjørelse | De 30 sist viste sakene i Medieklagenemnda |
| `marketing_appeal_cases` | Ny eller endret markedsføringsklage/avgjørelse | De 30 sist viste sakene i Markedsrådet |
| `nve_solar_cases` | Ny/endret konsesjonssak, stadium, høringsfrist og prosjektverdier | Standard alle solkraftsaker, maks 1000; type/kommune/søk kan tilpasses |
| `dmp_shortage_periods` | Ny mangelregistrering og endret periode/status/tiltak | Preparatnavn med dose/pakning og virkestoff; daglig kildeoppdatering |
| `mattilsynet_recall_products` | Tilbakekalling og endret produkt-/partiomfang | De ti sist viste sakene, med separate produktgrupper |
| `oslo_project_cases` | Ny sak eller endret status/siste dokumentdato | Eksplisitt fritekstsøk, ikke eksakt adresse eller dokumentert byggetillatelse |
| `finkn_company_decisions` | Ny/endret avgjørelse for valgt finansforetak | Siste 30 dagers behandlingsdato; senere publisering kan falle utenfor |
| `sodir_licence_operator` | Nye/endret gjeldende operatørperioder | Hele CSV-en leses; 541 gjeldende perioder uten sluttdato er valgt |
| `sodir_field_status` | Nye/endret faglig feltstatus og gyldighetsperioder | Hele feltstatus-CSV; synkroniseringsdato alene er stille |
| `aquaculture_site_capacity` | Endret kapasitet og plassering | Ett eksplisitt lokalitetsnummer; ikke fiskehelse eller lisenser |
| `research_approved_projects` | Nye/endret innvilgede prosjekter | Valgt utlysning; beløpet er søkt, ikke tildelt |
| `sodir_exploration_progress` | Ny letebrønn og endret status/boredatoer | Siste tiårs utvalg; brønnavn identifiserer posten, ikke NPD-ID |
| `nb_nowa_observations` | Ny/revidert daglig NOWA | Fem siste daglige renteobservasjoner; ikke volum eller styringsrente |
| `nve_reservoir_no1_weekly` | Ny uke eller revidert magasinfylling | NO1, siste publiserte uke; andeler og TWh |
| `brreg_account_document_years` | Nytt år på liste over regnskapskopier | PDF-henting har gitt tidsavbrudd; lenken går til årslisten |
| `pfu_decision_outcomes` | Ny/endret PFU-avgjørelse | Komplett 30 dagers behandlingsvindu; ikke alle historiske etterregistreringer |
| `cisa_known_exploited` | Ny CVE og endret tiltak/ransomware-status | Global CISA-liste; fristen er amerikansk, og norske angrep er ikke dokumentert |
| `clinical_trials_norway_updates` | Nyobservert studie, rekruttering, fase, sponsor eller resultater | Norske studiesteder; komplett 14-dagers oppdateringsvindu |
| `arbeidstilsynet_inspection_reactions_by_industry` | Ny/revidert reaksjonsstatistikk | År/næring; bygg og anlegg som standard, ikke individuelle selskapsvedtak |
| `smilefjes_restaurant_inspections` | Nytt tilsyn eller endret overordnet resultat | Ett serveringssted; detaljkrav og nye restauranter oppdages ikke |
| `ema_medicine_authorisations` | Produktstatus, uttalelse, indikasjon og innehaver | Humanlegemidler hos EMA; ikke norsk refusjonsvedtak |
| `mattilsynet_pesticide_products` | Produktgodkjenning, gyldighet og bruksmerknader | Numerisk produkt-ID; registreringsnummer kan mangle |
| `mattilsynet_pesticide_temporary_permits` | Tidsbegrenset brukstillatelse og vilkår | Egen bruksbetingelses-ID, ikke generell produktgodkjenning |
| `fishhealth_ila_outbreaks` | ILA-mistanke, påvisning og tomtdato | Råmeldinger fra BarentsWatch/Veterinærinstituttet |
| `fishhealth_ila_zone_validity` | Ny/endret sonepost, forskriftsreferanse og gyldighet | Også historiske soner; geometri overvåkes ikke |
| `nye_metoder_treatment_decisions` | Metode-/indikasjonsbeslutning og nasjonalt utfall | Aktuell Excel-oversikt oppdages dynamisk; beslutningsdato er separat |
| `industrial_installation_status` | Anleggsstatus, myndighet, rapporteringsår og rapporteringsflagg | Ett valgt anlegg; siste rapporteringsår 2023, ingen utslippsmengder/bruddpåstand |
| `storting_case_vote_outcomes` | Ny/endret votering, kildeoppgitt utfall og stemmetall | Valgte saks-ID-er; eksemplet fra 2021 oppdager ikke nye saker |

Endelige oppskrifter er kontrollert med to faktiske offentlige adapterpoller
hver: stille førsteinnlesing, null varsler ved uendret gjentakelse og identisk
full neste historikk. Eksakt konfigurasjon, adapterkode og råsvar er knyttet
sammen med SHA-256 i `source_checks/2026-09-11-final-verification.json`.
Dette er gjeldende kildebevis; tidligere wave-filer og kildeundersøkelser viser
mellomsteg. Register-/tilsyns-/beslutningsdato er skilt fra publiseringstid.

Lokal kilde- og konfigurasjonsverifikasjon beviser ikke at varsler er levert.
Installasjon, stille førstegangsinnlesing i drift og senere leveringskvitteringer
må kontrolleres separat i den installasjonen som tar oppsettene i bruk.

Bruk eksisterende kildevalg, for eksempel:

```bash
python -m watchtower add-source --runtime /path/to/private-runtime --recipe dmp_shortage_periods --topic semaglutid
python -m watchtower add-source --runtime /path/to/private-runtime --recipe kofa_case_progress --topic rådgivning
```

Kommandoen viser forslaget. `--apply` lagrer lokalt. Prosjekt/selskap/type eller
tidsvindu endres i det private oppsettet; vesentlig nytt utvalg skal ha ny ID.
Første kildeinnlesing er stille. Ingen av de nye adapterne tolker en forsvunnet
rad som en slettet sak eller tilbakekalt tillatelse. Mangelfulle svar feiler.

Generiske anvendelser gjelder ulike fagområder.



Dette er anvendelser av kildefunksjonene, ikke elleve ferdig aktiverte fagpakker.


## Automatisk oppdagelse og flere konkrete selskapsmål

Videre arbeid etter den første milepælen er dokumentert i [EVENT_FUNCTIONS.md](EVENT_FUNCTIONS.md). Forskningsutlysninger kan nå oppdages fra den offisielle resultatlisten for eksplisitte år. Industrielle dokumentlenker overvåkes separat fra driftsmetadata. Alle disse bruker stille førsteinnlesing; kildebevis i `source_checks/` må skilles fra senere drifts- og leveringsbevis.
