# EMA regulatoriske sikkerhetshendelser — 2026-09-11

EMA dokumenterer komplette JSON-filer som oppdateres to ganger daglig. Adapteren har tre faste datasettmoduser med eksakt offisiell URL, skjema, utvalg, datoformat og sidebane. Utvalget kan ikke endres med frie konfigurasjonsfiltre. `meta.total_records` må være lik antall rader. Første innlesing er stille, og fravær tolkes ikke som tilbaketrekking.

`dhpc` overvåker direkte sikkerhetsinformasjon om humane legemidler. Den eksakte typen `Medicine shortage` utelates for å begrense overlapp med mangelovervåking; kombinerte typer med andre sikkerhetsforhold beholdes. 142 av 174 rader ble valgt. Overvåkede felt er legemiddel, virkestoff, type, regulatorisk utfall, tilknyttet prosedyre, andre nasjonale legemidler og distribusjonsdato.

`referrals` velger humane prosedyrer der EMA markerer `safety_referral = Yes`. 100 av 592 rader ble valgt. Overvåkede felt er prosedyrenavn, virkestoff, status, type, tilknyttede legemidler, PRAC-anbefaling og uttrykkelige prosedyre- og beslutningsdatoer.

`psusa` overvåker regulatoriske utfall av periodiske sikkerhetsvurderinger. Alle 2714 rader velges fordi kategorien var blank i 2099 rader og `Human` i 615; et kategorifilter ville gitt et stort udokumentert hull. Overvåkede felt er de to observerte virkestofffeltene, relaterte legemidler og regulatorisk utfall. Noen rader mangler begge virkestofffeltene og bruker det validerte prosedyrenummeret bare som visningstittel.

Stabil nøkkel er den validerte EMA-side-URL-en. Prosedyre- og referansenumre var enten tomme eller dupliserte i deler av kildene, mens URL-ene var utfylte og unike. Adapteren validerer alle lenker i det valgte utvalget; lenker i rader som utvalget utelater brukes ikke som identitet og inngår ikke i denne påstanden. En framtidig endring av sideslug kan derfor fremstå som en ny post. `first_published_date` brukes som publiseringsdato. Filens genereringstid og `last_updated_date` valideres, men påvirker ikke sammenligningen.

Alle verdier må være tekst, datoer må følge `DD/MM/YYYY`, og kjente kategori-, status- og utfallsverdier valideres. Ugyldig JSON, endret skjema, ukjent enum, feil dato eller vertsnavn, duplisert URL, totalsvikt, tomt uttrekk og overskredne byte- eller radgrenser feiler lukket.

To faktiske adapterpoller gjennom endringsmotoren ble kjørt per modus. DHPC ga 142/142 poster, referrals 100/100 og PSUSA 2714/2714. Alle første- og andreinnlesninger ga null varsler, og hvert snapshot var identisk ved gjentakelse. Eksakte hashes og URL-er finnes i det maskinlesbare kildebeviset.
