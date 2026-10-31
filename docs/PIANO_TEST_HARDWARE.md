# Piano di collaudo con SMART Board SB480

| Fase | Osservazione richiesta | Criterio di successo |
| --- | --- | --- |
| USB | VID/PID e catena udev | `0b8c:0090` porta a `nwfermiN` |
| avvio | journal unità template | un daemon con `/instanceId N` |
| reboot | ordine USB variato | nessun riferimento a `nwfermi2` |
| un dito | evtest/libinput/GNOME | coordinate e rilascio corretti |
| due dita | frame, evtest, libinput | due slot e ID simultanei distinti |
| reconnect | scollega/ricollega | unità vecchia termina, nuova parte |
| marker | errore di avvio, unplug e crash daemon | marker rimosso solo dopo stop dell'unità proprietaria |
| cgroup | `systemctl status` e `systemd-cgls` | figlio daemon resta nel cgroup dell'unità |
| seconda board | collega una seconda SB480 | rifiuto chiaro, primo daemon invariato |
| display | duplicato/esteso, modalità diverse | profilo scelto o richiesto |
| Xorg | Orient tipo NextWindow 4 punti | nessun secondo daemon, mapping corretto |

Non eseguire aggiornatori firmware SMART/NextWindow durante il collaudo.

Le prove di build e lifecycle con mock sono automatizzate; questa tabella
elenca esclusivamente verifiche che richiedono hardware reale.
