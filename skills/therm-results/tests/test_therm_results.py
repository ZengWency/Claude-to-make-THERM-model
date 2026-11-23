"""Tests for therm-results against the research samples. Run from the skill folder: python -m unittest discover -s tests"""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "therm_results.py")
SAMPLES = os.path.join(HERE, "..", "..", "..", "research", "samples")
LBNL = os.path.join(SAMPLES, "lbnl-libraryfemtherm", "products")
FAIRY = os.path.join(SAMPLES, "fairyfly")

spec = importlib.util.spec_from_file_location("therm_results", SCRIPT)
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tr.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def tag(result, case, name):
    c = next(c for c in result["cases"] if c["case"] == case)
    return next(t for t in c["tags"] if t["tag"] == name)


class SampleSill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = tr.read_thmz(os.path.join(LBNL, "sample-sill.thmz"))

    def test_contract_fields(self):
        self.assertEqual((self.r["skill"], self.r["version"]), ("therm-results", 1))
        self.assertTrue(self.r["input"].endswith("sample-sill.thmz"))

    def test_displayed_u(self):
        for name, u in (("Frame", 2.0036), ("Edge", 2.3457), ("SHGC Exterior", 1.8128)):
            d = tag(self.r, "U-factor", name)["displayed"]
            self.assertEqual(d["type"], "Projected Y")
            self.assertAlmostEqual(d["u_factor"], u, places=4)

    def test_nodes_and_tag_temps(self):
        uf = self.r["cases"][0]
        self.assertEqual(uf["mesh_nodes"], 425)
        frame = tag(self.r, "U-factor", "Frame")["temperature"]
        self.assertEqual(frame["nodes"], 16)
        self.assertAlmostEqual(frame["tmean_c"], 12.34, places=2)

    def test_cr_case_present(self):
        self.assertIn("Condensation Resistance", [c["case"] for c in self.r["cases"]])


class FairyflyUnknownDisplay(unittest.TestCase):
    def test_fallback_and_degenerate(self):
        r = tr.read_thmz(os.path.join(FAIRY, "test_result.thmz"))
        self.assertEqual([c["case"] for c in r["cases"]], ["U-factor"])  # empty CR case dropped
        t = tag(r, "U-factor", "Wall Assembly")
        self.assertEqual(t["default_display_type"], "Unknown")
        self.assertEqual((t["displayed"]["type"], t["displayed"]["source"]), ("Projected Y", "UValueBasis"))
        self.assertAlmostEqual(t["displayed"]["u_factor"], 1.971534, places=5)
        degenerate = {p["type"] for p in t["projections"] if p["degenerate"]}
        self.assertEqual(degenerate, {"Projected in glass plane", "Custom Rotation"})


class JsonEntries(unittest.TestCase):
    def test_json_matches_xml(self):
        x = tr.read_thmz(os.path.join(LBNL, "mullion-jamb-xml.thmz"))
        j = tr.read_thmz(os.path.join(LBNL, "mullion-jamb-json.thmz"))
        self.assertEqual(x["cases"], j["cases"])


class Unsolved(unittest.TestCase):
    def test_exit_1(self):
        for path in (os.path.join(LBNL, "sample-sill-no-results.thmz"),
                     os.path.join(FAIRY, "test_no_result.thmz"),
                     os.path.join(SAMPLES, "missing.thmz")):
            code, out, err = run(path)
            self.assertEqual(code, 1, path)
            self.assertEqual(out, "")
            self.assertIn("error", err)
        self.assertIn("SteadyStateResults", run(os.path.join(FAIRY, "test_no_result.thmz"))[2])


class Cli(unittest.TestCase):
    def test_out_and_csv(self):
        with tempfile.TemporaryDirectory() as d:
            js, cs = os.path.join(d, "out dir.json"), os.path.join(d, "out dir.csv")
            code, out, _ = run(os.path.join(LBNL, "sample-sill.thmz"), "--out", js, "--csv", cs)
            self.assertEqual((code, out), (0, ""))
            with open(cs, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            self.assertTrue(rows[0].startswith("case,tag,"))
            self.assertTrue(any("Frame,39.0" in r and ",True," in r for r in rows))


if __name__ == "__main__":
    unittest.main()
