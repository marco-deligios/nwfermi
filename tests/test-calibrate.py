#!/usr/bin/python3
"""Unit tests for calibration maths and udev rule management; no hardware."""
import importlib.util
import sys
import tempfile
import unittest
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
            cal.restore_rule(directory, reload_rules=False)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_rule_rejects_invalid_matrix(self):
        with self.assertRaises(cal.CalibrationError):
            cal.build_rule((1, 0, 0))

    def test_verify_measures_installed_matrix_without_fitting(self):
        targets = cal.normalized_targets(1920, 1080, 80)
        result = cal.evaluate_matrix((1, 0, 0, 0, 1, 0), targets, targets)
        self.assertEqual(result.rms_residual, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
