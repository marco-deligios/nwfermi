#!/usr/bin/python3
"""Four-point libinput calibration for the NextWindow Fermi touchscreen.

This module deliberately keeps hardware access, GTK and the privileged udev
operation separate.  The mathematical and rule-writing functions are usable
from tests without an input device, a display server, or root privileges.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


DEVICE_NAME = "Nextwindow Fermi Touchscreen"
RULE_NAME = "99-nwfermi-calibration.rules"
RULE_DIRECTORY = Path("/etc/udev/rules.d")
INSTALLED_COMMAND = Path("/usr/bin/nwfermi-calibrate")
DEFAULT_MARGIN = 80
MAX_RMS_RESIDUAL = 0.025
MAX_POINT_RESIDUAL = 0.050
MIN_DETERMINANT = 1e-6


class CalibrationError(RuntimeError):
    """A calibration cannot safely produce a libinput matrix."""


@dataclass(frozen=True)
class AxisRange:
    minimum: int
    maximum: int

    def normalize(self, value: int | float) -> float:
        if self.maximum <= self.minimum:
            raise CalibrationError("intervallo ABS non valido")
        return (float(value) - self.minimum) / (self.maximum - self.minimum)


@dataclass(frozen=True)
class FitResult:
    matrix: tuple[float, float, float, float, float, float]
    rms_residual: float
    max_residual: float
    residuals: tuple[float, ...]


def normalized_targets(width: int, height: int, margin: int) -> list[tuple[float, float]]:
    """Return TL, TR, BL, BR target coordinates normalized for libinput."""
    if width <= 0 or height <= 0:
        raise CalibrationError("dimensioni dello schermo non valide")
    if margin < 0 or margin * 2 >= min(width, height):
        raise CalibrationError("margine non valido per le dimensioni dello schermo")
    return [
        (margin / width, margin / height),
        ((width - margin) / width, margin / height),
        (margin / width, (height - margin) / height),
        ((width - margin) / width, (height - margin) / height),
    ]


def _solve_3x3(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> tuple[float, float, float]:
    """Gaussian elimination with partial pivoting; no NumPy dependency."""
    augmented = [list(row) + [value] for row, value in zip(matrix, vector)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            raise CalibrationError("punti degeneri: non è possibile calcolare una trasformazione affine")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        for index in range(column, 4):
            augmented[column][index] /= divisor
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            for index in range(column, 4):
                augmented[row][index] -= factor * augmented[column][index]
    return tuple(augmented[row][3] for row in range(3))  # type: ignore[return-value]


def _least_squares(source: Sequence[tuple[float, float]], values: Sequence[float]) -> tuple[float, float, float]:
    normal = [[0.0] * 3 for _ in range(3)]
    rhs = [0.0] * 3
    for (x, y), value in zip(source, values):
        row = (x, y, 1.0)
        for left in range(3):
            rhs[left] += row[left] * value
            for right in range(3):
                normal[left][right] += row[left] * row[right]
    return _solve_3x3(normal, rhs)


def fit_affine(source: Sequence[tuple[float, float]], target: Sequence[tuple[float, float]]) -> FitResult:
    """Fit normalized source points to normalized display points and validate it."""
    if len(source) != 4 or len(target) != 4:
        raise CalibrationError("servono esattamente quattro punti di calibrazione")
    if any(not all(math.isfinite(value) for value in point) for point in (*source, *target)):
        raise CalibrationError("coordinate non finite")

    a, b, c = _least_squares(source, [point[0] for point in target])
    d, e, f = _least_squares(source, [point[1] for point in target])
    determinant = a * e - b * d
    if determinant <= MIN_DETERMINANT:
        raise CalibrationError(
            "punti invertiti o degeneri: la trasformazione cambierebbe l'orientamento del touchscreen"
        )

    residuals = tuple(
        math.hypot(a * x + b * y + c - u, d * x + e * y + f - v)
        for (x, y), (u, v) in zip(source, target)
    )
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    maximum = max(residuals)
    if rms > MAX_RMS_RESIDUAL or maximum > MAX_POINT_RESIDUAL:
        raise CalibrationError(
            "punti incoerenti: errore RMS %.4f, massimo %.4f (ripetere la calibrazione)" % (rms, maximum)
        )
    return FitResult((a, b, c, d, e, f), rms, maximum, residuals)


def fit_raw_samples(
    samples: Sequence[tuple[int, int]], x_axis: AxisRange, y_axis: AxisRange,
    width: int, height: int, margin: int,
) -> FitResult:
    source = [(x_axis.normalize(x), y_axis.normalize(y)) for x, y in samples]
    return fit_affine(source, normalized_targets(width, height, margin))


def format_matrix(matrix: Sequence[float]) -> str:
    if len(matrix) != 6 or not all(math.isfinite(value) for value in matrix):
        raise CalibrationError("matrice di calibrazione non valida")
    values = tuple(float(value) for value in matrix)
    if values[0] * values[4] - values[1] * values[3] <= MIN_DETERMINANT:
        raise CalibrationError("matrice di calibrazione invertita o degenere")
    if any(abs(value) > 10 for value in values):
        raise CalibrationError("matrice di calibrazione fuori intervallo")
    return " ".join(f"{value:.9g}" for value in values)


def build_rule(matrix: Sequence[float]) -> str:
    """Build the narrowly-scoped input-device rule, never tied to eventN/USB."""
    return (
        '# Generated by nwfermi-calibrate.  Edit only through nwfermi-calibrate.\n'
        'ACTION=="add|change", SUBSYSTEM=="input", KERNEL=="event*", '
        f'ATTRS{{name}}=="{DEVICE_NAME}", '
        f'ENV{{LIBINPUT_CALIBRATION_MATRIX}}="{format_matrix(matrix)}"\n'
    )


def load_rule_matrix(rule_path: Path) -> tuple[float, float, float, float, float, float]:
    """Read the sole calibration value emitted by this utility."""
    try:
        content = Path(rule_path).read_text(encoding="utf-8")
    except OSError as error:
        raise CalibrationError(f"impossibile leggere la regola di calibrazione: {error}") from error
    match = re.search(r'ENV\{LIBINPUT_CALIBRATION_MATRIX\}="([^"]+)"', content)
    if not match:
        raise CalibrationError("LIBINPUT_CALIBRATION_MATRIX non trovata nella regola")
    try:
        values = tuple(float(value) for value in match.group(1).split())
    except ValueError as error:
        raise CalibrationError("matrice nella regola non valida") from error
    try:
        format_matrix(values)
    except CalibrationError as error:
        raise CalibrationError("matrice nella regola non valida") from error
    return values  # type: ignore[return-value]


def evaluate_matrix(
    matrix: Sequence[float], source: Sequence[tuple[float, float]], target: Sequence[tuple[float, float]]
) -> FitResult:
    """Measure an installed matrix against the four known target positions."""
    if len(source) != 4 or len(target) != 4:
        raise CalibrationError("servono esattamente quattro punti di verifica")
    values = tuple(float(value) for value in matrix)
    if len(values) != 6 or not all(math.isfinite(value) for value in values):
        raise CalibrationError("matrice di calibrazione non valida")
    a, b, c, d, e, f = values
    residuals = tuple(
        math.hypot(a * x + b * y + c - u, d * x + e * y + f - v)
        for (x, y), (u, v) in zip(source, target)
    )
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return FitResult(values, rms, max(residuals), residuals)  # type: ignore[arg-type]


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def install_rule(matrix: Sequence[float], rule_dir: Path = RULE_DIRECTORY, reload_rules: bool = True) -> Path:
    """Backup, atomically replace and read-back verify the calibration rule."""
    content = build_rule(matrix)
    rule_dir = Path(rule_dir)
    if not rule_dir.is_dir():
        raise CalibrationError(f"directory regole udev assente: {rule_dir}")
    target = rule_dir / RULE_NAME
    backup = rule_dir / f"{RULE_NAME}.bak"
    if target.exists() and target.read_text(encoding="utf-8") != content:
        shutil.copy2(target, backup)
    _atomic_write(target, content)
    if target.read_text(encoding="utf-8") != content:
        raise CalibrationError("verifica dopo la scrittura della regola udev non riuscita")
    if reload_rules:
        subprocess.run(["udevadm", "control", "--reload-rules"], check=True)
    return target


def restore_rule(rule_dir: Path = RULE_DIRECTORY, reload_rules: bool = True) -> Path:
    rule_dir = Path(rule_dir)
    target = rule_dir / RULE_NAME
    backup = rule_dir / f"{RULE_NAME}.bak"
    if not backup.is_file():
        raise CalibrationError(f"backup non trovato: {backup}")
    _atomic_write(target, backup.read_text(encoding="utf-8"))
    if reload_rules:
        subprocess.run(["udevadm", "control", "--reload-rules"], check=True)
    return target


def load_simulation(path: Path) -> tuple[list[tuple[int, int]], AxisRange, AxisRange]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ranges = data["absinfo"]
        points = [(int(point[0]), int(point[1])) for point in data["points"]]
        return points, AxisRange(int(ranges["min_x"]), int(ranges["max_x"])), AxisRange(
            int(ranges["min_y"]), int(ranges["max_y"])
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CalibrationError(f"file di simulazione non valido: {error}") from error


def discover_device(input_device_class=None, list_devices_fn=None, ecodes_module=None):
    """Find exactly one evdev device by virtual input identity and capabilities."""
    if input_device_class is None or list_devices_fn is None or ecodes_module is None:
        try:
            from evdev import InputDevice, ecodes, list_devices
        except ImportError as error:
            raise CalibrationError("python3-evdev non disponibile") from error
        input_device_class = InputDevice
        list_devices_fn = list_devices
        ecodes_module = ecodes
    matches = []
    denied_paths = []
    for path in list_devices_fn():
        try:
            device = input_device_class(path)
            capabilities = device.capabilities(absinfo=True)
            abs_codes = {entry[0] if isinstance(entry, tuple) else entry
                         for entry in capabilities.get(ecodes_module.EV_ABS, [])}
            key_codes = set(capabilities.get(ecodes_module.EV_KEY, []))
            # The driver gives this virtual device INPUT_PROP_DIRECT plus the
            # two legacy axes and BTN_TOUCH.  Do not use its transient eventN
            # node or the physical USB parent as identity.
            direct = ecodes_module.INPUT_PROP_DIRECT in device.capabilities().get(ecodes_module.EV_PROP, [])
            if (device.name == DEVICE_NAME and direct and ecodes_module.ABS_X in abs_codes
                    and ecodes_module.ABS_Y in abs_codes and ecodes_module.BTN_TOUCH in key_codes):
                matches.append(device)
            else:
                device.close()
        except PermissionError:
            denied_paths.append(path)
            continue
        except OSError:
            continue
    if not matches and denied_paths:
        paths = ", ".join(denied_paths)
        raise CalibrationError(
            "permesso negato nell'accesso a dispositivi evdev (%s). "
            "Accedere dalla sessione grafica locale affinché logind assegni l'ACL al suo utente; "
            "non eseguire la GUI come root e non modificare i permessi del dispositivo" % paths
        )
    if not matches:
        raise CalibrationError(f"nessun dispositivo evdev compatibile chiamato {DEVICE_NAME!r}")
    if len(matches) != 1:
        names = ", ".join(device.path for device in matches)
        for device in matches:
            device.close()
        raise CalibrationError(f"trovati più touchscreen compatibili ({names}); rimuovere l'ambiguità")
    return matches[0]


class EvdevCapture:
    """Threaded, explicitly-armed state machine for one touchscreen point.

    ``IDLE`` and ``CAPTURED`` never become armed from a release event.  Only
    :meth:`arm_point`, called by the GUI when it displays a target, may enter
    ``ARMED``.  A pre-existing touch enters ``WAIT_RELEASE`` and must be
    released before a later explicit arm can accept another touch.
    """

    IDLE = "idle"
    WAIT_RELEASE = "wait-release"
    ARMED = "armed"
    PRESS_PENDING = "press-pending"
    CAPTURED = "captured"
    CLOSED = "closed"

    def __init__(self, device, on_sample: Callable[[int, int, int], None], on_error: Callable[[str], None],
                 ecodes_module=None, on_release_ready: Callable[[int], None] | None = None):
        if ecodes_module is None:
            from evdev import ecodes as ecodes_module
        self.device = device
        self.ecodes = ecodes_module
        self.on_sample = on_sample
        self.on_error = on_error
        self.on_release_ready = on_release_ready or (lambda _generation: None)
        # Values become usable only after the SYN_REPORT that confirms their
        # frame.  A missing ABS axis then means "unchanged", as specified by
        # evdev, not an opportunity to combine two unconfirmed updates.
        self.confirmed_x = None
        self.confirmed_y = None
        self.frame_x = None
        self.frame_y = None
        self.frame_has_x = False
        self.frame_has_y = False
        self.touch_down = False
        self.state = self.IDLE
        self.generation = 0
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="nwfermi-evdev-capture", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def arm_point(self) -> int:
        """Explicitly arm the currently displayed target and return its token."""
        with self.lock:
            if self.closed.is_set():
                raise CalibrationError("acquisizione evdev già chiusa")
            self.generation += 1
            self.state = self.WAIT_RELEASE if self.touch_down else self.ARMED
            return self.generation

    def snapshot(self) -> tuple[str, int]:
        with self.lock:
            return self.state, self.generation

    def close(self) -> None:
        self.closed.set()
        with self.lock:
            self.state = self.CLOSED
        try:
            self.device.close()
        except OSError:
            pass

    def process_event(self, event) -> None:
        """Process one event; public to permit deterministic fake-device tests."""
        sample = None
        release_ready = None
        with self.lock:
            if self.state == self.CLOSED:
                return
            if event.type == self.ecodes.EV_ABS:
                if event.code == self.ecodes.ABS_X:
                    self.frame_x = event.value
                    self.frame_has_x = True
                elif event.code == self.ecodes.ABS_Y:
                    self.frame_y = event.value
                    self.frame_has_y = True
            elif event.type == self.ecodes.EV_KEY and event.code == self.ecodes.BTN_TOUCH:
                if event.value == 0:
                    self.touch_down = False
                    # Release is deliberately passive: it never re-arms.
                    if self.state == self.WAIT_RELEASE:
                        self.state = self.IDLE
                        release_ready = self.generation
                    elif self.state == self.PRESS_PENDING:
                        self.state = self.IDLE
                elif event.value:
                    self.touch_down = True
                    if self.state == self.ARMED:
                        self.state = self.PRESS_PENDING
            elif event.type == self.ecodes.EV_SYN and event.code == self.ecodes.SYN_REPORT:
                frame_has_update = self.frame_has_x or self.frame_has_y
                if self.frame_has_x:
                    self.confirmed_x = self.frame_x
                if self.frame_has_y:
                    self.confirmed_y = self.frame_y
                if (self.state == self.PRESS_PENDING and frame_has_update
                        and self.confirmed_x is not None and self.confirmed_y is not None):
                    sample = (self.confirmed_x, self.confirmed_y, self.generation)
                    self.state = self.CAPTURED
                self.frame_has_x = False
                self.frame_has_y = False
                self.frame_x = None
                self.frame_y = None
            elif (event.type == self.ecodes.EV_SYN
                  and event.code == getattr(self.ecodes, "SYN_DROPPED", -1)):
                # We cannot know which ABS events were lost.  Do not combine
                # a later one-axis update with an old coordinate.
                self.confirmed_x = None
                self.confirmed_y = None
                self.frame_x = None
                self.frame_y = None
                self.frame_has_x = False
                self.frame_has_y = False
        if sample is not None:
            self.on_sample(*sample)
        if release_ready is not None:
            self.on_release_ready(release_ready)

    def _run(self) -> None:
        try:
            for event in self.device.read_loop():
                if self.closed.is_set():
                    return
                self.process_event(event)
        except OSError as error:
            if not self.closed.is_set():
                with self.lock:
                    self.state = self.CLOSED
                self.on_error(f"lettura evdev interrotta: {error}")


def run_gtk_calibration(args: argparse.Namespace) -> int:
    """Run as a regular desktop user; root is reserved for the narrow helper."""
    if os.geteuid() == 0:
        raise CalibrationError("avviare la GUI come utente della sessione GNOME, non come root")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as error:
        raise CalibrationError("GTK4/PyGObject non disponibili") from error

    device = discover_device()
    try:
        from evdev import ecodes
        x_info = device.absinfo(ecodes.ABS_X)
        y_info = device.absinfo(ecodes.ABS_Y)
        x_axis = AxisRange(x_info.min, x_info.max)
        y_axis = AxisRange(y_info.min, y_info.max)
    except Exception:
        device.close()
        raise

    class CalibrationApplication(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.smartboard.nwfermi.Calibrate")
            self.window = None
            self.area = None
            self.status = None
            self.capture = None
            self.samples: list[tuple[int, int]] = []
            self.point_index = 0
            self.expected_generation = None
            self.pending_matrix = None

        def do_activate(self):
            self.window = Gtk.ApplicationWindow(application=self, title="Calibrazione SMART Board")
            self.window.set_decorated(False)
            self.window.fullscreen()
            # The drawing surface must cover the whole monitor: its geometry
            # is the destination coordinate system of the libinput matrix.
            overlay = Gtk.Overlay()
            self.area = Gtk.DrawingArea(vexpand=True, hexpand=True)
            self.area.set_draw_func(self.draw_cross)
            self.status = Gtk.Label(label="Preparazione della calibrazione…", wrap=True)
            self.status.set_margin_top(12)
            self.status.set_margin_bottom(18)
            self.status.set_halign(Gtk.Align.CENTER)
            self.status.set_valign(Gtk.Align.END)
            overlay.set_child(self.area)
            overlay.add_overlay(self.status)
            self.window.set_child(overlay)
            key = Gtk.EventControllerKey()
            key.connect("key-pressed", self.on_key)
            self.window.add_controller(key)
            self.window.present()
            self.capture = EvdevCapture(device, lambda x, y, generation: GLib.idle_add(
                self.got_sample, x, y, generation),
                                        lambda message: GLib.idle_add(self.capture_failed, message),
                                        on_release_ready=lambda generation: GLib.idle_add(
                                            self.release_ready, generation))
            self.capture.start()
            GLib.idle_add(self.next_point)

        def on_key(self, _controller, keyval, _keycode, _state):
            if keyval == 0xff1b:  # Escape
                self.quit()
                return True
            if keyval in (ord("r"), ord("R")):
                if self.pending_matrix is not None:
                    self.pending_matrix = None
                    self.samples = []
                    self.point_index = 0
                if self.point_index >= 4:
                    return True
                self.samples = self.samples[:self.point_index]
                self.arm_current_point()
                self.status.set_label("Punto annullato. Toccare di nuovo la croce corrente.")
                return True
            if keyval == 0xff0d and self.pending_matrix is not None:
                self.apply_matrix(self.pending_matrix)
                return True
            return False

        def draw_cross(self, _area, context, width, height):
            if self.point_index >= 4:
                return
            try:
                targets = normalized_targets(width, height, args.margin)
            except CalibrationError:
                return
            x, y = targets[self.point_index]
            x *= width
            y *= height
            context.set_source_rgb(1.0, 0.15, 0.15)
            context.set_line_width(4)
            context.move_to(x - 30, y)
            context.line_to(x + 30, y)
            context.move_to(x, y - 30)
            context.line_to(x, y + 30)
            context.stroke()
            context.select_font_face("Sans")
            context.set_font_size(28)
            context.move_to(x + 38, y - 16)
            context.show_text(str(self.point_index + 1))

        def next_point(self):
            if self.point_index >= 4:
                self.finish()
                return False
            labels = ("alto sinistra", "alto destra", "basso sinistra", "basso destra")
            self.status.set_label(
                f"Punto {self.point_index + 1}/4 ({labels[self.point_index]}). "
                "Toccare e rilasciare la croce. R ripete il punto, Esc annulla."
            )
            self.arm_current_point()
            self.area.queue_draw()
            return False

        def arm_current_point(self):
            self.pending_matrix = None
            self.expected_generation = self.capture.arm_point()

        def got_sample(self, x, y, generation):
            if self.point_index >= 4 or generation != self.expected_generation:
                return False
            self.expected_generation = None
            self.samples.append((x, y))
            self.point_index += 1
            return self.next_point()

        def release_ready(self, generation):
            # The evdev thread only reports a release.  The GUI performs the
            # explicit re-arm and rejects stale notifications from a retry.
            if self.point_index < 4 and generation == self.expected_generation:
                self.arm_current_point()
                self.status.set_label("Toccare la croce corrente.")
            return False

        def capture_failed(self, message):
            self.expected_generation = None
            self.pending_matrix = None
            self.status.set_label(message)
            return False

        def finish(self):
            width = self.area.get_width()
            height = self.area.get_height()
            try:
                source = [(x_axis.normalize(x), y_axis.normalize(y)) for x, y in self.samples]
                target = normalized_targets(width, height, args.margin)
                result = (evaluate_matrix(load_rule_matrix(args.rule), source, target)
                          if args.verify else fit_affine(source, target))
            except CalibrationError as error:
                if args.verify:
                    self.status.set_label(f"Verifica non eseguibile: {error}.")
                    return
                self.status.set_label(f"Calibrazione rifiutata: {error}. Ripetere il quarto punto.")
                self.point_index = 3
                self.samples = self.samples[:3]
                self.arm_current_point()
                self.area.queue_draw()
                return
            matrix = format_matrix(result.matrix)
            text = (f"Matrice calcolata: {matrix}\n"
                    f"Errore RMS: {result.rms_residual:.5f}; massimo: {result.max_residual:.5f}.")
            if args.dry_run or args.verify:
                verdict = "precisione accettabile" if result.max_residual <= MAX_POINT_RESIDUAL else "precisione insufficiente"
                prefix = "Verifica della matrice installata" if args.verify else "Calcolo preliminare"
                self.status.set_label(f"{prefix}: {verdict}.\n{text}\nNessuna modifica applicata.")
                return
            self.pending_matrix = result.matrix
            self.status.set_label(text + "\nPremere Invio per applicare, R per ricominciare, Esc per annullare.")

        def apply_matrix(self, matrix):
            if self.pending_matrix != matrix:
                return
            self.pending_matrix = None
            if not INSTALLED_COMMAND.is_file() or not os.access(INSTALLED_COMMAND, os.X_OK):
                self.status.set_label("Applicazione non disponibile: installare il pacchetto nwfermi-daemon-service.")
                return
            command = [str(INSTALLED_COMMAND), "--install-rule", *format_matrix(matrix).split()]
            completed = subprocess.run(["pkexec", *command], text=True, capture_output=True)
            if completed.returncode:
                self.status.set_label("Applicazione non riuscita: " + (completed.stderr.strip() or "autorizzazione negata"))
            else:
                self.status.set_label(
                    "Regola udev salvata e ricaricata. Chiudere e riaprire la sessione GNOME per garantire "
                    "che libinput rilegga la calibrazione; non è stato riavviato alcun driver."
                )
        def do_shutdown(self):
            if self.capture:
                self.capture.close()
            else:
                device.close()
            Gtk.Application.do_shutdown(self)

    return CalibrationApplication().run([])


def print_result(result: FitResult) -> None:
    print("LIBINPUT_CALIBRATION_MATRIX=" + format_matrix(result.matrix))
    print("RMS_RESIDUAL=%.8f" % result.rms_residual)
    print("MAX_RESIDUAL=%.8f" % result.max_residual)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--margin", type=int, default=DEFAULT_MARGIN, help="margine delle croci in pixel (predefinito: 80)")
    parser.add_argument("--width", type=int, default=1920, help="larghezza per --simulate (predefinita: 1920)")
    parser.add_argument("--height", type=int, default=1080, help="altezza per --simulate (predefinita: 1080)")
    parser.add_argument("--simulate", type=Path, metavar="FILE", help="JSON con absinfo e quattro punti raw; non accede all'hardware")
    parser.add_argument("--rule", type=Path, default=RULE_DIRECTORY / RULE_NAME,
                        help="regola da leggere con --verify (predefinita: /etc/udev/rules.d/99-nwfermi-calibration.rules)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="calcola e mostra, senza scrivere la regola")
    action.add_argument("--verify", action="store_true", help="misura una calibrazione senza modificarla")
    action.add_argument("--restore", action="store_true", help="ripristina il backup della regola precedente")
    action.add_argument("--install-rule", nargs=6, type=float, metavar=("A", "B", "C", "D", "E", "F"), help=argparse.SUPPRESS)
    action.add_argument("--restore-rule", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.install_rule is not None:
            if os.geteuid() != 0:
                raise CalibrationError("l'helper --install-rule deve essere eseguito tramite pkexec")
            install_rule(args.install_rule)
            return 0
        if args.restore_rule:
            if os.geteuid() != 0:
                raise CalibrationError("l'helper --restore-rule deve essere eseguito tramite pkexec")
            restore_rule()
            return 0
        if args.restore:
            if not INSTALLED_COMMAND.is_file() or not os.access(INSTALLED_COMMAND, os.X_OK):
                raise CalibrationError("comando installato non disponibile; installare nwfermi-daemon-service")
            return subprocess.run(["pkexec", str(INSTALLED_COMMAND), "--restore-rule"], check=False).returncode
        if args.simulate:
            points, x_axis, y_axis = load_simulation(args.simulate)
            source = [(x_axis.normalize(x), y_axis.normalize(y)) for x, y in points]
            target = normalized_targets(args.width, args.height, args.margin)
            print_result(evaluate_matrix(load_rule_matrix(args.rule), source, target)
                         if args.verify else fit_affine(source, target))
            return 0
        return run_gtk_calibration(args)
    except (CalibrationError, OSError, subprocess.CalledProcessError) as error:
        print(f"nwfermi-calibrate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
