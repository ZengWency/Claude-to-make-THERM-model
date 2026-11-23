"""THERM 7.8 .thmx tests for therm-glazing (equivalent solid only). Run from the skill folder."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import therm_glazing as tg  # noqa: E402

RESEARCH = os.path.join(HERE, "..", "..", "..", "research")
SILL78 = os.path.join(RESEARCH, "samples", "therm78", "shipped", "sample-sill_7.8.55.thmx")
FIXTURE = os.path.join(RESEARCH, "fixtures", "library-78.json")
Q = "{http://windows.lbl.gov}"

# Copied from research/library-record-78.md section 3.
NAME_CASES = [
    ("fromx:sample-sill:Wood (Pine, Spruce, Fir)", "Wood (Pine, Spruce, Fir)"),
    ("Frame Cavity_Cavity_16_Cavity_16", "Frame Cavity"),
    ("sample-sill:Frame Cavity NFRC 100-2001_Cavity_16_Cavity_16", "Frame Cavity NFRC 100-2001"),
    ("sample_sill:Butyl Rubber ", "Butyl Rubber"),
    ("sample-sill:Sample GlzSys (ID:8) U-factor Inside Film", "Sample GlzSys (ID:8) U-factor Inside Film"),
    ("Int simple 7.7", "Int simple 7.7"),
    ("  NFRC 100-2010 Exterior ", "NFRC 100-2010 Exterior"),
    ("Frame Cavity NFRC 100_Cavity_49", "Frame Cavity NFRC 100"),
]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tg.main([str(a) for a in argv])
    return code, (json.loads(out.getvalue()) if code == 0 and out.getvalue() else None), err.getvalue()


class Thmx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sill = os.path.join(self.tmp, "mdl_sill.thmx")
        shutil.copy(SILL78, self.sill)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_normalise_name(self):
        for raw, want in NAME_CASES:
            self.assertEqual(tg.normalise_name(raw), want, raw)

    def test_solid_default_output_is_thmx(self):
        code, doc, err = run("solid", self.sill, "--changes", '{"gaps":[{"conductivity_w_mk":0.049352}]}')
        self.assertEqual(code, 0, err)
        self.assertEqual(doc["output"], self.path("mdl_sill_solid.thmx"))
        self.assertIn("no Condensation Resistance case", err)

    def test_solid_equivalent(self):
        out = self.path("mdl_glz.thmx")
        code, doc, err = run("solid", self.sill, "-o", out, "--changes", '{"gaps":[{"conductivity_w_mk":0.03}]}')
        self.assertEqual(code, 0, err)
        root = ET.parse(out).getroot()
        polys = {p.get("ID"): p for p in root.iter(Q + "Polygon")}
        mats = {m.get("Name"): m for m in root.iter(Q + "Material")}
        for pid in ("37", "39"):
            self.assertEqual(polys[pid].get("Type"), "1")
            self.assertEqual(mats[polys[pid].get("Material")].get("Conductivity"), "1.000000")
        self.assertEqual(mats[polys["38"].get("Material")].get("Conductivity"), "0.030000")
        self.assertFalse([b for b in root.iter(Q + "BCPolygon") if b.get("BC") == "Glazing Cavity Surface"])
        self.assertFalse(list(root.find(Q + "GlazingSystems")))
        self.assertIsNone(root.find(Q + "Results"))
        self.assertFalse(any(n.startswith("Sample GlzSys_") for n in mats))
        idlist = [e.get("ID") for e in list(root.iter(Q + "Polygon")) + list(root.iter(Q + "BCPolygon"))]
        self.assertEqual(len(idlist), len(set(idlist)))
        film = [b for b in root.iter(Q + "BCPolygon") if "Inside Film" in b.get("BC")]
        self.assertIn("Edge", [b.get("UFactorTag") for b in film])
        self.assertEqual(doc["glazing_cavity_boundaries_removed"], 6)

    def test_gas_alone_is_an_error(self):
        code, _, err = run("solid", self.sill, "-o", self.path("a.thmx"), "--changes", '{"gaps":[{"gas":"Argon"}]}')
        self.assertEqual(code, 1)
        self.assertIn("conductivity_w_mk", err)
        self.assertIn("Argon", err)
        code, _, err = run("solid", self.sill, "-o", self.path("a.thmx"), "--changes", "{}")
        self.assertEqual(code, 1)
        self.assertIn("conductivity_w_mk is required", err)

    def test_interior_bc_from_contract_record(self):
        lib = self.path("lib.json")
        with open(FIXTURE, encoding="utf-8") as f:
            doc = json.load(f)
        doc["boundary_conditions"].append({"kind": "bc", "name": "Glass interior 7.7", "thmx": dict(
            doc["boundary_conditions"][0]["thmx"], Name="Glass interior 7.7", H="7.700000", Temperature="21.000000",
            Tr="21.000000", Hr="0.000000", RadiationModel="0", RadiationFlag="0")})
        with open(lib, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        out = self.path("b.thmx")
        code, res, err = run("solid", self.sill, "-o", out, "--library", lib, "--changes",
                             '{"gaps":[{"conductivity_w_mk":0.049352}],"interior_bc":"Glass interior 7.7"}')
        self.assertEqual(code, 0, err)
        root = ET.parse(out).getroot()
        names = {b.get("Name") for b in root.iter(Q + "BoundaryCondition")}
        self.assertIn("Glass interior 7.7", names)
        self.assertFalse(any("Inside Film" in n for n in names))
        self.assertEqual(res["inside_film_bc"], "Glass interior 7.7")

    def test_period_refused_and_patch_refused(self):
        code, _, err = run("solid", self.sill, "-o", self.path("a.b.thmx"), "--changes",
                           '{"gaps":[{"conductivity_w_mk":0.04}]}')
        self.assertEqual(code, 1)
        self.assertIn("period", err)
        code, _, err = run("patch", self.sill, "--thmz", self.path("x.thmz"), "--changes", "{}")
        self.assertEqual(code, 1)
        self.assertIn("conductivity_w_mk", err)

    def test_show(self):
        code, doc, err = run("show", self.sill)
        self.assertEqual(code, 0, err)
        self.assertEqual([(p["id"], p["kind"]) for p in doc["polygons"]], [("37", "layer"), ("38", "gap"),
                                                                          ("39", "layer")])
        self.assertEqual(doc["polygons"][1]["conductivity_w_mk"], 0.049352)


if __name__ == "__main__":
    unittest.main()
