"""THERM 7.8 tests for therm-library: binary .lib names, contract records, presets, version choice.
Run from the skill folder: python -m unittest discover -s tests"""
import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import therm_library as tl  # noqa: E402

RESEARCH = os.path.join(HERE, "..", "..", "..", "research")
S78 = os.path.join(RESEARCH, "samples", "therm78")
LIB78 = os.path.join(S78, "lib")
FIXTURE = os.path.join(RESEARCH, "fixtures", "library-78.json")
SILL = os.path.join(S78, "converted", "sample_sill.thmx")
R0 = os.path.join(S78, "roundtrip", "r0base_solved.thmx")
NFRC_H = {"Interior Aluminum Frame (convection only)": "3.290000",
          "Interior Thermally Broken Frame (convection only)": "3.000000",
          "Interior Thermally Improved Frame (convection only)": "3.120000",
          "Interior Wood/Vinyl Frame (convection only)": "2.440000",
          "Interior (20 tilt) Aluminum Frame (convection only)": "4.650000",
          "Interior (20 tilt) Thermally Broken Frame (convection only)": "4.090000",
          "Interior (20 tilt) Thermally Improved Frame (convection only)": "4.320000",
          "Interior (20 tilt) Interior Wood/Vinyl Frame (convection only)": "3.090000",
          "NFRC 100-2010 Exterior": "26.000000", "Adiabatic": "0.000000"}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tl.main(list(argv))
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def assert_contract_shape(tc, doc, fixture):
    """Validate a project JSON against the shape of research/fixtures/library-78.json."""
    keyorder = {}
    for g in ("materials", "boundary_conditions"):
        for r in fixture[g]:
            keyorder[(r["kind"], r["thmx"].get("Type") if r["kind"] == "material" else "bc")] = list(r["thmx"])
    for g, kind in (("materials", "material"), ("boundary_conditions", "bc"), ("gases", None)):
        tc.assertIsInstance(doc[g], list)
        for r in doc[g]:
            tc.assertEqual(r["kind"], kind)
            tc.assertEqual(r["thmx"]["Name"], r["name"])
            tc.assertEqual(r["name"], tl.normalise_name(r["name"]))
            tc.assertTrue(all(isinstance(v, str) for v in r["thmx"].values()), r["name"])
            want = keyorder[(kind, r["thmx"]["Type"] if kind == "material" else "bc")]
            tc.assertEqual(list(r["thmx"]), want, r["name"])
            tc.assertNotIn("uuid", r)


class Normalise(unittest.TestCase):
    CASES = [("fromx:sample-sill:Wood (Pine, Spruce, Fir)", "Wood (Pine, Spruce, Fir)"),
             ("Frame Cavity_Cavity_16_Cavity_16", "Frame Cavity"),
             ("sample-sill:Frame Cavity NFRC 100-2001_Cavity_16_Cavity_16", "Frame Cavity NFRC 100-2001"),
             ("sample_sill:Butyl Rubber ", "Butyl Rubber"),
             ("sample-sill:Sample GlzSys (ID:8) U-factor Inside Film", "Sample GlzSys (ID:8) U-factor Inside Film"),
             ("Int simple 7.7", "Int simple 7.7"),
             ("  NFRC 100-2010 Exterior ", "NFRC 100-2010 Exterior"),
             ("Frame Cavity NFRC 100_Cavity_49", "Frame Cavity NFRC 100")]

    def test_contract_table(self):
        for given, want in self.CASES:
            self.assertEqual(tl.normalise_name(given), want, given)


class LibNames(unittest.TestCase):
    EXPECTED = {"material.lib": ("CMaterial", 82), "BC.lib": ("CBoundaryCondition", 17),
                "gas.lib": ("CCavityGas", 5), "material-NFRC.lib": ("CMaterial", 78),
                "Material-Arch.lib": ("CMaterial", 99), "BC-Arch.lib": ("CBoundaryCondition", 10),
                "UFACTOR.LIB": (None, 7), "UFACTOR-Arch.lib": (None, 5), "attributes.lib": (None, 6)}

    def test_every_sample_lib(self):
        self.assertEqual(sorted(os.listdir(LIB78)), sorted(self.EXPECTED))
        for f, (cls, n) in self.EXPECTED.items():
            got_cls, count, names = tl.lib_names(os.path.join(LIB78, f))
            self.assertEqual((got_cls, count, len(names)), (cls, n, n), f)
            self.assertTrue(all(x and x.isprintable() for x in names), f)

    def test_both_encodings(self):
        _, _, utf16 = tl.lib_names(os.path.join(LIB78, "BC.lib"))
        _, _, ansi = tl.lib_names(os.path.join(LIB78, "material-NFRC.lib"))
        self.assertIn("NFRC 100-2010 Exterior", utf16)
        self.assertIn("Frame Cavity NFRC 100", ansi)
        _, _, gases = tl.lib_names(os.path.join(LIB78, "gas.lib"))
        self.assertEqual(gases, ["Air", "Argon", "Krypton", "Xenon", "Air (5%) / Argon (95%) Mix"])

    def test_ini_names_only_its_libraries(self):
        with tempfile.TemporaryDirectory() as d:
            for f in os.listdir(LIB78):
                shutil.copy(os.path.join(LIB78, f), d)
            ini = os.path.join(d, "therm7.8.ini")
            with open(ini, "w", newline="\r\n") as fh:
                fh.write("[WINDOW7]\nMaterialLibName=x.lib\n[THERM]\n"
                         "BCLibName=C:\\Users\\Public\\LBNL\\THERM7.8\\Lib\\bc.lib\n"
                         "GasLibName=C:\\Users\\Public\\LBNL\\therm7.8\\Lib\\gas.lib\n"
                         "MaterialLibName=C:\\Users\\Public\\LBNL\\therm7.8\\Lib\\material.lib\n"
                         "UfactorLibName=C:\\Users\\Public\\LBNL\\therm7.8\\Lib\\ufactor.lib\n")
            code, doc, _ = run("list", "--ini", ini)
            self.assertEqual(code, 0)
            self.assertEqual(doc["therm_version"], "7.8")
            only = [r for g in tl.GROUPS for r in doc[g] if r.get("library_only")]
            by = {k: sum(1 for r in only if r["kind"] == k) for k in ("material", "bc", "gas")}
            self.assertEqual(by, {"material": 82, "bc": 17, "gas": 5})
            self.assertEqual({os.path.basename(r["source"]["path"]).lower() for r in only},
                             {"material.lib", "bc.lib", "gas.lib"})  # Windows keeps the ini's spelling
            code, _, err = run("export", "--ini", ini, "--name", "Polyamide (Nylon)")
            self.assertEqual(code, 1)
            self.assertIn("binary THERM 7.8 library", err)


class Version(unittest.TestCase):
    def ns(self, **kw):
        base = {"therm_version": None, "source": None, "lib": None, "ini": None, "out": None}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_default_and_extension(self):
        self.assertEqual(tl.version_for(self.ns(), installed=True), "7.8")
        self.assertEqual(tl.version_for(self.ns(), installed=False), "8.1")
        self.assertEqual(tl.version_for(self.ns(source=["a.thmx"]), installed=False), "7.8")
        self.assertEqual(tl.version_for(self.ns(source=["a.thmz"]), installed=True), "8.1")
        self.assertEqual(tl.version_for(self.ns(therm_version="8.1", source=["a.thmx"]), installed=True), "8.1")
        self.assertEqual(tl.version_for(self.ns(lib=LIB78), installed=False), "7.8")

    def test_installed_detection_and_existing_out(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "THERM7.exe")
            self.assertFalse(tl.installed_78([exe], os.path.join(d, "none.ini")))
            open(exe, "w").close()
            self.assertTrue(tl.installed_78([exe], os.path.join(d, "none.ini")))
            out = os.path.join(d, "p.json")
            with open(out, "w") as fh:
                json.dump({"boundary_conditions": [{"kind": "bc", "name": "x", "xml": "<BoundaryCondition/>"}]}, fh)
            self.assertEqual(tl.version_for(self.ns(out=out), installed=True), "8.1")
            shutil.copy(FIXTURE, out)
            self.assertEqual(tl.version_for(self.ns(out=out), installed=False), "7.8")


class Records(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "project.json")
        self.fixture = load(FIXTURE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_matches_fixture(self):
        for name in ("Wood (Pine, Spruce, Fir)", "Frame Cavity NFRC 100-2001", "NFRC 100-2010 Exterior", "Adiabatic"):
            self.assertEqual(run("export", "--therm-version", "7.8", "--name", name, "--out", self.out)[0], 0)
        code, _, _ = run("export", "--source", R0, "--name",
                         "sample-sill:Sample GlzSys (ID:8) U-factor Inside Film", "--out", self.out)
        self.assertEqual(code, 0)
        doc = load(self.out)
        assert_contract_shape(self, doc, self.fixture)
        for g in ("materials", "boundary_conditions"):
            got = {r["name"]: r for r in doc[g]}
            for r in self.fixture[g]:
                self.assertEqual(got[r["name"]]["kind"], r["kind"])
                self.assertEqual(got[r["name"]]["thmx"], r["thmx"], r["name"])
        self.assertEqual(doc["gases"], [])

    def test_thmx_source_normalises_and_keeps_case(self):
        code, doc, _ = run("list", "--source", SILL)
        self.assertEqual(code, 0)
        names = [r["name"] for r in doc["materials"]]
        self.assertIn("Butyl Rubber", names)
        self.assertIn("Butyl rubber", names)
        self.assertIn("Frame Cavity NFRC 100-2001", names)

    def test_81_reader_skips_78_only_records(self):
        shutil.copy(FIXTURE, self.out)
        code, doc, err = run("list", "--therm-version", "8.1", "--source", self.out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["counts"]["bcs"], 0)
        self.assertIn("7.8-only record", err)

    def test_presets(self):
        self.assertEqual(run("presets", "nfrc", "--therm-version", "7.8", "--out", self.out)[0], 0)
        doc = load(self.out)
        assert_contract_shape(self, doc, self.fixture)
        by = {r["name"]: r["thmx"] for r in doc["boundary_conditions"]}
        self.assertEqual({k: v["H"] for k, v in by.items()}, NFRC_H)
        self.assertEqual({v["Type"] for k, v in by.items() if k != "Adiabatic"}, {"1"})
        self.assertEqual(by["Adiabatic"]["Type"], "0")
        fx = {r["name"]: r["thmx"] for r in self.fixture["boundary_conditions"]}
        self.assertEqual(by["NFRC 100-2010 Exterior"], fx["NFRC 100-2010 Exterior"])
        iso = os.path.join(self.tmp.name, "iso.json")
        self.assertEqual(run("presets", "iso", "--therm-version", "7.8", "--out", iso)[0], 0)
        self.assertEqual(run("presets", "iso", "--out", iso)[0], 0)  # re-run merges (version from the file)
        doc = load(iso)
        assert_contract_shape(self, doc, self.fixture)
        by = {r["name"]: r for r in doc["boundary_conditions"]}
        self.assertEqual(len(by), 4)
        self.assertEqual(by["Adiabatic"]["thmx"], fx["Adiabatic"])
        ext, rsi = by["ISO Exterior Rse 0.04 (0 C)"], by["ISO Interior Rsi 0.13 normal (20 C)"]
        self.assertEqual((ext["thmx"]["Type"], ext["thmx"]["H"], ext["thmx"]["Temperature"]), ("1", "25.000000", "0.000000"))
        self.assertEqual((rsi["thmx"]["H"], rsi["thmx"]["Temperature"]), ("7.692308", "20.000000"))
        self.assertEqual(by["ISO Interior Rsi 0.20 reduced radiation (20 C)"]["thmx"]["H"], "5.000000")
        for r in (ext, rsi):
            self.assertTrue(r["preset"]["needs_engineer_check"])

    def test_add(self):
        self.assertEqual(run("add", "--therm-version", "7.8", "material", "--name", "PA66", "--conductivity", "0.3",
                             "--out", self.out)[0], 0)
        self.assertEqual(run("add", "--therm-version", "7.8", "bc", "--name", "Ext", "--temperature", "0",
                             "--film-coefficient", "25", "--out", self.out)[0], 0)
        doc = load(self.out)
        assert_contract_shape(self, doc, self.fixture)
        self.assertEqual(doc["materials"][0]["thmx"]["Conductivity"], "0.300000")
        self.assertEqual(run("add", "--therm-version", "7.8", "bc", "--name", "x", "--temperature", "0",
                             "--film-coefficient", "5", "--comprehensive")[0], 1)
        self.assertEqual(run("add", "--therm-version", "7.8", "material", "--name", "stem:x",
                             "--conductivity", "1")[0], 1)

    def test_refuses_lib_folder(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(LIB78, "BC.lib"), d)
            code, _, err = run("presets", "nfrc", "--therm-version", "7.8", "--out", os.path.join(d, "p.json"))
            self.assertEqual(code, 1)
            self.assertIn("refusing", err)


if __name__ == "__main__":
    unittest.main()
