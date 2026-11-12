# ByMe Lab — monitor grafico per ESP_KNXGate

**Per il tuo impianto:** centrale Vimar 21509, quattro cronotermostati Plana 14514, attuatore 01851.2 (4 testine per pavimento radiante), gateway ESP_KNXGate su `192.168.21.113:5045`.

## Avvio su iMac

1. Estrai il file ZIP in una cartella sul Desktop, ad esempio `BYME_Lab`.
2. Apri **Terminale** ed esegui (adatta il percorso se necessario):

   ```bash
   cd ~/Desktop/BYME_Lab
   python3 byme_lab.py
   ```

   Si apre automaticamente il browser alla pagina **http://127.0.0.1:8765**. Nessun server esterno, nessuna libreria da installare, nessuna modifica a Home Assistant.
3. In alternativa, fai doppio clic su `Avvia_BYME_Lab.command` dopo aver abilitato l'esecuzione (se richiesto):

   ```bash
   chmod +x ~/Desktop/BYME_Lab/Avvia_BYME_Lab.command
   ```

   Se macOS ne blocca l'apertura, usa il comando Python sopra.

## Uso suggerito

1. Imposta IP `192.168.21.113`, porta `5045` e personalizza i nomi delle quattro zone in **Impostazioni**.
2. Dai un nome alla prova, es. `Soggiorno ON OFF`.
3. Premi **Avvia** e attendi `Acquisizione in corso`.
4. Scegli la zona e la sorgente (termostato parete, centrale o Home Assistant).
5. **Premi il pulsante annotazione ON/OFF immediatamente prima di eseguire il gesto sul dispositivo reale**, e lascia 10–15 secondi tra una prova e la successiva. La pressione del pulsante nel browser **non invia ON/OFF al bus**.
6. Premi **Ferma**. Attendi il messaggio di ripristino `#ok ricevuto`.
7. Premi **Scarica pacchetto ZIP**. Allega lo ZIP nella conversazione ChatGPT in cui stiamo ricostruendo il protocollo.

## Pausa

**Pausa** scarta i telegrammi mentre la connessione TCP resta attiva; **Riprendi** ricomincia la registrazione, annotando l'intervallo saltato. **Congela vista** invece blocca soltanto la visualizzazione: il file continua a registrare. Per prove critiche, preferisci **Congela vista**.

## File e privacy

I file vengono scritti **anche mentre la registrazione è in corso** in `~/Documents/ByMe-Lab/sessioni`. La sessione produce `eventi.jsonl`, `bus_raw.txt` e `sessione.json`; lo ZIP aggiunge `telegrammi.csv`, `riepilogo.json`, `note_esperimento.txt` ed `esperimenti.json` (telegrammi entro 12 secondi dopo ciascuna annotazione, senza supporre causalità).

**Non viene inviato automaticamente nulla a ChatGPT**. Scarica lo ZIP e allegalo tu. L'interfaccia web è accessibile solo sul Mac che esegue lo script (`127.0.0.1`). Le note e i telegrammi possono rivelare struttura e abitudini d'uso dell'impianto.

## Sicurezza e limiti

- Il programma è **passivo rispetto al comando dei relè e delle testine**: invia solo i comandi tecnici `#setup`, inizio monitor (come lo sniffer originale di Guido Pagani), e ripristino TCP.
- Mentre il gateway è configurato per `uart:tcp`, il traffico dell'integrazione MQTT/Home Assistant **può essere influenzato**. Evita acquisizioni prolungate non presidiate e verifica `http://192.168.21.113/status` dopo la chiusura.
- Se si interrompe la corrente, il computer o la rete, il ripristino del gateway non può essere garantito. Controlla `/status`.
- Il parser interpreta solo campi convenzionali di telegrammi KNX: indirizzo sorgente e destinazione, APCI, bit di ritrasmissione, checksum, payload grezzo. **Non conosce automaticamente il significato dei telegrammi proprietari By-me**, né le temperature e i setpoint, senza prove annotate.
- Alcuni dati diagnostici (`KNX[x]`, `CC`, messaggi del firmware) vengono conservati integralmente. Le etichette `KNX[x]` non sono considerate lunghezze di pacchetto.
- Nessuna funzione nel programma scrive valori ON/OFF, setpoint o altri comandi a un dispositivo Vimar.

## Novità 1.1: correzione log vuoto e diagnostica

- Parsing TCP robusto con righe terminate da `\r`, `\n`, `\r\n` oppure senza terminatore (frammento elaborato dopo 0,8 s di inattività).
- **Filtri disattivati per impostazione iniziale**: tutte le ritrasmissioni, conferme e righe non decodificate rimangono visibili.
- Contatori **Byte TCP ricevuti**, **Blocchi TCP**, **Righe riconosciute** e ora dell'ultima ricezione. Se Byte TCP cresce ma Telegrammi resta 0, il problema è di interpretazione e non di connessione.
- Esportazione aggiuntiva del file binario `tcp_stream.bin` per conservare tutti i byte ricevuti e consentire indagini forensi.
- Le modifiche non eseguono alcun comando ON/OFF sui dispositivi Vimar; soltanto inizializzazione e chiusura del monitor.

## Risoluzione problemi

- `#err`: la modalità TCP non è stata accettata; controlla firmware e stato gateway. Non modificare il firmware senza necessità.
- Indirizzo occupato 8765: chiudi un'altra istanza o esegui `python3 byme_lab.py --web-port 8766`.
- Porta 5045 occupata / nessun dato: chiudi altri sniffer o programmi che usano la stessa UART del gateway.
- Python non apre il browser: visita manualmente `http://127.0.0.1:8765`.
- Se l'interfaccia è ferma, non chiudere subito il Terminale: attendi che il ripristino del gateway venga confermato o verifica `/status`.

## Fonti di compatibilità

- [Sniffer Guido Pagani](https://github.com/papergion/domotic_bus_sniffer)
- [Firmware ESP_KNXGate](https://github.com/papergion/scsknxgate)

Questa applicazione è un progetto indipendente per analisi locale e non è un prodotto ufficiale Vimar o Guido Pagani.

## Sessioni precedenti

Il menu **Quale sessione vuoi scaricare?** elenca anche le acquisizioni salvate nei giorni precedenti. Puoi riaprire il programma e creare lo ZIP di una sessione vecchia senza dover ripetere il test. I file restano in `~/Documents/ByMe-Lab/sessioni` anche dopo aver chiuso ByMe Lab.

Gli orari dei telegrammi sono quelli di **ricezione sul Mac** (non timestamp generati dal cronotermostato), quindi potrebbero differire leggermente dagli eventi reali per latenza di gateway e rete.