# Bemanningsregisterets godkjenningsstatus

Oppskriften `staffing_enterprise_approval_changes` følger hovedenheter i [Arbeidstilsynets bemanningsregister](https://www.arbeidstilsynet.no/bemanningsvirksomhet/). To faktiske innlesinger av den endelige oppskriften ga 3 051 hovedenheter, null varsler ved første innlesing og gjentakelse, og identisk full neste historikk. Hver innlesing henter og sammenligner to komplette uttrekk før hovedenheter velges. Eksakte POST-adresser, kilde-, konfigurasjons-, adapter- og felleskodehasher står i [JSON-beviset](staffing-register-2026-09-14.json).

Hele uttrekket har 7 250 unike organisasjonsnumre med gyldig sjekksum: 3 051 hovedenheter og 4 199 underenheter. Sidegrensen 500 gir 15 sider, med 250 rader på siste side. Alle sideantall, totaltall, statusmappinger og boolske godkjenningsflagg valideres i begge uttrekk. Normaliserte oppføringer må være identiske mellom uttrekkene, også i mellomliggende sider. Sortering og kartkoordinater inngår ikke i endringsfeltene.

Oppført status er Godkjent for 6 508 enheter og Ikke godkjent for 742. Blant hovedenhetene er tilsvarende tall 2 689 og 362. Koden Ikke registrert er dokumentert i registerets mapping, men ble ikke observert i eksporten. Alle observerte godkjenningsflagg stemmer med statuskoden. Navn mangler for 138 enheter, hvorav 57 hovedenheter; de beholdes med ukjent navn og organisasjonsnummer som identitet.

## Endringer og avgrensning

Ny enhet og endret oppført navn eller status følges. Underenheter kan tas med via `include_subunits = true`; et eksplisitt utvalg kan angis med `orgnrs`. Hele uttrekket valideres før utvalg. En eksplisitt valgt enhet som mangler eller er utelukket av enhetstype, avviser innlesingen. Antall enheter skal ikke omtales som antall foretak uten hovedenhetsavgrensning.

Godkjennings-/vedtaksdato er ikke oppgitt i den observerte listekontrakten. Varslene viser registerstatus uten en selvstendig juridisk lovlighetsvurdering. Fravær tolkes ikke som inndratt godkjenning. Detaljsidens fulle innhold, relasjoner mellom hoved- og underenheter og geografiske flyttinger overvåkes ikke. To like uttrekk gir konsistensbevis, ikke en servergarantert transaksjon eller garanti for at opplysninger ikke endres etter avlesingen.

73 målrettede tester bestod, inkludert kildeadapter, motor, konfigurasjon og oppskrifter. Syntetiske tester dekker blant annet endret status, ukjent navn, underenheter, eksplisitt utvalg, delvise sider, duplikater, endret mellomside og stabil gjentakelse. Ekte kildedata er brukt i de separate innlesingene beskrevet over; senere planlagt kjøring og faktisk varslingslevering er ikke bevist av disse kontrollene.

## Historikkgrense for store oppsett

Det første forsøket ga null/null varsler og identiske registeroppføringer, men roterende historikknøkler fordi 3 051 oppføringer oversteg standardgrensen 3 000. Oppskriften bruker derfor `max_seen_per_source = 5000` for denne kilden. Andre kilder bruker fortsatt sin eksisterende grense. Endring av denne lagringsgrensen utløser ikke ny baseline for registeroppsettet. De endelige to kildeinnlesingene etter rettingen ga identisk full historikk. Ved større utvalg må både `max_records` og historikkgrensen tilpasses.
