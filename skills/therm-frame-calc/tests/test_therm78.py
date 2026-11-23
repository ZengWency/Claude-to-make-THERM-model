"""THERM 7.8 tests for therm-frame-calc: solved .thmx -> therm-results -> frame-calc."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "therm_frame_calc.py")
RESULTS = os.path.join(HERE, "..", "..", "therm-results", "scripts", "therm_results.py")
T78 = os.path.join(HERE, "..", "..", "..", "research", "samples", "therm78")
SILL_MESH = os.path.join(T78, "with-mesh", "sample_sill_mesh.thmx")
JAMB_MESH = os.path.join(T78, "with-mesh", "sample_jamb_mesh.thmx")
R0 = os.path.join(T78, "roundtrip", "r0base_solved.thmx")
R1 = os.path.join(T78, "roundtrip", "r1woodx2_solved.thmx")

spec = importlib.util.spec_from_file_location("therm_frame_calc_78", SCRIPT)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fc.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def results(self, thmx):
        out = os.path.join(self.tmp.name, os.path.basename(thmx) + ".json")
        subprocess.run([sys.executable, RESULTS, thmx, "--out", out], check=True, capture_output=True)
        return out


class Nfrc78(Base):
    def rows(self, thmx, *extra):
        code, text, err = run("nfrc", self.results(thmx), *extra)
        self.assertEqual(code, 0, err)
        s = json.loads(text)
        return s, {r["role"]: r for r in s["table"]}

    def test_sill_mesh_defaults_to_projected_y(self):
        s, rows = self.rows(SILL_MESH)
        self.assertEqual(s["inputs"]["projection"], "y")
        self.assertEqual(rows["Frame"]["projection"], "Projected Y")
        self.assertAlmostEqual(rows["Frame"]["u_factor_w_m2k"], 2.015155, places=6)
        self.assertAlmostEqual(rows["Edge"]["u_factor_w_m2k"], 2.337128, places=6)
        self.assertNotEqual(rows["Frame"]["u_factor_w_m2k"], 1.216263)
        self.assertIsNone(s["simulation_error_pct"])
        self.assertEqual(s["warnings"], [])
        self.assertIn("THERM 7.8", s["note"])
        self.assertNotIn("8.1", s["note"])

    def test_imported_sill_never_glass_fallback(self):
        # r0base: raw glass-plane Frame value is 1.216263 (= Projected X after .thmx import).
        _, rows = self.rows(R0)
        self.assertAlmostEqual(rows["Frame"]["u_factor_w_m2k"], 2.016345, places=6)
        self.assertNotEqual(rows["Frame"]["u_factor_w_m2k"], 1.216263)

    def test_r1woodx2(self):
        _, rows = self.rows(R1)
        self.assertAlmostEqual(rows["Frame"]["u_factor_w_m2k"], 2.887787, places=6)
        self.assertAlmostEqual(rows["Edge"]["u_factor_w_m2k"], 2.293155, places=6)

    def test_glass_refused_on_78(self):
        code, _, err = run("nfrc", self.results(R0), "--projection", "glass")
        self.assertEqual(code, 1)
        self.assertIn("no reliable glass-plane projection", err)

    def test_jamb_needs_projection(self):
        p = self.results(JAMB_MESH)
        code, _, err = run("nfrc", p)
        self.assertEqual(code, 1)
        self.assertIn("'Jamb'", err)
        code, text, err = run("nfrc", p, "--projection", "y")
        self.assertEqual(code, 0, err)
        self.assertTrue(any("not checked" in w for w in json.loads(text)["warnings"]))

    def test_total_length_case_insensitive(self):
        _, rows = self.rows(R0, "--projection", "total")
        self.assertEqual(rows["Edge"]["projection"], "Total Length")
        self.assertAlmostEqual(rows["Edge"]["u_factor_w_m2k"], 2.337976, places=6)


class Iso78(Base):
    """No ISO calibration-panel/glazed pair exists among the 7.8 samples. This runs the ISO
    chain on two solved 7.8 files (r0base as 'panel', r1woodx2 as 'glazed') to prove the
    arithmetic works on 7.8 JSON; the numbers are not a physical Uf or psi."""

    def test_chain(self):
        code, text, err = run("iso", "--panel", self.results(R0), "--glazed", self.results(R1),
                              "--tag", "Frame", "--tag", "Edge", "--bf", "42.875183", "--bp", "63.5",
                              "--ug", "1.0", "--up", "1.0")
        self.assertEqual(code, 0, err)
        s = json.loads(text)
        l2d_p = (2.016345 * 42.875183 + 2.337976 * 63.500015) / 1000
        self.assertAlmostEqual(s["results"]["l2d_panel_w_mk"], l2d_p, places=5)
        self.assertAlmostEqual(s["results"]["uf_w_m2k"], (l2d_p - 0.0635) / 0.042875183, places=4)
        self.assertTrue(any("THERM 7.8" in a for a in s["assumptions"]))
        self.assertFalse([w for w in s["warnings"] if "U*L" in w])


if __name__ == "__main__":
    unittest.main()
