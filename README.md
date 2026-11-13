# ByMe Lab

Monitor grafico **locale e passivo** per l'analisi dei telegrammi del bus **Vimar By-me** tramite il gateway **ESP_KNXGate** di Guido Pagani. Sviluppato per indagare la comunicazione fra centrale 21509, termostati 14514 e zone climatiche indipendenti e attuatore 01851.2.

![Python](https://img.shields.io/badge/Python-3.9%2B-blue) ![Stato](https://img.shields.io/badge/Stato-sperimentale-orange)

## Avvio rapido (macOS)

Scarica questo repository come ZIP oppure esegui:

```bash
git clone https://github.com/JRcode197/ByMe-Lab.git
cd ByMe-Lab
python3 byme_lab.py
```

Apri **http://127.0.0.1:8765** se il browser non si apre automaticamente. Non serve `pip install`: usa solo la libreria standard di Python. Il gateway è preimpostato su `192.168.21.113:5045`, modificabile in **Connessione**.

## Funzioni

- Avvia, pausa, riprendi e arresta la cattura TCP del bus.
- Visualizza **tutte** le righe ricevute per impostazione iniziale; puoi attivare i filtri per ritrasmissioni e ACK quando vuoi.
- Annotazioni con zona, origine e azione (ON, OFF, AUTO, MANUALE, setpoint).
- Parsing KNX preliminare, senza inventare significati specifici Vimar.
- Salvataggio locale automatico, download ZIP con eventi cronologici, note e traccia binaria originale (`tcp_stream.bin`).
- Diagnostica: byte TCP durante il monitor, blocchi ricevuti, righe riconosciute e telegrammi validi.
- Censimento automatico delle sorgenti e dei gruppi realmente osservati, collegamenti fra indirizzi, nomi di comodo salvati.
- Importazione di vecchi ZIP ed elenco degli ID EEPROM letto dallo `/status` del gateway.
- Esportazione dell'anagrafica e CSV con nomi di comodo; riferimento a indirizzi/dispositivi nelle annotazioni.

## Versione 1.3: importazione JSON e zone indipendenti

In **Dispositivi → Importa configurazione JSON**, scegli un inventario esportato da ByMe Lab v1 o v2. Il programma valida il file e mostra elementi aggiunti, modificati e rimossi, con valori prima/dopo espandibili. Scegli la modalità e premi **Conferma importazione** solo dopo aver controllato l'anteprima:

- **Unione:** mantiene gli indirizzi assenti dal file; sui duplicati applica nome, categoria, zona, note e verifica del file, anche se vuoti o `false`. Conserva il massimo dei contatori e dei collegamenti osservati, senza sommare snapshot potenzialmente sovrapposti.
- **Sostituzione:** rimpiazza le tre sezioni dispositivi, incluse le osservazioni, con quelle importate. Le rimozioni compaiono nell'anteprima.
- Le zone sono importate se il JSON contiene `climate_zones`: in unione si abbinano per ID stabile; in sostituzione si sostituisce l'elenco. I vecchi JSON v1 senza zone conservano quelle attuali.
- Un backup `dispositivi.backup-<data-ora>.json` viene scritto prima di ogni applicazione. Per ripristinarlo, importalo in modalità sostituzione. Le sessioni registrate non vengono modificate. La cronologia degli ZIP già importati è conservata per evitare doppi conteggi.
- Il monitor deve essere fermo. Un'anteprima scade dopo 15 minuti e diventa inutilizzabile se il catalogo cambia. Cambiando modalità occorre rigenerarla. Nessun dato viene inviato online.

**Zone climatiche** parte da cinque schede e permette di aggiungere/rimuovere zone (da 1 a 100), senza alcun legame con il numero di uscite di un attuatore. Le precedenti quattro etichette vengono conservate e viene aggiunta una quinta zona vuota di associazioni. Ogni scheda contiene nome, termostato 14514, eventuale attuatore (nome/modello), riferimenti tipizzati e indirizzi KNX associati. Termostato, attuatore e ogni indirizzo hanno note e stato di verifica indipendenti. Un riferimento inserito manualmente non è un'osservazione del bus e non incrementa i contatori. Le etichette dei dispositivi e le annotazioni storiche non vengono riscritte quando si rinomina una zona.

Il JSON v2 esportato include anche le schede climatiche; nomi/associazioni si salvano nel catalogo locale `dispositivi.json`. Gli ID EEPROM non vengono convertiti in sorgenti o gruppi KNX. Un gruppo resta una destinazione logica, non l'identità fisica di un termostato o di un attuatore.

### Firmware e avvio Windows

Riferimento originale indicato per il gateway: [papergion/ESPknxgate_32](https://github.com/papergion/ESPknxgate_32). Il firmware dell'impianto di riferimento è **ESP_KNXGATE VER_5.0618**. La versione 1.3 conserva i comandi di setup, monitor e ripristino della 1.2 già utilizzati su quel firmware; non aggiorna il firmware e non aggiunge comandi al bus. Il repository ESP32 non dimostra da solo l'identità del firmware installato.

Su Windows, dalla cartella del progetto: `py byme_lab.py` (oppure `python byme_lab.py` se Python è nel PATH). Per prove isolate si può impostare la variabile `BYME_DATA_DIR` su una cartella separata e usare `--web-port 8766 --no-browser`.

## Nuovo nella versione 1.2: censimento e nomi salvati

Le impostazioni sono suddivise in tre pannelli **Connessione**, **Zone climatiche** e **Dispositivi**.

In **Dispositivi** puoi vedere tre categorie distinte:

- **Sorgenti individuali KNX** osservate nei telegrammi, ad esempio `1.1.42` (possibili dispositivi mittenti).
- **Indirizzi di gruppo KNX**, ad esempio `1/4/137` (destinazioni logiche, NON dispositivi fisici in sé).
- **Identificativi EEPROM ESP_KNXGate** letti via `GET http://<ip>/status`. I loro valori non vengono automaticamente associati a un indirizzo KNX.

Ogni riga può avere un **nome di comodo, categoria, zona e note**, con flag *verificato* solo dopo prove ripetute. I nomi compaiono nel monitor, nei gruppi più attivi, nel menu annotazioni e nei CSV esportati. Una sorgente è associata ai gruppi a cui l'hai vista inviare telegrammi; questa relazione osservata non dimostra il ruolo fisico.

Premi **Importa ZIP sessione** per ricostruire l'inventario da un archivio precedente, senza dover registrare nuovamente. È consentito lo ZIP esportato dal programma e contenente `eventi.jsonl`; una stessa copia esatta non viene conteggiata due volte.

I dati sono salvati in `~/Documents/ByMe-Lab/dispositivi.json`, che puoi esportare anche separatamente come backup. Lo ZIP di una sessione include la copia dei nomi **al momento dell'esportazione** (non è una fotografia storica). I nomi non vengono condivisi con GitHub: sono locali.

**Attenzione:** il censimento è passivo e quindi mostra soltanto gli indirizzi effettivamente osservati. Può non trovare luci, tapparelle o termostati silenziosi. La lettura `/status` è una richiesta HTTP in sola lettura, non un'interrogazione di ciascun dispositivo sul bus.

## Problema del log vuoto: risolto in v1.1.0

La versione precedente riconosceva solo righe terminate da `LF`. ESP_KNXGate può usare anche `CR`, `CRLF` o fornire un frammento senza terminatore. Ora il lettore gestisce questi formati e recupera un frammento incompleto dopo 0,8 secondi di inattività. I filtri sono inizialmente disattivati e vengono salvati i byte TCP grezzi anche in caso di parser non riuscito.

**Come diagnosticare:** se “Byte TCP dal monitor” cresce ma “Telegrammi” resta a zero, il gateway sta trasmettendo dati ma la decodifica non ha ancora riconosciuto i frame; controlla tutte le righe e scarica lo ZIP. Se entrambi restano a zero pur essendo connesso, controlla l'instradamento TCP, la modalità di monitor, gli altri client e `http://192.168.21.113/status`.

## Test offline

```bash
python3 -m unittest discover -s tests -v
```

I test simulano un ESP_KNXGate su socket TCP; **non richiedono collegamento a un impianto reale**.

## Sicurezza e privacy

Il tool non invia telegrammi ON/OFF, ma **invia al gateway comandi di setup e di monitoraggio** che possono modificare temporaneamente la gestione della UART e interferire con Home Assistant. Alla chiusura tenta il ripristino ma non può garantirlo se la connessione cade. Non lasciare prove di acquisizione incustodite e verifica il ritorno al normale stato del gateway. I log rimangono nel Mac (`~/Documents/ByMe-Lab/sessioni`) finché non li condividi manualmente; possono includere informazioni sensibili sul funzionamento dell'abitazione.

Per la guida completa consulta [LEGGIMI_ITALIANO.md](LEGGIMI_ITALIANO.md) e [PIANO_PROVE.md](PIANO_PROVE.md).

**Progetto indipendente, non ufficiale Vimar.** Firmware e sniffer originari: [papergion/scsknxgate](https://github.com/papergion/scsknxgate), [papergion/domotic_bus_sniffer](https://github.com/papergion/domotic_bus_sniffer).