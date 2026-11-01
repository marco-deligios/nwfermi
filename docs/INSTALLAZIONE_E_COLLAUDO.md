# Installazione e collaudo

## Requisiti

- Ubuntu con header del kernel in esecuzione e DKMS;
- una copia originale del daemon `nwfermi_daemon`, ottenuta
  legittimamente dall'amministratore; attenzione: il payload analizzato è
  ELF32/i386 e richiede il loader `/lib/ld-linux.so.2` e le librerie i386;
- lavagna SB480 collegata via USB; Secure Boot configurato per permettere il
  caricamento di moduli DKMS firmati/localmente autorizzati.

## Installazione prevista

1. Costruire i `.deb` con `dpkg-buildpackage -us -uc` su una macchina di build
   con `debhelper` e `dkms`.
2. Installare `nw-fermi-dkms` e verificare la compilazione DKMS per il kernel.
3. Installare `nwfermi-daemon-service`.
4. Copiare il daemon originale in `/usr/local/sbin/nwfermi_daemon`, renderlo eseguibile e
   impostare `DAEMON=` in `/etc/nwfermi/daemon.conf`.
5. Collegare una sola lavagna. udev richiederà l'unità template. Non mantenere in
   parallelo il vecchio `nwfermi.service` con istanza fissa.

## Diagnostica con la lavagna

Usare `journalctl -u 'nwfermi-daemon@*' -b`, `dkms status`, `lsmod`,
`udevadm info --name=/dev/nwfermiN --attribute-walk`, `libinput debug-events`
ed `evtest`. Registrare separatamente presenza USB, nodo, daemon, eventi
evdev, slot MT, ID e comportamento GNOME.

Per il dual touch, non modificare `DefaultDriverSettings.dat`. Prima osservare
uno e due contatti nei frame daemon→modulo, poi `ABS_MT_SLOT`,
`ABS_MT_TRACKING_ID`, X/Y e `BTN_TOUCH` in evtest, quindi libinput e GNOME.

## Aggiornamenti, rimozione e problemi noti

Gli aggiornamenti kernel attivano DKMS se sono disponibili gli header. Secure
Boot può richiedere firma/enrollment MOK: seguire la procedura Ubuntu e non
disabilitare la protezione. Prima della rimozione scollegare la lavagna o
fermare le unità template; il modulo non viene scaricato automaticamente.
SMART Orient e SMARTBoardService non fanno parte del pacchetto e non sono
supportati sotto Wayland senza collaudo Xorg isolato.

## Calibrazione geometrica Wayland/libinput

`nwfermi-calibrate` non avvia il daemon proprietario e non modifica il modulo
kernel. Va eseguito dall'utente della sessione GNOME, dopo che il dispositivo
input `Nextwindow Fermi Touchscreen` è stato creato:

```bash
nwfermi-calibrate
```

L'applicazione GTK4 a schermo intero richiede quattro tocchi nell'ordine
alto-sinistra, alto-destra, basso-sinistra, basso-destra. Le croci hanno un
margine di 80 pixel, modificabile con `--margin`. Le coordinate provengono
direttamente da evdev (`ABS_X`, `ABS_Y`, `BTN_TOUCH`, `SYN_REPORT`), non da
GTK. `R` ripete il punto corrente e `Esc` annulla.

La matrice affine di libinput è stimata sui quattro punti normalizzati. Se
`(x, y)` sono le coordinate raw normalizzate da `ABS_X/ABS_Y` e `(u, v)` sono
le coordinate normalizzate dello schermo, la matrice è nel verso libinput:
`u=a*x+b*y+c`, `v=d*x+e*y+f`. Punti incompleti, degeneri, invertiti o con
residui incoerenti sono rifiutati.
Prima della scrittura viene mostrata la matrice e l'utente deve premere Invio.
Solo allora `pkexec` esegue l'helper ristretto che salva, con backup e
sostituzione atomica, `/etc/udev/rules.d/99-nwfermi-calibration.rules`.
La regola identifica `ATTRS{name}=="Nextwindow Fermi Touchscreen"`; non
dipende da `eventN`, porta USB o identificatore USB.

Comandi senza modifica della configurazione:

```bash
nwfermi-calibrate --dry-run
nwfermi-calibrate --verify     # misura la matrice già installata
nwfermi-calibrate --simulate tests/fixtures/calibration-regression.json
```

`--verify` confronta i quattro tocchi con la matrice già salvata nella regola
(per test offline si può indicare `--rule FILE`). `--restore` richiede
autorizzazione e ripristina il backup della regola.
Dopo un'applicazione la regola viene ricaricata con `udevadm control
--reload-rules`; chiudere e riaprire la sessione GNOME per garantire che
libinput rilegga la proprietà. Lo strumento non forza logout, reboot,
ricaricamento del driver o riavvio del daemon.

La calibrazione reale, l'accesso evdev dell'utente e il momento effettivo in
cui GNOME/libinput applica una nuova regola restano da verificare con la
lavagna collegata.

Il package verifica staticamente il daemon prima dell'avvio, ma non può provare
senza hardware che il figlio creato dal double-fork resti nel cgroup systemd.
Non dichiarare l'installazione pronta per distribuzione finché il collaudo di
scollegamento e ricollegamento non lo conferma.
