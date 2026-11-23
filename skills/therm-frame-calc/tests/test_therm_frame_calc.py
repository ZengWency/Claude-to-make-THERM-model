"""Tests for therm-frame-calc. Run from the skill folder: python -m unittest discover -s tests

Hand-worked ISO example (synthetic, all round numbers):
  Panel run, tags Frame and Edge, dT = 20 K:
    Frame q = -6 W/m, Edge q = -4 W/m      -> L2D_panel = (6 + 4) / 20 = 0.5 W/mK
  Up = 1.0 W/m2K, bp = 200 mm, bf = 100 mm -> Uf = (0.5 - 1.0*0.2) / 0.1 = 3.0 W/m2K
  Glazed run, same tags, dT = 20 K:
    Frame q = -8 W/m, Edge q = -6 W/m      -> L2D_glazed = 14 / 20 = 0.7 W/mK
  Ug = 1.0 W/m2K, bg = bp = 200 mm         -> psi = 0.7 - 3.0*0.1 - 1.0*0.2 = 0.2 W/mK
  Up from a panel instead: dp = 29.05 mm, lambda = 0.035 -> dp/lambda = 0.83,
    Rsi 0.13 + 0.83 + Rse 0.04 = 1.0 m2K/W  -> Up = 1.0 W/m2K
"""
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
SILL = os.path.join(HERE, "..", "..", "..", "research", "samples", "lbnl-libraryfemtherm", "products",
                    "sample-sill.thmz")

spec = importlib.util.spec_from_file_location("therm_frame_calc", SCRIPT)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fc.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def tag(name, q, dt, length_mm):
    u = abs(q) / dt / (length_mm / 1000.0)
    proj = {"type": "Projected Y", "length_mm": length_mm, "u_factor": u, "degenerate": False}
    return {"tag": name, "delta_t": dt, "heat_flux_w_m2": q, "default_display_type": "Projected Y",
            "displayed": dict(proj, source="DefaultDisplayType"), "projections": [proj]}


def results(tags, case="U-factor", length_unit="mm"):
    return {"skill": "therm-results", "version": 1, "units": {"length": length_unit},
            "cases": [{"case": case, "simulation_error_pct": 1.0, "tags": tags}], "warnings": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def iso(self, panel, glazed, *extra):
        p = self.write("panel.json", panel)
        g = self.write("glazed.json", glazed)
        return run("iso", "--panel", p, "--glazed", g, "--tag", "Frame", "--tag", "Edge",
                   "--bf", "100", "--bp", "200", "--ug", "1.0", *extra)


PANEL = results([tag("Frame", -6.0, 20.0, 100.0), tag("Edge", -4.0, 20.0, 63.5)])
GLAZED = results([tag("Frame", -8.0, 20.0, 100.0), tag("Edge", -6.0, 20.0, 63.5)])


class HandWorkedIso(Base):
    def test_given_up(self):
        code, out, err = self.iso(PANEL, GLAZED, "--up", "1.0")
        self.assertEqual(code, 0, err)
        s = json.loads(out)
        self.assertEqual((s["skill"], s["version"], s["mode"]), ("therm-frame-calc", 1, "iso"))
        r = s["results"]
        self.assertAlmostEqual(r["l2d_panel_w_mk"], 0.5, places=12)
        self.assertAlmostEqual(r["uf_w_m2k"], 3.0, places=12)
        self.assertAlmostEqual(r["l2d_glazed_w_mk"], 0.7, places=12)
        self.assertAlmostEqual(r["psi_w_mk"], 0.2, places=12)
        self.assertEqual(s["inputs"]["bg_mm"], 200.0)
        self.assertIn("licensed copy of ISO 10077-2", s["disclaimer"])
        self.assertTrue(any("Sign convention" in a for a in s["assumptions"]))
        self.assertTrue(any("bg = bp" in a for a in s["assumptions"]))
        self.assertEqual(s["warnings"], [])

    def test_up_from_panel(self):
        code, out, err = self.iso(PANEL, GLAZED, "--panel-thickness", "29.05")
        self.assertEqual(code, 0, err)
        s = json.loads(out)
        self.assertAlmostEqual(s["inputs"]["up_w_m2k"], 1.0, places=12)
        self.assertAlmostEqual(s["results"]["uf_w_m2k"], 3.0, places=10)
        self.assertAlmostEqual(s["results"]["psi_w_mk"], 0.2, places=10)

    def test_markdown(self):
        md = os.path.join(self.tmp.name, "sheet.md")
        code, _, err = self.iso(PANEL, GLAZED, "--up", "1.0", "--markdown", md)
        self.assertEqual(code, 0, err)
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Uf = (L2D_panel - Up*bp) / bf", text)
        self.assertIn("| Uf | 3 W/m2K |", text)
        self.assertIn("licensed copy", text)


class IsoErrors(Base):
    def assertError(self, code, _out, err, text):
        self.assertEqual(code, 1)
        self.assertIn(text, err)

    def test_missing_tag(self):
        self.assertError(*self.iso(results([tag("Frame", -6.0, 20.0, 100.0)]), GLAZED, "--up", "1"),
                         "tag 'Edge' not found")

    def test_missing_case(self):
        panel = results(PANEL["cases"][0]["tags"], case="Condensation Resistance")
        self.assertError(*self.iso(panel, GLAZED, "--up", "1"), "case 'U-factor' not found")

    def test_mixed_signs(self):
        panel = results([tag("Frame", -6.0, 20.0, 100.0), tag("Edge", 4.0, 20.0, 63.5)])
        self.assertError(*self.iso(panel, GLAZED, "--up", "1"), "opposite sign")

    def test_length_unit(self):
        self.assertError(*self.iso(results(PANEL["cases"][0]["tags"], length_unit="m"), GLAZED, "--up", "1"),
                         "expects therm-results lengths in mm")

    def test_negative_width(self):
        self.assertError(*self.iso(PANEL, GLAZED, "--up", "1", "--bg", "-5"), "--bg")

    def test_up_choice(self):
        self.assertError(*self.iso(PANEL, GLAZED), "exactly one of --up or --panel-thickness")

    def test_uf_not_physical(self):
        self.assertError(*self.iso(PANEL, GLAZED, "--up", "3.0"), "Uf would be <= 0")

    def test_not_results_json(self):
        self.assertError(*self.iso({"skill": "other"}, GLAZED, "--up", "1"), "not therm-results output")


class NfrcSampleSill(Base):
    def test_sample_sill(self):
        out = os.path.join(self.tmp.name, "sill.json")
        subprocess.run([sys.executable, RESULTS, SILL, "--out", out], check=True, capture_output=True)
        code, text, err = run("nfrc", out)
        self.assertEqual(code, 0, err)
        s = json.loads(text)
        rows = {r["role"]: r for r in s["table"]}
        self.assertEqual(round(rows["Frame"]["u_factor_w_m2k"], 4), 2.0036)
        self.assertEqual(round(rows["Edge"]["u_factor_w_m2k"], 4), 2.3457)
        self.assertEqual(rows["Frame"]["projection"], "Projected in glass plane")
        self.assertAlmostEqual(rows["Edge"]["length_mm"], 63.5, places=3)
        self.assertIn("THERM 7 / WINDOW 7", s["note"])
        self.assertEqual(s["warnings"], [])

    def test_missing_edge_tag(self):
        p = self.write("r.json", results([tag("Frame", -6.0, 20.0, 100.0)]))
        code, _, err = run("nfrc", p, "--projection", "displayed")
        self.assertEqual(code, 1)
        self.assertIn("tag 'Edge' not found", err)

    def test_missing_glass_projection(self):
        p = self.write("r.json", results([tag("Frame", -6.0, 20.0, 100.0), tag("Edge", -4.0, 20.0, 63.5)]))
        code, _, err = run("nfrc", p)
        self.assertEqual(code, 1)
        self.assertIn("no usable 'Projected in glass plane' projection", err)


if __name__ == "__main__":
    unittest.main()
