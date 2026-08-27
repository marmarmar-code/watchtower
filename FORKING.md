# Forking og eierskap

Watchtower er laget for at hver redaksjon skal eie sin egen installasjon.

> Det er foreløpig ikke lagt inn en programvarelisens. Fram til rettighetshaver og lisens er avklart, krever bruk, endring og videre distribusjon uttrykkelig tillatelse fra rettighetshaveren.

## Opprett en selvstendig installasjon

1. Fork den offentlige Watchtower-koden til redaksjonens egen GitHub-konto.
2. Opprett et privat `watchtower-runtime` hos samme eier fra runtime-malen.
3. Legg deploy-nøkkel, Slack- eller Teams-webhook og eventuelle kildenøkler i Actions Secrets i forken. Legg aldri secrets i runtime.
4. Fyll inn kilder og private filterregler i runtime-konfigurasjonen.
5. Kjør `test-notification` fra Actions for å kontrollere runtime og varslingskanal.
6. Kjør deretter `run` én gang for å etablere en stille baseline.
7. Kontroller at en senere naturlig planlagt kjøring fullføres før installasjonen regnes som operativ.

Standardoppsettet finner et privat repository med navnet `watchtower-runtime` hos samme GitHub-eier. En annen plassering må angis med `WATCHTOWER_RUNTIME_REPOSITORY`.

## Etter forking

Redaksjonen eier og drifter:

- sin egen fork av den offentlige koden og GitHub Actions;
- Actions-secrets, deploy-nøkler og andre credentials;
- privat runtime, overvåkingslister, filtre og state;
- adapterendringer, kildeoppfølging, varsler og feilhåndtering;
- utrulling, sikkerhetsvurderinger og brukerstøtte.

Upstream er bare et offentlig startpunkt. Det finnes ingen SLA, sentral drift eller sentral avhengighet, og upstream har ikke tilgang til private runtimes eller secrets.

## Oppdateringer

En fork mottar ingen automatiske upstream-oppdateringer. Redaksjonen velger selv om en endring skal hentes inn, vurderer sikkerhet og kompatibilitet, tester den mot egen runtime og ruller den ut når den er klar. Det er også helt greit å bli på en kjent versjon.

## Bidrag

Bidrag til upstream er frivillige. Hold installasjonsspesifikke innstillinger og særadaptere i forken. En generell adapter bør ha en navngitt vedlikeholder; ellers bør den bli i forken. Se [CONTRIBUTING.md](CONTRIBUTING.md) og [SECURITY.md](SECURITY.md) før deling.
