# Rapporterte utslippsmengder per stoff og år

Kilden følger oppførte årsverdier fra eksplisitt valgte [offisielle faktaark](https://www.norskeutslipp.no/). To oppskrifter bruker samme funksjon: fossilt CO2/ammoniakk og biologisk oksygenforbruk (BOF5). Stoff, anlegg, år og medium gir identitet. Oppgitt mengde, kildeoppgitt enhet, tilgjengelighet og fotnotemerker overvåkes uten omregning eller summering på tvers av enheter.

Hver endelig oppskrift er hentet to ganger gjennom den faktiske adapteren. Begge ga null varsler ved første innlesing og gjentakelse, og identisk full neste historikk. Hver innlesing sammenligner to komplette avlesninger. [JSON-beviset](emission-values-2026-09-14.json) oppgir eksakte forespørselsadresser, metoder, byteantall og kilde-/kode-/konfigurasjonshasher. Midlertidig skjema- og øktinnhold publiseres ikke.

| Oppsett | Oppføringer | Tall | Ikke tilgjengelig | Ikke rapportert | Siste år med tall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fossilt CO2 og ammoniakk | 40 | 16 | 12 | 12 | 2023 |
| BOF5 | 20 | 10 | 10 | 0 | 2025 |

Begge faktaark viser ti år, 2016–2025, med kolonner for luft og vann. Første oppsett har luftverdier; BOF5-oppsettet har vannverdier. Fossilt CO2 er oppgitt i 1000 tonn per år. Ammoniakk og BOF5 er oppgitt i tonn per år. Enheten står uttrykkelig i stoffoverskriften. Ingen tall for 2024/2025 hevdes for det eldre eksempelet, og en anleggsoppføring med nyere rapporteringsmetadata er ikke alene bevis på en ny mengdeverdi.

## Kildekontrakt

Nettsidens aktive forklaring skiller `(I.T.) = Ikke tilgjengelig` fra `(I.R.) = Ikke rapportert`. Begge bevares med mengde som mangler. Tallverdi null er en egen verdi; nulloppføringer er testet syntetisk, men ingen faktisk null ble observert i de endelige utvalgene. Desimalkomma og mellomrom som tusenskille normaliseres med eksakte desimaltall. Ukjent markør eller blank celle avviser innlesingen. Fotnotemerker beholdes uten en udokumentert tolkning av estimering eller deteksjonsgrense.

Stoffvalget følger lenken og skjemaet som faktisk returneres av nettsiden. Før eventuell POST kontrolleres faktaarkets identitet, skjemastørrelse og tillatt offentlig valgshandling. Stoffoverskriften må bekrefte det valgte stoffet. Hele tabellens årsspenn må samsvare med nettsidens valgte fra-/til-år; manglende år, gjentatte år eller endrede mediumkolonner avviser innlesingen.

Første prøve avdekket at nettstedets anonyme økt kunne beholde stoffvalg og endre årsspennet fra 2016–2025 til 1994–2025. Denne prøven feilet før ny historikk ble lagret. Adapteren starter derfor hvert faktaark med tom informasjonskapsellagring i sin egen anonyme kildeøkt. Dette berører bare nettsidens midlertidige visning; overvåkingshistorikken beholdes. De endelige prøvene etter rettingen omfatter åtte HTTP-svar for første oppsett og fire for BOF5-oppsettet.

Den lenkede Excel-eksporten for første faktaark returnerte en arbeidsbok med teksten «Finner ikke data». Den brukes ikke som mengdebevis eller som kilde for adapteren.

## Omfang og verifikasjon

Velg 1–10 faktaark og inntil ti eksakte stoffbetegnelser. Valgt stoff må finnes i hvert faktaark. Faktisk sidevalg, enhet og komplett årstabell kontrolleres; den aktive årstabellen bestemmer historikkvinduet. Faktaarklenken åpner standardvisningen. For et stoff valgt med skjema må mottakeren velge stoffet under Type, slik også varselet opplyser.

Nye årsoppføringer uten tall gir ikke førstegangsvarsel. Senere endring av mengde eller tilgjengelighet følges. Ingen sletting eller tilbaketrukket rapportering utledes fra fravær. Oppført år er ikke publiserings-/godkjenningsdato. Kilden fastslår ikke tillatelsesoverskridelser, juridiske brudd, samlet miljøvirkning eller komplett landsdekning. To like avlesninger gir konsistensbevis, ikke en servergarantert transaksjon.

46 målrettede tester bestod. De omfatter faktiske parser-/skjemaløp med syntetiske svar, øktisolasjon, stoffvalg, ulike enheter, null versus manglermarkører, senere tall for et nytt år, årshull, endringer mellom avlesninger, omdirigeringer, størrelsesgrenser og bevart tidligere historikk. Senere planlagt kjøring og faktisk ny varsellevering er ikke bevist av de lokale prøvene.
