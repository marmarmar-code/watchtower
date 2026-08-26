## Endring

Beskriv kort problemet og hva som er endret.

## Kontroll

- [ ] Endringen er generell og hører hjemme i upstream, ikke bare i én installasjon.
- [ ] Ingen reelle overvåkingsverdier, runtime-state, credentials eller uredigerte produksjonslogger er lagt inn.
- [ ] Nye eller endrede funksjoner har automatiske tester.
- [ ] Stille første baseline og deduplisering er bevart.
- [ ] Nye nettverkskall har timeout og begrenset retry.
- [ ] `python scripts/check_public_safety.py` består.
- [ ] `python -m unittest discover -s tests -v` består.

## Driftsvirkning

Beskriv eventuelle endringer i runtime-kontrakt, secrets, state, varsling eller oppgraderingsprosedyre. Skriv `Ingen` dersom det ikke er noen.
