# Support

Watchtower distribueres som kildekode og utgangspunkt for selvstendige installasjoner.

En installasjon er en egen fork. Den kan kjøre videre uten upstream, og upstream oppdaterer aldri en fork automatisk. Hver redaksjon bestemmer selv hvilke endringer som tas inn, og må kontrollere dem før bruk.

## Den enkelte installasjonen er ansvarlig for

- egen Watchtower-fork og privat runtime;
- egen offentlig kodebase, inkludert eventuelle adapterendringer;
- GitHub Actions, tidsplan og branch-beskyttelse;
- deploy keys, webhook-adresser, API-nøkler og rotasjon;
- filterregler, overvåkingslister og state;
- kontroll av varsler og driftsfeil;
- lokale kodeendringer;
- oppfølging når eksterne kilder eller API-er endres;
- vurdering av om løsningen kan brukes etter gjeldende interne regler.

Dette ansvaret omfatter også å følge med på kildekilder, GitHub Actions og egne avhengigheter. Upstream har ikke tilgang til installasjonens secrets, runtime eller driftsmiljø.

## Upstream omfatter

- den generelle overvåkingsmotoren;
- adaptere som ligger i dette repositoryet;
- automatiske tester og sikkerhetskontroller;
- vurdering av generelle pull requests etter kapasitet.

Upstream innebærer ikke:

- sentral drift eller overvåking av andre installasjoner;
- garanti for oppetid eller fullstendighet;
- konfigurering av en konkret installasjon;
- utvikling eller vedlikehold av særadaptere;
- tilgang til private runtimes eller credentials;
- garanti for at eksterne datakilder beholder samme format eller tilgjengelighet.
- automatiske oppdateringer eller sentral synkronisering av en fork.

## Feilrapportering

En generell kodefeil kan beskrives i et offentlig issue dersom rapporten ikke inneholder private verdier, produksjonsstate, credentials eller uredigerte logger.

Installasjonsspesifikke problemer håndteres i den aktuelle forken. Se `SECURITY.md` før sikkerhetsrelatert informasjon deles.
