# Contributing

Generelle forbedringer kan foreslås som pull requests. Installasjonsspesifikk konfigurasjon og særtilpasninger bør normalt bli i den enkelte fork.

## Før en pull request

- Opprett en avgrenset branch fra oppdatert `main`.
- Ikke legg inn reelle overvåkingsverdier, runtime-state, credentials eller produksjonsoutput.
- Bevar eksisterende kildeadaptere og runtime-kontrakt med mindre endringen uttrykkelig krever noe annet.
- Hold endringen så liten som mulig.

## Krav

Alle kodeendringer skal:

1. ha automatiske tester for ny eller endret oppførsel;
2. bevare stille første baseline;
3. unngå varsling av uendrede elementer;
4. bruke stabile item-nøkler og deterministiske fingerprints;
5. gi korte og redigerte feilmeldinger;
6. ha begrenset retry, timeout og ressursbruk;
7. ikke skrive private verdier til logger;
8. bestå sikkerhetsgaten.

Kjør:

```bash
python -m pip install .
python -m pip check
python -m compileall -q watchtower tests scripts
python scripts/check_public_safety.py
python -m unittest discover -s tests -v
```

## Nye adaptere

En ny adapter skal:

- bruke `Source.get()` for nettverkskall;
- normalisere data til `Item`;
- ha stabile nøkler;
- ha syntetiske parser-fixtures eller mocks;
- dokumentere nødvendige runtime-felt og secrets;
- håndtere tomme og uventede svar uten å lekke request-data;
- etablere stille baseline på første kjøring.

Adaptere med egen snapshot-state kan bruke `augment_state()`. State skal være kompakt, deterministisk og lagres bare i privat runtime.

## Pull request-beskrivelse

Beskriv:

- hvilket problem som løses;
- hvilke filer eller kontrakter som endres;
- hvordan bakoverkompatibilitet er ivaretatt;
- hvilke tester som er kjørt;
- eventuelle drifts- eller sikkerhetskonsekvenser.

Ikke legg inn privat installasjonsinformasjon i pull requesten.
