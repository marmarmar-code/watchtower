# Publiserte inntektsrammer for 2025

Oppskriften `rme_distribution_income_cap_changes` følger RMEs vedtaksregneark for andre nettselskaper. To faktiske innlesinger av den endelige oppskriften ga 88 selskapsoppføringer, null varsler ved første innlesing og gjentakelse, og identisk full neste historikk. Én oppføring har faktisk beløp 0. Begge innlesinger validerte også vedtakssiden før og etter filhenting. Eksakte kilde-, konfigurasjons-, adapter- og workbook-leserhasher står i [JSON-beviset](income-caps-2026-09-14.json).

Organisasjonsnummer og rammeår gir identitet. Navn, intern-ID, kostnadsgrunnlagsår og publisert rammebeløp følges som endringsfelter. Rammeåret er 2025, kostnadsgrunnlaget 2023 og den eksplisitte enheten er 1000 NOK. Lagrede numeriske formelresultater leses uten å beregne formlene. Desimaler og faktisk null bevares. Summen av selskapenes rammeverdier kontrolleres mot den publiserte totalen med toleranse 0,0001 i kildeenheten for lagret tallpresisjon; selve rammebeløpet omberegnes ikke.

Manglende cache, feil identitet/enhet, duplikater, ukjent eller ufullstendig tabellavslutning, totalsumavvik og endret vedleggslenke under innlesing avviser hele uttrekket. 39 målrettede tester omfatter ny adapter, opt-in-lesing av formelcacher, eksisterende årsdataleser og katalog/oppskrifter. Syntetiske tester beviser endrings- og feiloppførsel; de to offentlige innlesingene er kildebevis.

## Avgrensning

Dette er én funksjon for publiserte rammeverdier. Statnett og NordLink ligger i et separat regneark og inngår ikke. Nye vedtaksår velges eksplisitt; bare 2025-formatet har ekte kildebevis. Varsel og foreløpige beregninger godtas ikke som vedtak. En endret celle beviser ikke et nytt juridisk vedtak, og nettsidens publiseringsdato brukes ikke som hendelsesdato. Fravær tolkes ikke som tilbaketrekking. Kalibreringsunderlaget overvåkes ikke som selvstendige endringsfelt.

Det offentlige vedtaksregnearket krever ingen API-nøkkel. RMEs separate API krever nøkkel og brukes ikke av denne oppskriften. Kildebeviset dokumenterer ikke senere planlagt kjøring eller leverte varsler.
