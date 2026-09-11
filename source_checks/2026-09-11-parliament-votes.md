# Stortingets voteringsresultater – 11. september 2026

Offisiell dokumentasjon viser sak 83657 som eksempel. Det faste endepunktet
`https://data.stortinget.no/eksport/voteringer?SakId=83657` ga fire voteringer
fra 1. juni 2021. Dette er befolket, historisk kildebevis, ikke en ny politisk
hendelse i september 2026. Brukeren må velge aktuelle saks-ID-er selv.

To ekte avlesninger av den endelige oppskriften ga fire poster hver, null
førstegangsvarsler, null gjentakelsesvarsler og identisk full historikk.
Konfigurasjon, adapter og råsvar er hashfestet i
`2026-09-11-final-verification.json`. Ingen varsler ble sendt.

Voterings-ID er stabil nøkkel. Svarets namespace, liste, saks-ID og hver
posts saks-ID kontrolleres. Duplikater, manglende felt, ugyldige boolske
verdier, stemmetall og datoer feiler. Alle valgte saker må kunne leses.
Responsens genereringstid og presidentens personopplysninger ignoreres.
Voteringstid er et eget kildefelt; den brukes ikke som publiseringstid.

`vedtatt` kommer direkte fra kilden. Enstemmige voteringer kan ha null i
stemmetallene, så utfallet beregnes ikke fra antall stemmer. Varsel lenker til
den verifiserte offentlige voteringslisten for saken. Oppsettet oppdager ikke
nye saker automatisk, behandler ikke representantnivå og tolker ikke en
forsvunnet votering som trukket vedtak. Utvalget er begrenset til ti saker og
tusen voteringer totalt, med egen bytegrense per svar.
