#!/usr/bin/python3
"""Unit tests for calibration maths and udev rule management; no hardware."""
import importlib.util
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nwfermi_calibrate", ROOT / "calibrate" / "nwfermi_calibrate.py")
cal = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = cal
SPEC.loader.exec_module(cal)


class AffineFitTests(unittest.TestCase):
    def test_known_matrix_is_recovered(self):
        matrix = (1.034, 0.0, -0.022, 0.0, 1.392, -0.213)
        targets = cal.normalized_targets(1920, 1080, 80)
        source = [((u - matrix[2]) / matrix[0], (v - matrix[5]) / matrix[4]) for u, v in targets]
        result = cal.fit_affine(source, targets)
        for got, expected in zip(result.matrix, matrix):
            self.assertAlmostEqual(got, expected, places=8)
        self.assertLess(result.max_residual, 1e-8)

    def test_simulated_regression_samples(self):
        points, x_axis, y_axis = cal.load_simulation(ROOT / "tests/fixtures/calibration-regression.json")
        result = cal.fit_raw_samples(points, x_axis, y_axis, 1920, 1080, 80)
        expected = (1.034, 0.0, -0.022, 0.0, 1.392, -0.213)
        for got, value in zip(result.matrix, expected):
            self.assertAlmostEqual(got, value, delta=0.001)

    def test_noisy_but_coherent_samples_are_accepted(self):
        targets = cal.normalized_targets(1920, 1080, 80)
        noise = ((0.001, -0.001), (-0.001, 0.001), (0.001, 0.001), (-0.001, -0.001))
        samples = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(targets, noise)]
        result = cal.fit_affine(samples, targets)
        self.assertLess(result.rms_residual, cal.MAX_RMS_RESIDUAL)

    def test_rejects_inverted_points(self):
        targets = cal.normalized_targets(1920, 1080, 80)
        inverted = [targets[1], targets[0], targets[3], targets[2]]
        with self.assertRaises(cal.CalibrationError):
            cal.fit_affine(inverted, targets)

    def test_rejects_incomplete_degenerate_and_incoherent_samples(self):
        targets = cal.normalized_targets(1920, 1080, 80)
        with self.assertRaises(cal.CalibrationError):
            cal.fit_affine(targets[:3], targets[:3])
        with self.assertRaises(cal.CalibrationError):
            cal.fit_affine([(0.5, 0.5)] * 4, targets)
        with self.assertRaises(cal.CalibrationError):
            cal.fit_affine([(0, 0), (1, 0), (0, 1), (0.9, 0.9)], targets)


class UdevRuleTests(unittest.TestCase):
    def test_rule_is_specific_and_restorable(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / cal.RULE_NAME
            original = "old calibration\n"
            target.write_text(original, encoding="utf-8")
            cal.install_rule((1, 0, 0, 0, 1, 0), directory, reload_rules=False)
            content = target.read_text(encoding="utf-8")
            self.assertIn('SUBSYSTEM=="input"', content)
            self.assertIn('ATTRS{name}=="Nextwindow Fermi Touchscreen"', content)
            self.assertNotIn("event0", content)
            self.assertNotIn("0b8c", content)
            self.assertEqual((directory / (cal.RULE_NAME + ".bak")).read_text(encoding="utf-8"), original)
            self.assertEqual(cal.load_rule_matrix(target), (1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
            cal.install_rule((1, 0, 0, 0, 1, 0), directory, reload_rules=False)
            self.assertEqual((directory / (cal.RULE_NAME + ".bak")).read_text(encoding="utf-8"), original)
            cal.restore_rule(directory, reload_rules=False)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_rule_rejects_invalid_matrix(self):
        with self.assertRaises(cal.CalibrationError):
            cal.build_rule((1, 0, 0))
        with self.assertRaises(cal.CalibrationError):
            cal.build_rule((-1, 0, 0, 0, 1, 0))
        self.assertEqual(cal.INSTALLED_COMMAND, Path("/usr/bin/nwfermi-calibrate"))

    def test_verify_measures_installed_matrix_without_fitting(self):
        targets = cal.normalized_targets(1920, 1080, 80)
        result = cal.evaluate_matrix((1, 0, 0, 0, 1, 0), targets, targets)
        self.assertEqual(result.rms_residual, 0.0)


class FakeEcodes:
    EV_ABS = 1
    EV_KEY = 2
    EV_SYN = 3
    ABS_X = 10
    ABS_Y = 11
    BTN_TOUCH = 12
    SYN_REPORT = 13
    SYN_DROPPED = 16
    EV_PROP = 14
    INPUT_PROP_DIRECT = 15


class FakeDevice:
    def __init__(self):
        self.x = 100
        self.y = 200
        self.touch = False
        self.recovery_error = None

    def close(self):
        pass

    def read_loop(self):
        return iter(())

    def absinfo(self, code):
        if self.recovery_error:
            raise self.recovery_error
        return SimpleNamespace(value=self.x if code == FakeEcodes.ABS_X else self.y)

    def active_keys(self):
        if self.recovery_error:
            raise self.recovery_error
        return [FakeEcodes.BTN_TOUCH] if self.touch else []


def event(kind, code, value=0):
    return SimpleNamespace(type=kind, code=code, value=value)


class EvdevCaptureTests(unittest.TestCase):
    def setUp(self):
        self.samples = []
        self.errors = []
        self.release_ready = []
        self.desync_messages = []
        self.device = FakeDevice()
        self.capture = cal.EvdevCapture(
            self.device, lambda x, y, generation: self.samples.append((x, y, generation)),
            self.errors.append, FakeEcodes, self.release_ready.append, self.desync_messages.append
        )

    def feed(self, *events):
        for item in events:
            self.capture.process_event(item)

    def complete_press(self, x=100, y=200):
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, x),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_Y, y),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))

    def prime_axes(self, x=100, y=200):
        self.feed(event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, x),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_Y, y),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))

    def test_one_sample_requires_explicit_arm_and_coordinates_after_press(self):
        token = self.capture.arm_point()
        self.complete_press()
        self.assertEqual(self.samples, [(100, 200, token)])
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 0))
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.CAPTURED)
        self.complete_press(300, 400)
        self.assertEqual(len(self.samples), 1)
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 0))
        next_token = self.capture.arm_point()
        self.complete_press(300, 400)
        self.assertEqual(self.samples[-1], (300, 400, next_token))

    def test_held_touch_and_incomplete_frame_are_not_captured(self):
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1))
        self.capture.arm_point()
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.WAIT_RELEASE)
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 0))
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.IDLE)
        self.assertEqual(len(self.release_ready), 1)
        token = self.capture.arm_point()
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT),
                  event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 0))
        self.assertEqual(self.samples, [])
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.IDLE)
        self.assertGreater(token, 0)

    def test_single_axis_update_uses_only_a_syn_confirmed_other_axis(self):
        self.prime_axes(100, 200)
        token = self.capture.arm_point()
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, 300),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [(300, 200, token)])

    def test_y_only_update_and_abs_before_press_are_coherent(self):
        self.prime_axes(100, 200)
        token = self.capture.arm_point()
        self.feed(event(FakeEcodes.EV_ABS, FakeEcodes.ABS_Y, 400),
                  event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [(100, 400, token)])

    def test_press_without_abs_and_split_frames_do_not_mix_coordinates(self):
        self.prime_axes(100, 200)
        token = self.capture.arm_point()
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [])
        self.feed(event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, 300),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [(300, 200, token)])

    def test_dropped_frame_invalidates_residual_axes(self):
        self.prime_axes(100, 200)
        self.capture.arm_point()
        self.device.x = 300
        self.device.y = 400
        self.device.touch = True
        self.feed(event(FakeEcodes.EV_SYN, FakeEcodes.SYN_DROPPED),
                  event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, 300),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [])
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.IDLE)
        self.assertTrue(self.desync_messages)
        # Recovery knows the finger is still down; a new explicit arm waits
        # for release and cannot manufacture a sample from lost events.
        self.assertEqual(self.capture.arm_point() > 0, True)
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.WAIT_RELEASE)

    def test_drop_discards_events_and_recovery_with_released_touch_needs_explicit_arm(self):
        self.capture.arm_point()
        self.device.x = 500
        self.device.y = 600
        self.device.touch = False
        self.feed(event(FakeEcodes.EV_SYN, FakeEcodes.SYN_DROPPED),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, 900),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_Y, 901),
                  event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [])
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.IDLE)
        token = self.capture.arm_point()
        self.complete_press(500, 600)
        self.assertEqual(self.samples, [(500, 600, token)])

    def test_failed_recovery_keeps_touch_state_unknown_and_does_not_rearm(self):
        self.capture.arm_point()
        self.device.recovery_error = OSError("ioctl failed")
        self.feed(event(FakeEcodes.EV_SYN, FakeEcodes.SYN_DROPPED),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.IDLE)
        self.assertIn("impossibile recuperare", self.desync_messages[-1])
        self.capture.arm_point()
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.WAIT_RELEASE)

    def test_drop_invalidates_a_queued_generation(self):
        token = self.capture.arm_point()
        self.feed(event(FakeEcodes.EV_KEY, FakeEcodes.BTN_TOUCH, 1),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_X, 10),
                  event(FakeEcodes.EV_ABS, FakeEcodes.ABS_Y, 20),
                  event(FakeEcodes.EV_SYN, FakeEcodes.SYN_REPORT))
        self.assertEqual(self.samples, [(10, 20, token)])
        self.feed(event(FakeEcodes.EV_SYN, FakeEcodes.SYN_DROPPED))
        self.assertFalse(self.capture.generation_is_current(token))

    def test_retry_discards_old_generation(self):
        self.capture.arm_point()
        token = self.capture.arm_point()
        self.complete_press(700, 800)
        self.assertEqual(self.samples, [(700, 800, token)])

    def test_close_prevents_future_samples(self):
        self.capture.arm_point()
        self.capture.close()
        self.complete_press()
        self.assertEqual(self.samples, [])
        self.assertEqual(self.capture.snapshot()[0], cal.EvdevCapture.CLOSED)


class DiscoveryTests(unittest.TestCase):
    def test_permission_denied_is_distinct_from_absent_device(self):
        def denied(_path):
            raise PermissionError("denied")

        with self.assertRaisesRegex(cal.CalibrationError, "permesso negato.*logind"):
            cal.discover_device(denied, lambda: ["/dev/input/event99"], FakeEcodes)

    def test_matching_virtual_device_uses_name_and_capabilities(self):
        class MatchingDevice:
            name = cal.DEVICE_NAME
            path = "/dev/input/event55"

            def capabilities(self, absinfo=False):
                return {
                    FakeEcodes.EV_ABS: [(FakeEcodes.ABS_X, object()), (FakeEcodes.ABS_Y, object())],
                    FakeEcodes.EV_KEY: [FakeEcodes.BTN_TOUCH],
                    FakeEcodes.EV_PROP: [FakeEcodes.INPUT_PROP_DIRECT],
                }

            def close(self):
                pass

        device = cal.discover_device(lambda _path: MatchingDevice(), lambda: ["/dev/input/event55"], FakeEcodes)
        self.assertEqual(device.name, cal.DEVICE_NAME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
