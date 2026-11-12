# Piano di esperimenti per ricostruire il protocollo By-me

Segna l'evento nel programma **appena prima** di eseguire il gesto fisico; ripeti sempre dalla stessa fonte (es. termostato a parete), altrimenti è difficile distinguere chi produce il telegramma.

## Sessione A: modalità ON/OFF della prima stanza

1. Avvia monitor, attendi 15 secondi senza agire: è il **rumore di fondo**.
2. Seleziona la prima stanza e `Termostato a parete`; premi il pulsante di nota `ON`, poi esegui ON sul 14514.
3. Attendi 10–15 secondi.
4. Premi nota `OFF`, esegui OFF sul 14514. Attendi.
5. Ripeti ON → OFF, sempre con la nota premuta prima dell'azione. Attendi almeno 10 secondi tra i cambi.
6. Ferma e scarica lo ZIP.

## Sessione B: variazione setpoint della stessa stanza

1. Metti il 14514 in una modalità dove il setpoint è modificabile, annota modalità, temperatura ambiente e setpoint attuale.
2. Premi `+1°C` nel programma; subito dopo, modifica il setpoint reale di +1 °C. Attendi 10–15 secondi.
3. Premi `−1°C` nel programma; subito dopo riporta il setpoint reale al valore originale.
4. Ripeti una volta e salva ZIP. Evita che altre persone agiscano su altre zone durante la prova.

## Sessione C: stessa azione su tutte e quattro le zone

1. Registra sempre la zona corretta prima dell'azione.
2. Ripeti una prova identica OFF/ON in ciascuna stanza, una alla volta.
3. Confronta quali indirizzi cambiano fra le zone e quali restano identici.

## Sessione D: da dove arriva la priorità sul relè?

1. Lascia il termostato chiedere OFF e registra il log.
2. Marca `ON` con origine `Home Assistant` e accendi **una sola** uscita (solo se è sicuro per il tuo impianto).
3. Non ripetere né contrastare i comandi: attendi che Vimar ripristini il suo stato, e annota la modifica osservata del relè.
4. Esporta il log completo, meglio se includendo alcuni minuti prima e dopo la variazione.

**Attenzione:** poiché controlli testine elettrotermiche di riscaldamento a pavimento, non effettuare prove che possano disattivare protezioni, lasciare una pompa senza flusso o alterare il circuito idraulico. Non cambiare configurazioni bus o firmware per eseguire questi test.

## Quando carichi lo ZIP su ChatGPT

Puoi scrivere: “Analizza gli eventi cronologici e le note del pacchetto ByMe Lab. Per ogni gesto individua i telegrammi candidati entro 12 secondi, raggruppali per sorgente/destinazione e confronta ON/OFF, ritrasmissioni e variazioni dei byte. Distingui le ipotesi dai fatti osservati e proponi il successivo test che permetterebbe di confermare il significato.”