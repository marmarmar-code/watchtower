# Forking og eierskap

Watchtower er laget for at hver installasjonseier skal eie sin egen installasjon.

> Det er foreløpig ikke lagt inn en programvarelisens. Fram til rettighetshaver og lisens er avklart, krever bruk, endring og videre distribusjon uttrykkelig tillatelse fra rettighetshaveren.

## Opprett en selvstendig installasjon

Følg [INSTALL.md](INSTALL.md) for hele oppsettet: repoer, veiviser, nøkler,
varsling, stille baseline og kontroll av automatisk kjøring. Runtime-malen peker
til samme oppskrift.

Bruk ett eget privat runtime-repo per installasjon og angi
`WATCHTOWER_RUNTIME_REPOSITORY` eksplisitt i kodeforken. Eksisterende standardoppslag
av `<eier>/watchtower-runtime` støttes fortsatt.

## Etter forking

Installasjonseieren eier og drifter:

- sin egen fork av den offentlige koden og GitHub Actions;
- Actions-secrets, deploy-nøkler og andre credentials;
- privat runtime, overvåkingslister, filtre og state;
- adapterendringer, kildeoppfølging, varsler og feilhåndtering;
- utrulling, sikkerhetsvurderinger og brukerstøtte.

Upstream er bare et offentlig startpunkt. Det finnes ingen SLA, sentral drift eller sentral avhengighet, og upstream har ikke tilgang til private runtimes eller secrets.

## Oppdateringer

En fork mottar ingen automatiske upstream-oppdateringer. Installasjonseieren velger selv om en endring skal hentes inn, vurderer sikkerhet og kompatibilitet, tester den mot egen runtime og ruller den ut når den er klar. Det er også helt greit å bli på en kjent versjon.

Se [UPGRADING.md](UPGRADING.md) for versjoner, kompatibilitet og retur til kjent kode.

## Bidrag

Bidrag til upstream er frivillige. Hold installasjonsspesifikke innstillinger og særadaptere i forken. En generell adapter bør ha en navngitt vedlikeholder; ellers bør den bli i forken. Se [CONTRIBUTING.md](CONTRIBUTING.md) og [SECURITY.md](SECURITY.md) før deling.
