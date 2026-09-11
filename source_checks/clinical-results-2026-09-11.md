# Publiserte kliniske studieresultater

Oppsettet velger ClinicalTrials.gov-studier med norsk studiested, publiserte resultater og siste offentlige oppdatering innenfor et rullerende 14-dagersvindu. Det er et avgrenset oppdateringsutvalg, ikke et historisk totalregister. Identiteten er NCT-ID; første offentlige resultatdato vises som publiseringsdato.

Den eksisterende studieovervåkingen registrerer rekrutteringsstatus, fase, sponsor og om resultater finnes. Den nye funksjonen registrerer faktisk deltakerantall og endringer i publiserte tabeller for deltakertilflyt, resultatmål og rapporterte skadehendelser. Varslene beskriver hvilken tabell som er revidert og viser avgrensede tellinger. Antall hendelsestermer er ikke antall unike pasienter. Overvåkingen vurderer ikke behandlingseffekt eller om behandlingen forårsaket en hendelse.

Sideantall, sidetotal, unike sidetoken, NCT-identiteter, datoer, norsk lokasjon, resultatstatus og byte-/radgrenser valideres. Utvalgte studier må ha faktisk deltakerantall og de forventede resultatmodulene. Ufullstendige uttrekk bevarer tidligere historikk gjennom kildefeil. Et gyldig tomt vindu er tillatt; fravær fra vinduet er ingen slettingspåstand.

Fingeravtrykket dekker normalisert modulinnhold. Ordboknøkler sorteres, og lister med eksplisitte kilde-ID-er normaliseres etter ID. Resultatmål sorteres etter den validerte kombinasjonen type, tittel og tidsramme; dupliserte kombinasjoner avvises. Andre lister beholder kildens rekkefølge; en rekkefølgeendring der regnes derfor som en revisjon. Generell oppdateringsdato brukes til utvalg, men utløser ikke alene varsel. Modulhasher og rå JSON vises ikke i varselteksten.

De to studieoppsettene kan vise samme NCT-lenke for forskjellige endringer. En første resultatpublisering kan sammenfalle med melding om resultattilgjengelighet i den eksisterende funksjonen. Det er ikke innført en bred sperre som også kunne skjult rekrutterings- eller sponsorendringer; full deduplisering på tvers av disse kontraktene er ikke dokumentert.

Offisiell dokumentasjon: [studiedatastruktur](https://clinicaltrials.gov/data-api/about-api/study-data-structure), [resultatdefinisjoner](https://clinicaltrials.gov/prs-info/results-definitions) og [lesing av studieresultater](https://clinicaltrials.gov/study-basics/how-to-read-study-results).

To faktiske poller av den ferdige oppskriften gjennom endringsmotoren ga 15 komplette studieposter hver. Første innlesing ga null varsler; den gjentatte innlesingen ga null varsler og identisk full historikk. 26 målrettede tester av ny og eksisterende klinisk kilde samt oppskriftsintegrasjon bestod. Katalog- og offentlighetskontroll bestod. Hashes, eksakt spørring og tidspunkt er lagret i JSON-beviset. Dette er kilde- og motorbevis, ikke dokumentasjon på en senere ny hendelse eller levert varsel.

To av de 15 studiene utelot minst én av listene over alvorlige eller øvrige hendelser. Kilden tillater dette når den nødvendige hendelsesmodulen og gruppeopplysningene finnes: telleren viser da null oppførte termer. Det dokumenterer ikke null berørte deltakere eller komplette validerte hendelsestotaler.
