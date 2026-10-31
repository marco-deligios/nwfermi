# Architettura della distribuzione

## Confini di licenza

`nw-fermi-dkms` contiene soltanto il sorgente del modulo presente in questo
repository. `nwfermi-daemon-service` contiene integrazione systemd/udev e non
contiene il daemon NextWindow, gli RPM, SMART Product Drivers, SMART Notebook
o firmware. Le condizioni di redistribuzione di questi binari proprietari non
sono state accertate: ogni amministratore deve procurarsi una copia originale
legittima e indicarne il percorso in `/etc/nwfermi/daemon.conf`.

## Componenti

1. `nw-fermi-dkms` installa quattro file sorgente in
   `/usr/src/nw-fermi-0.6.5.1`; DKMS compila il modulo per ogni kernel con
   header corrispondenti. Non carica né scarica il modulo.
2. `nwfermi-daemon-service` riconosce il class device `nwfermiN` il cui
   antenato USB è `0b8c:0090`, e richiede `nwfermi-daemon@nwfermiN.service`.
3. `nwfermi-daemonctl` deriva `N` dal nome sicuro `nwfermiN`, verifica il nodo
   e invoca il daemon con `/instanceId N`.

La relazione `instanceId=N` è supportata dalla prova statica: il daemon forma
il percorso `/dev/nwfermi%d` e contiene il comando `SetInstanceId`. Il suo
funzionamento con ogni possibile N resta una verifica hardware prevista.

## Perché non una symlink fissa

Una symlink udev semplifica l'uso umano ma non risolve l'argomento numerico del
daemon. L'unità template conserva il suffisso del nome kernel ed evita
l'ipotesi precedente `/dev/nwfermi2`. Il minor USB interno non viene usato.

La versione 1 supporta **una sola SB480 per computer**. `ExecStartPre` crea
atomicamente `/run/nwfermi/active-board`; una seconda unità riceve un errore
esplicito e non avvia né termina il daemon della prima. La directory è creata
come stato volatile globale da `tmpfiles.d`, non da `RuntimeDirectory`: una
template instance fallita non può quindi rimuovere il marker dell'altra. In
arresto systemd termina il cgroup intero e `ExecStopPost` rimuove
esclusivamente il marker del proprio nodo, anche se `/dev/nwfermiN` è già
sparito.

`SYSTEMD_WANTS` avvia soltanto sull'evento udev `add`; non è un meccanismo di
arresto. La regola `remove` marca a sua volta il device per systemd, e
`BindsTo=dev-%i.device` è il collegamento che deve arrestare l'unità quando il
nodo scompare. Questa sequenza è verificata staticamente, ma richiede ancora
il collaudo hardware.

Il daemon è ELF32/i386, usa `daemon()` e non fornisce un PID file osservato.
Il wrapper verifica classe ELF, loader e librerie ma non cerca PID in `/proc`.
`KillMode=control-group` è il meccanismo di arresto; la permanenza del figlio
double-forked nel cgroup è una verifica hardware ancora obbligatoria.

## Stato delle verifiche

**Verificato staticamente:** il modulo crea un class-device `nwfermiN`, la
regola riceve `%k`, il daemon accetta `/instanceId` e costruisce
`/dev/nwfermi%d`; inoltre il payload analizzato è ELF32/i386 e chiama
`daemon()`.

**Verificato con mock:** il wrapper accetta un nodo dinamico, rifiuta il
secondo nodo, non avvia due volte lo stesso nodo, rifiuta nodo/configurazione/
binario mancanti, e completa `stop`/`release` anche dopo la rimozione del nodo.

**Da verificare con la lavagna:** corrispondenza effettiva `%k`→`instanceId`,
visibilità degli attributi USB alla regola, cgroup del processo figlio,
arresto durante unplug, pulizia del marker dopo fallimento/caduta del daemon e
corretto riavvio dopo reconnect.

## Calibrazione futura

Due livelli restano separati:

- calibrazione ottica/controller: daemon NextWindow e `DriverSettings.dat`;
- orientamento display: una configurazione per utente e per identità/layout
  dell'output, non nel modulo kernel.

Una futura utility Wayland dovrà salvare profili associati a connettore,
modalità, scaling e rettangolo dell'immagine proiettata. Con layout differente
il profilo va richiesto nuovamente. SMART Orient potrà essere valutato soltanto
in una sessione Xorg isolata e non deve essere installato dal pacchetto.
