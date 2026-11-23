"""Tests for therm-glazing against LBNL's sample-sill. Run from the skill folder: python -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import therm_glazing as tg  # noqa: E402

SKILLS = os.path.join(HERE, "..", "..")
SAMPLES = os.path.join(SKILLS, "..", "research", "samples")
PRODUCTS = os.path.join(SAMPLES, "lbnl-libraryfemtherm", "products")
SILL = os.path.join(PRODUCTS, "sample-sill.thmz")
SILL_NR = os.path.join(PRODUCTS, "sample-sill-no-results.thmz")
LIB = os.path.join(SAMPLES, "therm81-lib-copies")
SPEC = os.path.join(SKILLS, "therm-model", "examples", "simple-frame.json")
RESULTS = {"Mesh.xml", "SteadyStateResults.xml", "SteadyStateMeshResults.xml"}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tg.main([str(a) for a in argv])
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def entries(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def model(path):
    return ET.fromstring(entries(path)["Model.xml"])


def pts(m, pid):
    p = [x for x in m.find("Polygons") if x.findtext("ID") == str(pid)][0]
    return [(float(q.findtext("x")), float(q.findtext("y"))) for q in p.findall("Points/Point")]


def poly_xml(m):
    return {p.findtext("ID"): ET.tostring(p) for p in m.find("Polygons")}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def changes(self, doc):
        p = self.path("changes.json")
        with open(p, "w") as fh:
            json.dump(doc, fh)
        return p

    def lib_json(self):
        """A therm-library project JSON (ISO presets plus a simplified "Glass interior" BC), frozen in
        tests/fixtures so these tests never import therm-library code."""
        return os.path.join(HERE, "fixtures", "lib-81.json")


class Show(Base):
    def test_matches_model_xml(self):
        code, doc, _ = run("show", SILL)
        self.assertEqual(code, 0)
        self.assertEqual((doc["skill"], doc["version"]), ("therm-glazing", 1))
        self.assertEqual(len(doc["glazing_systems"]), 1)
        g = doc["glazing_systems"][0]
        x = model(SILL).find("GlazingSystems/GlazingSystem")
        self.assertEqual((g["id"], g["index"], g["name"]), ("8", "1", "Sample GlzSys"))
        for i, L in enumerate(x.findall("Layers/Layer")):
            self.assertEqual(g["layers"][i]["thickness_mm"], float(L.findtext("Thickness")))
            self.assertEqual(g["layers"][i]["conductivity_w_mk"], float(L.findtext("Conductivity")))
            self.assertEqual(g["layers"][i]["surface1"]["emissivity"], float(L.findtext("Surface1/Emissivity")))
            self.assertEqual(g["layers"][i]["surface2"]["emissivity"], float(L.findtext("Surface2/Emissivity")))
        self.assertEqual(g["layers"][1]["surface1"]["emissivity"], 0.157969)
        G = x.find("Gases/Gas")
        self.assertEqual(g["gaps"][0]["thickness_mm"], float(G.findtext("Thickness")))
        self.assertEqual(g["gaps"][0]["conductivity_w_mk"], 0.049344)
        self.assertEqual(g["gaps"][0]["components"], [{"gas": "Air", "fraction": 1.0}])
        winter = g["properties"][0]
        self.assertEqual((winter["environment"], winter["u_value_w_m2k"]), ("Winter", 1.934458))
        self.assertEqual(winter["interior"], {"h": 7.209864, "hc": 2.614016, "hr": 4.595847, "temperature": 21.0})
        self.assertEqual(winter["exterior"]["h"], 29.21273)
        self.assertEqual(g["properties"][1]["u_value_w_m2k"], 1.677309)
        self.assertEqual((g["sight_line_height_mm"], g["eog_height_mm"]), (11.112494, 63.499966))
        self.assertEqual(g["total_thickness_mm"], 26.51)
        self.assertEqual(g["interior_direction"], "+x")
        self.assertEqual([(p["id"], p["type"], p["element"], p["index"]) for p in g["polygons"]],
                         [("98", "Glass Out", "layer", 0), ("99", "Gap", "gap", 0), ("100", "Glass In", "layer", 1)])
        self.assertEqual(g["igu_bc"]["film_coefficient_w_m2k"], 2.614016)
        self.assertTrue(g["igu_bc"]["igu_surface"])
        self.assertEqual(doc["warnings"], [])
        self.assertEqual(doc["unresolved_materials"], [])

    def test_no_results_sample_project_prefixed_names(self):
        code, doc, _ = run("show", SILL_NR)
        self.assertEqual(code, 0)
        self.assertEqual(doc["unresolved_materials"], [])
        self.assertEqual(doc["glazing_systems"][0]["igu_bc"]["film_coefficient_w_m2k"], 2.614402)

    def test_missing_file(self):
        self.assertEqual(run("show", self.path("nope.thmz"))[0], 1)


class Patch(Base):
    def test_null_change_set_is_identity(self):
        out = self.path("null.thmz")
        code, doc, _ = run("patch", SILL, "--changes", "{}", "--thmz", out)
        self.assertEqual(code, 0)
        self.assertFalse(doc["changed"])
        a, b = entries(SILL), entries(out)
        self.assertEqual(a["Model.xml"], b["Model.xml"])
        self.assertEqual(a, b)  # nothing changed, so results stay valid and are kept

    def test_thicker_gap_refused_on_frame_collision(self):
        out = self.path("gap.thmz")
        code, _, err = run("patch", SILL, "--changes", '{"gaps":[{"index":0,"thickness_mm":18.51}]}', "--thmz", out)
        self.assertEqual(code, 1)
        self.assertIn("fixed polygon 77", err)
        self.assertIn("fixed polygon 42", err)
        self.assertFalse(os.path.exists(out))

    def test_thicker_gap_moves_inner_pane_by_delta_only(self):
        out = self.path("gap.thmz")
        code, doc, _ = run("patch", SILL, "--thmz", out, "--changes",
                           '{"gaps":[{"index":0,"thickness_mm":18.51}],"also_shift":[77,42]}')
        self.assertEqual(code, 0, doc)
        m0, m1 = model(SILL), model(out)
        self.assertEqual(pts(m1, 100), [(x + 2.0, y) for x, y in pts(m0, 100)])  # Glass In
        self.assertEqual(pts(m1, 98), pts(m0, 98))                                # Glass Out
        cut = 248.624459
        self.assertEqual(pts(m1, 99), [(x + 2.0 if x > cut else x, y) for x, y in pts(m0, 99)])  # gap stretches
        for pid in (77, 42):  # listed frame parts follow rigidly (entirely on the interior side)
            self.assertEqual(pts(m1, pid), [(x + 2.0, y) for x, y in pts(m0, pid)])
        a, b = poly_xml(m0), poly_xml(m1)
        unchanged = set(a) - {"98", "99", "100", "77", "42"}
        self.assertEqual({k: a[k] for k in unchanged}, {k: b[k] for k in unchanged})
        # IGU record, gap cavity and the interior film boundary follow
        self.assertEqual(m1.findtext("GlazingSystems/GlazingSystem/Gases/Gas/Thickness"), "18.51")
        cav = [c for c in m1.find("Cavities") if c.findtext("UUID") == "928181c7-661d-39aa-92608ee341f6"][0]
        self.assertEqual(cav.findtext("MaxXDimension"), "18.509995")
        b38 = [b for b in m1.find("Boundaries") if b.findtext("ID") == "38"][0]
        self.assertEqual(b38.findtext("StartPoint/x"), "263.879456")
        # stale results stripped; THERM's own no-result files keep CalculationReady true
        self.assertEqual(RESULTS & set(entries(out)), set())
        self.assertEqual(m1.findtext("CalculationReady"), "true")
        self.assertTrue(any("lost" in w for w in doc["warnings"]))  # spacer contact: void warning

    def test_thinner_gap_moves_inner_pane_back(self):
        out = self.path("thin.thmz")
        code, doc, err = run("patch", SILL, "--thmz", out, "--changes",
                             '{"gaps":[{"index":0,"thickness_mm":15.51}],"also_shift":"all"}')
        self.assertEqual(code, 0, err)
        self.assertEqual(pts(model(out), 100), [(round(x - 1.0, 6), y) for x, y in pts(model(SILL), 100)])

    def test_films_keep_igu_bc_in_step(self):
        out = self.path("film.thmz")
        code, doc, err = run("patch", SILL, "--thmz", out, "--changes",
                             '{"properties":{"winter":{"interior":{"hc":3.0,"hr":4.5}}},"name":"X"}')
        self.assertEqual(code, 0, err)
        g = doc["glazing_system"]
        self.assertEqual(g["properties"][0]["interior"]["h"], 7.5)
        self.assertEqual(g["igu_bc"]["film_coefficient_w_m2k"], 3.0)
        self.assertEqual(g["name"], "X")
        self.assertIn(b"<FilmCoefficient>3</FilmCoefficient>", entries(out)["SteadyStateBC.xml"])

    def test_inconsistent_h_refused(self):
        code, _, err = run("patch", SILL, "--thmz", self.path("x.thmz"), "--changes",
                           '{"properties":{"winter":{"interior":{"h":9}}}}')
        self.assertEqual(code, 1)
        self.assertIn("hc + hr", err)

    def test_emissivity_and_gas(self):
        out = self.path("e.thmz")
        code, doc, err = run("patch", SILL, "--thmz", out, "--library", LIB, "--changes",
                             '{"layers":[{"index":1,"emissivity_1":0.037}],'
                             '"gaps":[{"index":0,"gas":"Argon","conductivity_w_mk":0.0339}]}')
        self.assertEqual(code, 0, err)
        m = model(out)
        b102 = [b for b in m.find("Boundaries") if b.findtext("ID") == "102"][0]
        self.assertEqual(b102.findtext("ThermalEmissionProperties/Emissivity"), "0.037")
        self.assertEqual(m.findtext("GlazingSystems/GlazingSystem/Gases/Gas/Components/Component/Properties/Name"),
                         "Argon")
        self.assertEqual(doc["geometry"]["polygons_moved"], [])

    def test_unknown_key_and_bad_values(self):
        for ch in ('{"gap":[]}', '{"layers":[{"index":5,"thickness_mm":4}]}',
                   '{"layers":[{"index":0,"thickness_mm":-1}]}', '{"also_shift":[999],"gaps":[{"index":0,"thickness_mm":17}]}'):
            self.assertEqual(run("patch", SILL, "--thmz", self.path("x.thmz"), "--changes", ch)[0], 1, ch)

    def test_never_overwrites_input_without_in_place(self):
        copy_in = self.path("in.thmz")
        shutil.copy(SILL, copy_in)
        self.assertEqual(run("patch", copy_in, "--thmz", copy_in, "--changes", '{"name":"Y"}')[0], 1)
        self.assertEqual(run("patch", copy_in, "--in-place", "--changes", '{"name":"Y"}')[0], 0)
        self.assertEqual(model(copy_in).findtext("GlazingSystems/GlazingSystem/Name"), "Y")


class Solid(Base):
    def test_glass_and_gaps_resolve(self):
        out = self.path("solid.thmz")
        code, doc, err = run("solid", SILL, "--thmz", out, "--library", self.lib_json(),
                             "--changes", '{"interior_bc":"Glass interior"}')
        self.assertEqual(code, 0, err)
        m, e = model(out), entries(out)
        self.assertEqual(tg.unresolved_materials(m, ET.fromstring(e["Materials.xml"])), [])
        self.assertEqual(len(m.find("GlazingSystems")), 0)
        self.assertFalse([p for p in m.find("Polygons") if p.find("GlazingSystem") is not None])
        self.assertFalse([b for b in m.find("Boundaries") if b.findtext("Type") == "Glazing Cavity"])
        names = {p.findtext("ID"): p.findtext("MaterialName") for p in m.find("Polygons")}
        self.assertEqual(names["98"], "Glass k=1 e=0.84 (therm-glazing)")
        self.assertEqual(names["99"], "Glazing gap keff=0.049344 (therm-glazing)")
        self.assertIn(b"<Name>Glass interior</Name>", e["SteadyStateBC.xml"])
        self.assertNotIn(b"U-factor Inside Film", e["SteadyStateBC.xml"])
        self.assertEqual(RESULTS & set(e), set())
        self.assertEqual(doc["boundaries_retargeted"], 2)

    def test_panel(self):
        out = self.path("panel.thmz")
        code, doc, err = run("solid", SILL, "--thmz", out, "--library", self.lib_json(), "--changes",
                             '{"panel":{"thickness_mm":26.51},"interior_bc":"ISO Interior Rsi 0.13 normal (20 C)"}')
        self.assertEqual(code, 0, err)
        mats = {p["material"] for p in doc["polygons"]}
        self.assertEqual(mats, {"Calibration panel k=0.035 (therm-glazing)"})
        m = model(out)
        self.assertEqual(tg.unresolved_materials(m, ET.fromstring(entries(out)["Materials.xml"])), [])

    def test_needs_interior_bc_and_matching_panel(self):
        self.assertEqual(run("solid", SILL, "--thmz", self.path("a.thmz"))[0], 1)
        self.assertEqual(run("solid", SILL, "--thmz", self.path("a.thmz"), "--library", self.lib_json(),
                             "--changes", '{"panel":{"thickness_mm":28},"interior_bc":"Glass interior"}')[0], 1)

    def test_spec_region(self):
        out = self.path("spec.json")
        code, doc, err = run("solid", SPEC, "--spec", out, "--changes",
                             '{"region":"panel","interior":"+x","layers":[{"thickness_mm":4},{"thickness_mm":4}],'
                             '"gaps":[{"thickness_mm":20,"conductivity_w_mk":0.0339}]}')
        self.assertEqual(code, 0, err)
        with open(out) as fh:
            spec = json.load(fh)
        ids = [p["id"] for p in spec["polygons"]]
        self.assertNotIn("panel", ids)
        self.assertEqual(ids[-3:], ["panel-glass-0", "panel-gap-0", "panel-glass-1"])
        self.assertEqual(spec["polygons"][-2]["points"], [[25.0, 50.0], [45.0, 50.0], [45.0, 240.0], [25.0, 240.0]])
        known = {m["name"] for m in spec["materials"]}
        self.assertTrue(all(p["material"] in known for p in spec["polygons"]))
        gap = [m for m in spec["materials"] if m["name"].startswith("Glazing gap")][0]
        self.assertEqual(gap["conductivity"], 0.0339)

    def test_spec_thickness_mismatch(self):
        code, _, err = run("solid", SPEC, "--spec", self.path("s.json"), "--changes",
                           '{"region":"panel","interior":"+x","layers":[{"thickness_mm":4},{"thickness_mm":4}],'
                           '"gaps":[{"thickness_mm":16,"conductivity_w_mk":0.0339}]}')
        self.assertEqual(code, 1)
        self.assertIn("28 mm", err)


class Geometry(unittest.TestCase):
    def test_overlap_and_contact(self):
        a = [(0, 0), (1, 0), (1, 1), (0, 1)]
        b = [(1, 0), (2, 0), (2, 1), (1, 1)]          # touches a along x=1
        c = [(0.5, 0.2), (1.5, 0.2), (1.5, 0.8), (0.5, 0.8)]
        self.assertFalse(tg.overlap(a, b))
        self.assertTrue(tg.overlap(a, c))
        self.assertTrue(tg.overlap(a, a))
        self.assertAlmostEqual(tg.contact(a, b), 1.0)


if __name__ == "__main__":
    unittest.main()
