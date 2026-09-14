# AD-publikasjoner og revisjoner

Oppskriften `easa_airworthiness_directive_updates` følger EU-utstedte AD-publikasjoner i [EASAs offentlige verktøy](https://ad.easa.europa.eu/search/), med utstedelsesdato de siste 30 dagene. To faktiske innlesinger av den endelige oppskriften ga 13 publikasjoner, null/null varsler og identisk full neste historikk. Hele nummeret er identitet, inkludert revisjonssuffiks. Ett observert dokument viser uttrykkelig til en tidligere revisjon, og tre har oppførte referanser til dokumenter de erstatter. Opplysningene gjengis som kildens tekst.

Hver innlesing sammenligner to komplette avlesninger av liste, XML og alle valgte detaljsider. I det verifiserte vinduet 2026-08-16 til 2026-09-14 hadde listen 31 AD-publikasjoner på to sider, fordelt 20/11. HTML- og XML-oppføringer må ha samme ID-er, utsteder, utstedelsesdato, ikrafttredelsesdato og emne. Hele listen valideres før utstederfilteret velger 13 EU-publikasjoner. Totalt 64 faktiske HTTP-svar inngår i de to innlesingene. Metode, POST-filter, adresser, byteantall og kilde-/kode-/konfigurasjonshasher står i [JSON-beviset](airworthiness-2026-09-14.json).

Revisjon, korrigering, oppført erstatning og ATA-kapittel leses fra dokumentets egen detaljtabell. Et separat varselbanner om et annet dokument inngår ikke. Typebetegnelse og vedleggslenker overvåkes også; selve vedleggsinnholdet lastes ikke. Detaljsidens ID, utsteder og datoer må stemme med listen. Endret rekkefølge og oppgitte filstørrelser utløser ikke varsel. Ufullstendige sider, duplikater, motstridende data, endring mellom de to avlesningene, uventede omdirigeringer og ugyldig XML avviser innlesingen før ny historikk lagres.

Et tomt datovindu har en eksplisitt «no results»-side, uten sideteller. Dette er observert for 2026-09-13. Tomresultatet må gjentas i begge avlesningene, og oppskriften tillater tomt vindu. XML-eksport for tomt resultat er ikke verifisert; forsøket fikk tidsavbrudd, og adapteren antar ikke et udokumentert XML-format.

## Omfang

`lookback_days` kan velges fra 1 til 90, og `issuers` angir kildeoppgitte koder; en tom liste velger alle utstedere i AD-vinduet. Inntil 25 sider kan konfigureres, med 20 oppføringer per side. Oppskriften tillater 10 sider og 200 valgte publikasjoner. For store eller ufullstendige uttrekk feiler tydelig.

Datoavgrensningen gjelder utstedelse, og holdes atskilt fra ikrafttredelse. En senere endring i et eldre dokument uten ny utstedelsesdato faller utenfor vinduet. Eksplisitte dokumentnumre utenfor vinduet støttes ikke. SIB/PAD og andre publikasjonstyper er ikke AD-scope. Ingen oppheving utledes fra fravær, og registeret er ikke globalt komplett eller bevis på hvilke krav som gjelder et bestemt fly. To like avlesninger gir konsistensbevis, ikke en transaksjonsgaranti fra kilden. Senere planlagt kjøring og faktisk ny levering er ikke bevist av disse lokale kontrollene.

45 målrettede tester bestod. Syntetiske HTTP-svar tester blant annet to sider, stille baseline/gjentakelse, revisjons- og datoendringer, ignorerte bannere, utstederfilter, null treff, duplikater, HTML/XML-konflikt, manglende detaljfelt, endring i andre innlesing, ugyldig XML, omdirigeringer og størrelsesgrenser. Dette er atskilt fra de faktiske kildeinnlesingene ovenfor.
