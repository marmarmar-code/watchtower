# Security

Dette offentlige repositoryet skal til enhver tid være trygt å publisere.

## Skal aldri ligge i Git-historikken

- produksjonsfiltre, overvåkingslister eller private prioriteringer;
- privat runtime-state eller hendelseshistorikk;
- Slack-, Teams-, Power Automate- eller andre webhook-adresser;
- deploy keys, API-nøkler, tokens, private nøkler eller sertifikater;
- kopiert produksjonskonfigurasjon;
- produksjonsoutput som identifiserer overvåkede verdier bare fordi de overvåkes.

En credential som har vært publisert, skal roteres. Det er ikke tilstrekkelig å slette den fra siste commit.

## Workflow-krav

Produksjonsworkflowen skal:

1. gi offentlig `GITHUB_TOKEN` bare lesetilgang;
2. bruke en repository-avgrenset deploy key mot privat runtime;
3. maskere private konfigurasjonsverdier før kjøring;
4. aldri skrive privat konfigurasjon til Actions-loggen;
5. bygge privat lekkasjekontroll fra `protected_values`, filterregler, `search_queries` og `companies`;
6. kontrollere at disse verdiene ikke finnes i offentlig kildekode;
7. committe bare `state/` til privat runtime;
8. stoppe dersom andre filer er staged;
9. bruke pinnede commit-SHA-er for tredjeparts Actions;
10. validere runtime og adapterinnstillinger før eksterne kilder kontaktes.

Lekkasjekontrollen skal bare rapportere berørte offentlige filstier, aldri selve de private verdiene.

## Runtime

Produksjonsruntime skal være privat. Secrets skal lagres i Actions Secrets i Watchtower-forken, ikke i runtime-repositoryet.

Tillatte top-level-elementer i runtime er:

```text
README.md
.gitignore
config/
state/
```

## Rapportering

Ikke opprett et offentlig issue med:

- en aktiv credential eller webhook;
- privat konfigurasjon;
- Actions-logger som inneholder private verdier;
- state fra en produksjonsinstallasjon.

Ved mistanke om eksponering: deaktiver eller roter credentialen først, og del deretter bare en redigert teknisk beskrivelse med repository-eieren gjennom en privat kanal.
