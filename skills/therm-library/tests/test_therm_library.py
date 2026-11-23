"""Tests for therm-library against the research samples. Run from the skill folder: python -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import therm_library as tl  # noqa: E402

SAMPLES = os.path.join(HERE, "..", "..", "..", "research", "samples")
LIB = os.path.join(SAMPLES, "therm81-lib-copies")
SILL = os.path.join(SAMPLES, "lbnl-libraryfemtherm", "products", "sample-sill.thmz")
UNIFIED = os.path.join(SAMPLES, "lbnl-libraryfemtherm", "schemas-v0.24.0", "Boundary Conditions")
NFRC_EXT = "1810f37a-de4d-4e2d-95d5-09fe01157c34"


def recs(doc):
    return doc["materials"] + doc["boundary_conditions"] + doc["gases"]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tl.main(list(argv))
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


class ShippedLib(unittest.TestCase):
    def test_counts(self):
        code, doc, _ = run("list", "--lib", LIB)
        self.assertEqual(code, 0)
        self.assertEqual((doc["skill"], doc["version"]), ("therm-library", 1))
        self.assertEqual(doc["counts"], {"materials": 102, "pure_gases": 4, "gases": 5, "bcs": 14})

    def test_find_nfrc_exterior(self):
        code, doc, _ = run("find", "nfrc 100-2010 ext", "--lib", LIB)
        self.assertEqual(code, 0)
        (r,) = recs(doc)
        self.assertEqual((r["uuid"], r["kind"], r["type"]), (NFRC_EXT, "bc", "comprehensive"))
        p = r["properties"]
        self.assertEqual((p["convection_temperature_c"], p["film_coefficient_w_m2k"], p["radiation_model"]),
                         (-18.0, 26.0, "black_body"))

    def test_find_by_uuid_and_kind(self):
        _, doc, _ = run("find", "8d33196f", "--lib", LIB, "--kind", "pure_gas")
        self.assertEqual([r["name"] for r in recs(doc)], ["Air"])

    def test_material_types(self):
        _, doc, _ = run("list", "--lib", LIB, "--kind", "material")
        types = [r["type"] for r in recs(doc)]
        self.assertEqual((types.count("solid"), types.count("cavity"), types.count("radiation_enclosure")),
                         (97, 4, 1))

    def test_unified_bc_file(self):
        code, doc, _ = run("find", "Adiabatic", "--lib", UNIFIED)
        self.assertEqual(code, 0)
        self.assertEqual(recs(doc)[0]["type"], "adiabatic")
        self.assertEqual(recs(doc)[0]["xml_format"], "libraryfemtherm-unified")


class Thmz(unittest.TestCase):
    def test_embedded(self):
        code, doc, _ = run("list", "--source", SILL)
        self.assertEqual(code, 0)
        self.assertEqual(doc["counts"], {"materials": 8, "pure_gases": 1, "gases": 1, "bcs": 4})
        names = {r["name"] for r in recs(doc) if r["kind"] == "bc"}
        self.assertIn("Sample GlzSys (ID:8) U-factor Inside Film", names)
        steel = next(r for r in recs(doc) if r["name"].startswith("Steel"))
        self.assertEqual(steel["properties"]["conductivity_w_mk"], 14.3)


class ProjectFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "project.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_round_trip_with_gas_dependencies(self):
        code, _, _ = run("export", "--lib", LIB, "--uuid", NFRC_EXT, "--name", "Frame Cavity NFRC 100",
                         "--out", self.out)
        self.assertEqual(code, 0)
        _, doc, _ = run("list", "--source", self.out, "--full")
        kinds = sorted((r["kind"], r["name"]) for r in recs(doc))
        self.assertEqual(kinds, [("bc", "NFRC 100-2010 Exterior"), ("gas", "Air"),
                                 ("material", "Frame Cavity NFRC 100"), ("pure_gas", "Air")])
        ext = next(r for r in recs(doc) if r["uuid"] == NFRC_EXT)
        self.assertIn("<BlackBodyRadiation>", ext["xml"])
        _, orig, _ = run("find", NFRC_EXT, "--lib", LIB)
        self.assertEqual(ext["properties"], recs(orig)[0]["properties"])

    def test_therm_model_style_records(self):
        # therm-model's hand-written shape: lists of {name, xml} with no kind or properties
        _, lib, _ = run("find", NFRC_EXT, "--lib", LIB, "--full")
        with open(self.out, "w", encoding="utf-8") as fh:
            json.dump({"boundary_conditions": [{"name": "NFRC 100-2010 Exterior", "kind": "bc",
                                                "xml": recs(lib)[0]["xml"]}]}, fh)
        code, doc, _ = run("find", "nfrc", "--source", self.out)
        self.assertEqual(code, 0)
        self.assertEqual(recs(doc)[0]["properties"]["film_coefficient_w_m2k"], 26.0)
        self.assertEqual(recs(doc)[0]["uuid"], NFRC_EXT)

    def test_add_and_validation(self):
        self.assertEqual(run("add", "--therm-version", "8.1", "material", "--name", "PA66", "--conductivity", "0.3", "--out", self.out)[0], 0)
        self.assertEqual(run("add", "--therm-version", "8.1", "bc", "--name", "Ext", "--temperature", "0", "--film-coefficient", "25",
                             "--out", self.out)[0], 0)
        _, doc, _ = run("list", "--source", self.out)
        pa = next(r for r in recs(doc) if r["name"] == "PA66")
        self.assertEqual(pa["properties"]["conductivity_w_mk"], 0.3)
        self.assertEqual(len(pa["uuid"]), 36)
        self.assertEqual(run("add", "--therm-version", "8.1", "material", "--name", "x", "--conductivity", "0")[0], 1)
        self.assertEqual(run("add", "--therm-version", "8.1", "material", "--name", "x", "--conductivity", "1", "--emissivity", "1.5")[0], 1)
        self.assertEqual(run("add", "--therm-version", "8.1", "bc", "--name", "x", "--temperature", "0", "--film-coefficient", "5",
                             "--comprehensive", "--emissivity", "-0.1")[0], 1)
        # same name, new UUID: refused, because THERM matches BCs by name
        self.assertEqual(run("add", "--therm-version", "8.1", "bc", "--name", "Ext", "--temperature", "0", "--film-coefficient", "25",
                             "--out", self.out)[0], 1)

    def test_presets(self):
        self.assertEqual(run("presets", "nfrc", "--lib", LIB, "--out", self.out)[0], 0)
        self.assertEqual(run("presets", "iso", "--therm-version", "8.1", "--out", self.out)[0], 0)
        self.assertEqual(run("presets", "iso", "--therm-version", "8.1", "--out", self.out)[0], 0)  # deterministic UUIDs: re-run merges
        _, doc, _ = run("list", "--source", self.out)
        self.assertEqual(doc["counts"]["bcs"], 13)  # 10 NFRC + 3 ISO; Adiabatic shared
        by = {r["name"]: r for r in recs(doc)}
        self.assertEqual(by["ISO Exterior Rse 0.04 (0 C)"]["properties"]["film_coefficient_w_m2k"], 25.0)
        rsi = by["ISO Interior Rsi 0.13 normal (20 C)"]
        self.assertAlmostEqual(rsi["properties"]["film_coefficient_w_m2k"], 1 / 0.13, places=5)
        self.assertTrue(rsi["preset"]["needs_engineer_check"])
        self.assertEqual(by["ISO Interior Rsi 0.20 reduced radiation (20 C)"]["properties"]["temperature_c"], 20.0)

    def test_refuses_lib_folder(self):
        code, _, err = run("export", "--lib", LIB, "--uuid", NFRC_EXT, "--out", os.path.join(LIB, "x.json"))
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)
        self.assertFalse(os.path.exists(os.path.join(LIB, "x.json")))


class Errors(unittest.TestCase):
    def test_missing_source(self):
        self.assertEqual(run("list", "--source", "/no/such/lib")[0], 1)

    def test_bad_xml_is_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Materials.xml"), "w") as fh:
                fh.write("<Materials><Material>")
            self.assertEqual(run("list", "--lib", d)[0], 2)

    def test_export_unknown_name(self):
        code, _, err = run("export", "--lib", LIB, "--name", "NFRC 100-2010")
        self.assertEqual(code, 1)
        self.assertIn("similar", err)


if __name__ == "__main__":
    unittest.main()
