# Forskningsrådet – kontroll11. september2026

Endelig oppskrift `research_approved_projects` leser utlysningen
https://www.forskningsradet.no/utlysninger/2026/kompetansebyggende-prosjekt-naringslivet/.
To faktiske adapterpoller ga19 innvilgede prosjekter, stille førstegangsinnlesing,
null varsler ved gjentakelse og identisk full historikk. Eksakt konfigurasjon,
kodehash og råsvarshasher står i `2026-09-11-wave-three.json`.

Parseren leser ProposalPage sitt JSON-objekt og krever én results-blokk med
én tabell merket «Innvilgede søknader» og de seks observerte kolonnene.
Avslag eller tvetydig overskrift kan ikke tolkes som innvilgelse. Prosjektnummer,
tekstfelt, søkt beløp og gyldig publiseringsdato valideres. Publiseringsdato
normaliseres til ISO og påvirker ikke substansfingeravtrykket.

`requested_amount` er søkt beløp i kroner. Prosjektradene oppgir ikke tildelt
beløp; utlysningens samlede tildelte midler fordeles ikke på prosjektene.
Identitet er utlysningens URL og prosjektnummer. Ingen sletting, ingen automatisk
oppdagelse av nye utlysninger og ingen nasjonal totaldekning. Tom tabell kan
aksepteres bare med `allow_empty`; manglende resultatblokk er alltid kildefeil.
