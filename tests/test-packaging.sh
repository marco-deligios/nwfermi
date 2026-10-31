#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

for file in packaging/usr/lib/nwfermi/nwfermi-daemonctl \
            debian/nw-fermi-dkms.postinst \
            debian/nw-fermi-dkms.postrm \
            debian/nwfermi-daemon-service.postinst debian/nwfermi-daemon-service.prerm; do
  sh -n "$file"
done

grep -Fq 'KERNEL_VERSION ?=' nwfermi-0.6.5.0/Makefile
grep -Fq 'KERNEL_SOURCE=$kernel_source_dir' nwfermi-0.6.5.0/dkms.conf
grep -Fq 'USB_DEVICE(0x0b8c, 0x0090)' nwfermi-0.6.5.0/nw-fermi.c
grep -Fq 'input_mt_init_slots' nwfermi-0.6.5.0/nw-fermi.c
grep -Fq 'SUBSYSTEM=="usbmisc"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
grep -Fq 'ATTRS{idVendor}=="0b8c"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
grep -Fq 'ATTRS{idProduct}=="0090"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
grep -Fq 'nwfermi-daemon@%k.service' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
test "$(grep -Fc 'SUBSYSTEM=="usbmisc"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules)" -eq 2
! grep -Fq 'SUBSYSTEM=="usb"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
grep -Fq 'ACTION=="remove"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
grep -Fq 'KillMode=control-group' packaging/lib/systemd/system/nwfermi-daemon@.service
grep -Fq 'ExecStopPost=/usr/lib/nwfermi/nwfermi-daemonctl release %i' packaging/lib/systemd/system/nwfermi-daemon@.service
! grep -F 'RuntimeDirectory=' packaging/lib/systemd/system/nwfermi-daemon@.service
grep -Fq 'ACTION=="remove"' packaging/lib/udev/rules.d/70-nwfermi-sb480.rules
grep -Fq 'd /run/nwfermi 0755 root root -' packaging/usr/lib/tmpfiles.d/nwfermi.conf
grep -Fq 'DAEMON=/usr/local/sbin/nwfermi_daemon' packaging/etc/nwfermi/daemon.conf
grep -Fq 'dkms add -m nw-fermi -v 0.6.5.1' debian/nw-fermi-dkms.postinst
grep -Fq 'dkms autoinstall -m nw-fermi -v 0.6.5.1' debian/nw-fermi-dkms.postinst
! grep -R -F '/dev/nwfermi2' debian packaging
! grep -R -F 'nwfermi-daemon-0.6.5.0-1.fc16' debian packaging

tmp=$(mktemp -d /tmp/nwfermi-packaging-test.XXXXXX)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
mkdir -p "$tmp/dev" "$tmp/state" "$tmp/bin"

cat > "$tmp/bin/readelf" <<'EOF'
#!/bin/sh
case "$1" in
  -h) echo '  Class:                             ELF32' ;;
  -l) echo '      [Requesting program interpreter: /lib/ld-linux.so.2]' ;;
esac
EOF
cat > "$tmp/bin/ldd" <<'EOF'
#!/bin/sh
echo 'libc.so.6 => /lib/i386-linux-gnu/libc.so.6'
EOF
cat > "$tmp/bin/daemon" <<EOF
#!/bin/sh
printf '%s\n' "\$*" >> "$tmp/daemon.args"
EOF
chmod +x "$tmp/bin/readelf" "$tmp/bin/ldd" "$tmp/bin/daemon"
printf 'DAEMON=%s\n' "$tmp/bin/daemon" > "$tmp/daemon.conf"

runctl() {
  NWFERMI_CONFIG="$tmp/daemon.conf" NWFERMI_DEV_DIR="$tmp/dev" \
  NWFERMI_STATE_DIR="$tmp/state" NWFERMI_READELF="$tmp/bin/readelf" \
  NWFERMI_LDD="$tmp/bin/ldd" NWFERMI_I386_LOADER=/bin/sh \
  packaging/usr/lib/nwfermi/nwfermi-daemonctl "$@"
}

# The DKMS package must register its source before autoinstalling.  Exercise
# the maintainer scripts with a mocked dkms command, never the host DKMS tree.
cat > "$tmp/bin/dkms" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$DKMS_LOG"
case "$1" in
  status)
    [ "${DKMS_REGISTERED:-0}" = 1 ] && echo 'nw-fermi/0.6.5.1, test-kernel, x86_64: installed'
    ;;
esac
EOF
chmod +x "$tmp/bin/dkms"
DKMS_LOG="$tmp/dkms-first.log" PATH="$tmp/bin:$PATH" DKMS_REGISTERED=0 \
  sh debian/nw-fermi-dkms.postinst configure
grep -Fxq 'remove -m nw-fermi -v 0.6.5.0 --all' "$tmp/dkms-first.log"
grep -Fxq 'status -m nw-fermi -v 0.6.5.1' "$tmp/dkms-first.log"
grep -Fxq 'add -m nw-fermi -v 0.6.5.1' "$tmp/dkms-first.log"
grep -Fxq 'autoinstall -m nw-fermi -v 0.6.5.1' "$tmp/dkms-first.log"
DKMS_LOG="$tmp/dkms-known.log" PATH="$tmp/bin:$PATH" DKMS_REGISTERED=1 \
  sh debian/nw-fermi-dkms.postinst configure
! grep -Fq 'add -m nw-fermi -v 0.6.5.1' "$tmp/dkms-known.log"
grep -Fxq 'autoinstall -m nw-fermi -v 0.6.5.1' "$tmp/dkms-known.log"
DKMS_LOG="$tmp/dkms-remove.log" PATH="$tmp/bin:$PATH" \
  sh debian/nw-fermi-dkms.postrm remove
grep -Fxq 'remove -m nw-fermi -v 0.6.5.1 --all' "$tmp/dkms-remove.log"

# Dynamic suffix derives from the kernel node, not a hard-coded minor.
: > "$tmp/dev/nwfermi37"
runctl claim nwfermi37
runctl check
runctl start nwfermi37
grep -Fxq '/daemon /instanceId 37' "$tmp/daemon.args"

# A second start needs no new process while the unit's claim still exists.
runctl start nwfermi37
test "$(wc -l < "$tmp/daemon.args")" -eq 1

# Second board is rejected atomically and cannot release the first claim.
: > "$tmp/dev/nwfermi8"
if runctl claim nwfermi8 >"$tmp/second.out" 2>&1; then
  echo 'second board claim unexpectedly succeeded' >&2
  exit 1
fi
grep -Fq 'refusing a second board' "$tmp/second.out"
runctl release nwfermi8
test "$(sed -n '1p' "$tmp/state/active-board/node")" = nwfermi37

# Stop must succeed after unplug and after configuration/binary disappearance.
rm -f "$tmp/dev/nwfermi37" "$tmp/daemon.conf" "$tmp/bin/daemon"
runctl stop nwfermi37
runctl release nwfermi37
test ! -e "$tmp/state/active-board"

# Start fails cleanly for missing node, configuration and binary.
if runctl claim nwfermi4 >/dev/null 2>&1 && runctl start nwfermi4 >"$tmp/missing-node.out" 2>&1; then
  echo 'missing node unexpectedly started' >&2
  exit 1
fi
grep -Fq 'nwfermi4 is unavailable' "$tmp/missing-node.out"
runctl release nwfermi4
: > "$tmp/dev/nwfermi4"
if runctl claim nwfermi4 >/dev/null 2>&1 && runctl start nwfermi4 >"$tmp/missing-config.out" 2>&1; then
  echo 'missing config unexpectedly started' >&2
  exit 1
fi
grep -Fq 'missing ' "$tmp/missing-config.out"
runctl release nwfermi4
printf 'DAEMON=%s\n' "$tmp/bin/missing-daemon" > "$tmp/daemon.conf"
if runctl claim nwfermi4 >/dev/null 2>&1 && runctl start nwfermi4 >"$tmp/missing-daemon.out" 2>&1; then
  echo 'missing daemon unexpectedly started' >&2
  exit 1
fi
grep -Fq 'not executable' "$tmp/missing-daemon.out"
runctl release nwfermi4

echo 'packaging static and lifecycle tests: PASS'
