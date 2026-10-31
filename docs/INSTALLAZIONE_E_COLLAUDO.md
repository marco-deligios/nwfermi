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

Il package verifica staticamente il daemon prima dell'avvio, ma non può provare
senza hardware che il figlio creato dal double-fork resti nel cgroup systemd.
Non dichiarare l'installazione pronta per distribuzione finché il collaudo di
scollegamento e ricollegamento non lo conferma.
