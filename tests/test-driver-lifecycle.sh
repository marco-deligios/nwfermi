#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
src="$root/nwfermi-0.6.5.0/nw-fermi.c"

# These checks deliberately exercise only the source-level lifecycle contract.
# They neither load the module nor issue a USB unbind/disconnect.
grep -Fq 'init_usb_anchor(&dev->submitted)' "$src"
grep -Fq 'usb_anchor_urb(urb, &dev->submitted)' "$src"
grep -Fq 'usb_unanchor_urb(urb)' "$src"
grep -Fq 'usb_kill_anchored_urbs(&dev->submitted)' "$src"
grep -Fq 'wait_queue_head_t' "$src"
grep -Fq 'wait_event_interruptible(dev->bulk_in_wait' "$src"
grep -Fq 'wake_up_interruptible_all(&dev->bulk_in_wait)' "$src"
grep -Fq 'WRITE_ONCE(dev->disconnected, true)' "$src"
grep -Fq 'disconnect may have run after usb_find_interface() returned' "$src"
grep -Fq 'fermi_stop_bulk_reads(dev, -ENODEV)' "$src"
grep -Fq 'retval = fermi_start_bulk_reads(dev);' "$src"
grep -Fq 'if (retval) {' "$src"
! grep -Fq 'bulk_in_completion' "$src"
! grep -Fq 'usb_wait_anchor_empty_timeout' "$src"

# There must be one bulk submit funnel; both initial submission and completion
# resubmission use it so that stop and submit cannot race.
test "$(grep -Fc 'usb_submit_urb(urb, GFP_ATOMIC)' "$src")" -eq 1
test "$(grep -Fc 'fermi_submit_bulk_urb(dev, urb)' "$src")" -eq 2

echo 'driver USB lifecycle static tests: PASS'
