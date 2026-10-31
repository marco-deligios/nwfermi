# Sicurezza della disconnessione USB

## Correzione 0.6.5.1-1

Il reader bulk-in e' ora ancorato prima di ogni `usb_submit_urb()`. Il
percorso di stop imposta uno stato persistente, cancella in modo sincrono tutti
gli URB ancorati con `usb_kill_anchored_urbs()` e risveglia i reader. La callback
non puo' risottomettere un URB dopo che lo stop ha acquisito tale stato.

`fermi_read()` usa una wait queue con tre condizioni persistenti: FIFO non
vuota, errore USB, oppure disconnessione. Non reinizializza piu' una completion
dopo aver osservato FIFO vuota; questo elimina la finestra di lost wake-up.

Il riferimento aperto dal daemon continua a proteggere `struct usb_fermi` fino
a `release()`. Durante `disconnect()` gli URB vengono terminati prima del
rilascio del riferimento posseduto dalla USB interface.

## Verifiche senza hardware

`tests/test-driver-lifecycle.sh` controlla staticamente il funnel unico di
submit, l'anchor, la cancellazione, le wake-up e il fallimento di probe quando
il reader bulk non parte. La compilazione con `W=1` verifica l'integrazione con
gli header del kernel di destinazione.

Questi controlli non simulano concorrenza reale di HCD, callback in hard/soft
IRQ, rimozione fisica, oppure il comportamento del daemon proprietario. Non
dimostrano quindi che un particolare controller USB non possa bloccarsi: il
collaudo di unplug deve essere svolto soltanto in una fase hardware controllata
dopo l'installazione di un modulo candidato.

## Questione separata: nessun evento in evtest

Il driver ora fallisce `probe()` se l'URB bulk-in iniziale non puo' essere
sottomesso. Restano da verificare con la lavagna il contenuto del flusso USB,
la lettura del daemon e la scrittura dei frame sul nodo `nwfermiN`; questa
correzione non modifica il protocollo daemon-driver ne' garantisce da sola la
presenza di eventi input.
