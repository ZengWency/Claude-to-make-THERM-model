"""THERM 7.8 .thmx tests for therm-results, on the solved samples in research/samples/therm78/."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "therm_results.py")
T78 = os.path.join(HERE, "..", "..", "..", "research", "samples", "therm78")
SILL_MESH = os.path.join(T78, "with-mesh", "sample_sill_mesh.thmx")
JAMB_MESH = os.path.join(T78, "with-mesh", "sample_jamb_mesh.thmx")
DIVIDER_MESH = os.path.join(T78, "with-mesh", "Divider_mesh.thmx")
ROUNDTRIP = os.path.join(T78, "roundtrip")
LBNL_SILL = os.path.join(HERE, "..", "..", "..", "research", "samples", "lbnl-libraryfemtherm", "products",
                         "sample-sill.thmz")

spec = importlib.util.spec_from_file_location("therm_results_78", SCRIPT)
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tr.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def case(result, name="U-factor"):
    return next(c for c in result["cases"] if c["case"] == name)


def tag(result, name, case_name="U-factor"):
    return next(t for t in case(result, case_name)["tags"] if t["tag"] == name)


class SillMeshTagTable(unittest.TestCase):
    """Probe research/therm78-interface.md section 3.3 table, U-factor case."""

    @classmethod
    def setUpClass(cls):
        cls.r = tr.read_results(SILL_MESH)

    def test_all_nodes(self):
        t = case(self.r)["temperature"]
        self.assertEqual(t["nodes"], 859)
        self.assertAlmostEqual(t["tmin_c"], -16.89, places=2)
        self.assertAlmostEqual(t["tmax_c"], 21.00, places=2)

    def test_tag_table(self):
        want = {"Edge": (13, 0.81, 9.63, 5.84), "Frame": (29, 0.81, 19.16, 13.66),
                "SHGC Exterior": (22, -16.89, -10.82, -15.05)}
        for name, (n, lo, hi, mean) in want.items():
            t = tag(self.r, name)["temperature"]
            self.assertEqual(t["nodes"], n, name)
            self.assertAlmostEqual(t["tmin_c"], lo, places=2, msg=name)
            self.assertAlmostEqual(t["tmax_c"], hi, places=2, msg=name)
            self.assertAlmostEqual(t["tmean_c"], mean, places=2, msg=name)

    def test_displayed_is_projected_y(self):
        for name, u in (("Frame", 2.015155), ("Edge", 2.337128), ("SHGC Exterior", 1.874959)):
            d = tag(self.r, name)["displayed"]
            self.assertEqual((d["type"], d["source"]), ("Projected Y", "7.8-sill-rule"))
            self.assertAlmostEqual(d["u_factor"], u, places=6)

    def test_contract_and_error_norm_unavailable(self):
        self.assertEqual((self.r["skill"], self.r["version"], self.r["therm_format"]),
                         ("therm-results", 1, "thmx-7.8"))
        self.assertEqual(self.r["cross_section"], "Sill")
        self.assertTrue(all(c["simulation_error_pct"] is None for c in self.r["cases"]))
        self.assertTrue(any("error energy norm: unavailable" in n for n in self.r["notes"]))
        self.assertEqual(self.r["warnings"], [])

    def test_cr_case_read_when_present(self):
        self.assertAlmostEqual(tag(self.r, "Frame", "Condensation Resistance")["displayed"]["u_factor"],
                               1.971377, places=6)

    def test_frame_cavities_normalised(self):
        names = {c["base_name"] for c in case(self.r)["frame_cavities"]}
        self.assertEqual(names, {"Frame Cavity NFRC 100-2001", "Frame Cavity NFRC 100"})


class CentredOriginCoverage(unittest.TestCase):
    """Review 2.6: every convection-BC node lands on a BC segment, on THERM-written mesh files and on
    therm-model outputs solved by THERM (built-with-mesh/)."""

    def test_full_coverage(self):
        files = [SILL_MESH, JAMB_MESH, DIVIDER_MESH] + [os.path.join(ROUNDTRIP, s + "_solved.thmx") for s in
                                                        ("r0base", "r1woodx2", "r2gaphalf", "r3film5", "r5libbc")]
        for f in files:
            r = tr.read_results(f)
            for c in r["cases"]:
                on, total = map(int, c["surface_nodes_on_segments"].split("/"))
                self.assertEqual(on, total, "%s %s" % (f, c["case"]))
                self.assertGreater(total, 0)
            self.assertFalse([w for w in r["warnings"] if "convection-BC" in w], f)

    def test_full_coverage_hand_built(self):
        # Review 2.6: therm-model outputs that THERM solved with ExportMeshToTHMX=1.
        built = os.path.join(T78, "built-with-mesh")
        for stem in ("mdl_simple_frame_solved", "mdl_wall_solved"):
            f = os.path.join(built, stem + ".thmx")
            r = tr.read_results(f)
            self.assertTrue(r["cases"], f)
            for c in r["cases"]:
                on, total = map(int, c["surface_nodes_on_segments"].split("/"))
                self.assertEqual(on, total, "%s %s" % (stem, c["case"]))
                self.assertGreater(total, 0)
            self.assertFalse([w for w in r["warnings"] if "convection-BC" in w], stem)
        # Wall interior surface: 21 - U * 39 / 7.7 = 15.26 C on every Frame node.
        t = tag(tr.read_results(os.path.join(built, "mdl_wall_solved.thmx")), "Frame")["temperature"]
        self.assertAlmostEqual(t["tmean_c"], 21 - 1.132974 * 39 / 7.7, places=1)


class RoundTrip(unittest.TestCase):
    """Probe section 4.3, U-factor case, Projected Y."""
    WANT = {
        "r0base": (2.016345, 2.337976, 1.876081),
        "r1woodx2": (2.887787, 2.293155, 2.737644),
        "r2gaphalf": (1.978716, 1.837270, 1.922887),
        "r3film5": (1.943339, 2.680637, 1.991903),
        "extH13": (1.895095, 2.201090, 1.862365),
        "butyl24": (2.060860, 2.342297, 1.901422),
    }

    def test_values(self):
        for stem, us in self.WANT.items():
            r = tr.read_results(os.path.join(ROUNDTRIP, stem + "_solved.thmx"))
            for name, u in zip(("Frame", "Edge", "SHGC Exterior"), us):
                self.assertAlmostEqual(tag(r, name)["displayed"]["u_factor"], u, places=6, msg=stem + name)

    def test_glass_plane_never_shown(self):
        # r0base's raw glass-plane Frame value is the Projected X fallback 1.216263.
        r = tr.read_results(os.path.join(ROUNDTRIP, "r0base_solved.thmx"))
        self.assertNotIn("Projected in glass plane", json.dumps(r["cases"]))
        for c in r["cases"]:
            for t in c["tags"]:
                self.assertNotEqual(t["displayed"]["u_factor"], 1.216263)
        self.assertAlmostEqual(tag(r, "Frame")["displayed"]["u_factor"], 2.016345, places=6)

    def test_heat_flux_derived(self):
        r = tr.read_results(os.path.join(ROUNDTRIP, "r1woodx2_solved.thmx"))
        t = tag(r, "Frame")
        y = next(p for p in t["projections"] if p["type"] == "Projected Y")
        self.assertAlmostEqual(t["heat_flux_w_m2"], y["u_factor"] * y["length_mm"] / 1000 * t["delta_t"],
                               places=3)


class Labels(unittest.TestCase):
    def proj(self, label):
        el = ET.fromstring('<Projection xmlns="http://windows.lbl.gov"><Length-type>%s</Length-type>'
                           '<Length units="mm" value="10.0"/><U-factor value="2.0"/></Projection>' % label)
        return tr.read_projection_78(el)

    def test_six_labels(self):
        for label, want in (("Total length", "Total Length"), ("Projected X", "Projected X"),
                            ("Projected Y", "Projected Y"), ("Custom length", "Custom Length"),
                            ("Projected in glass plane", "Projected in glass plane"),
                            ("Custom rotation", "Custom Rotation")):
            self.assertEqual(self.proj(label)["type"], want)

    def test_case_insensitive(self):
        self.assertEqual(self.proj("TOTAL LENGTH")["type"], "Total Length")
        self.assertEqual(self.proj("projected y")["type"], "Projected Y")

    def test_na_is_omitted(self):
        el = ET.fromstring('<Projection xmlns="http://windows.lbl.gov"><Length-type>Custom length</Length-type>'
                           '<U-factor value="NA"/></Projection>')
        self.assertIsNone(tr.read_projection_78(el))


class NormaliseName(unittest.TestCase):
    """The 8 cases of research/library-record-78.md section 3, verbatim."""
    CASES = [
        ("fromx:sample-sill:Wood (Pine, Spruce, Fir)", "Wood (Pine, Spruce, Fir)"),
        ("Frame Cavity_Cavity_16_Cavity_16", "Frame Cavity"),
        ("sample-sill:Frame Cavity NFRC 100-2001_Cavity_16_Cavity_16", "Frame Cavity NFRC 100-2001"),
        ("sample_sill:Butyl Rubber ", "Butyl Rubber"),
        ("sample-sill:Sample GlzSys (ID:8) U-factor Inside Film", "Sample GlzSys (ID:8) U-factor Inside Film"),
        ("Int simple 7.7", "Int simple 7.7"),
        ("  NFRC 100-2010 Exterior ", "NFRC 100-2010 Exterior"),
        ("Frame Cavity NFRC 100_Cavity_49", "Frame Cavity NFRC 100"),
    ]

    def test_contract_cases(self):
        for raw, want in self.CASES:
            self.assertEqual(tr.normalise_name(raw), want, raw)


class Sections(unittest.TestCase):
    def test_jamb_flagged(self):
        r = tr.read_results(JAMB_MESH)
        self.assertTrue(any("'Jamb'" in w and "checked only for a Sill" in w for w in r["warnings"]))
        self.assertEqual(tag(r, "Frame")["displayed"]["source"], "7.8-fallback")

    def test_divider_flagged(self):
        r = tr.read_results(DIVIDER_MESH)
        self.assertTrue(any("'Vertical Divider'" in w for w in r["warnings"]))


class Errors(unittest.TestCase):
    def test_unsolved_thmx(self):
        code, _, err = run(os.path.join(T78, "wall", "handmade-wall.thmx"))
        self.assertEqual(code, 1)
        self.assertIn("no <Results> block", err)

    def test_missing_thmx(self):
        code, _, err = run(os.path.join(T78, "nope.thmx"))
        self.assertEqual(code, 1)
        self.assertIn("file not found", err)

    def test_no_mesh_warns(self):
        r = tr.read_results(os.path.join(T78, "wall", "handmade-wall_solved.thmx"))
        self.assertTrue(any("no <Mesh>" in w for w in r["warnings"]))
        self.assertAlmostEqual(tag(r, "Frame")["displayed"]["u_factor"], 1.132974, places=6)


class CliAndDispatch(unittest.TestCase):
    def test_cli_csv(self):
        with tempfile.TemporaryDirectory() as d:
            out, csvp = os.path.join(d, "r.json"), os.path.join(d, "r.csv")
            code, _, err = run(SILL_MESH, "--out", out, "--csv", csvp)
            self.assertEqual(code, 0, err)
            with open(csvp, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            self.assertTrue(any(r.startswith("U-factor,Frame,") and ",Projected Y," in r and ",True," in r
                                for r in rows))
            self.assertFalse(any("glass plane" in r for r in rows))

    def test_degenerate_projections_split_out(self):
        # Built model: Edge has Projected X, Custom Length and Custom Rotation at length ~0.
        built = os.path.join(T78, "built-with-mesh", "mdl_simple_frame_solved.thmx")
        with tempfile.TemporaryDirectory() as d:
            out, csvp = os.path.join(d, "r.json"), os.path.join(d, "r.csv")
            code, _, err = run(built, "--out", out, "--csv", csvp)
            self.assertEqual(code, 0, err)
            with open(out, encoding="utf-8") as fh:
                r = json.load(fh)
            with open(csvp, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
        edge = tag(r, "Edge")
        self.assertFalse(any(p["degenerate"] for p in edge["projections"]))
        self.assertEqual(sorted(p["type"] for p in edge["degenerate_projections"]),
                         ["Custom Length", "Custom Rotation", "Projected X"])
        self.assertTrue(all(p["degenerate"] for p in edge["degenerate_projections"]))
        self.assertTrue(rows[0].split(",")[7] == "degenerate")
        self.assertFalse(any(row.split(",")[7] == "True" for row in rows[1:]))

    def test_thmz_keeps_degenerate_in_projections(self):
        # 8.1 output is unchanged: no degenerate_projections key, degenerate rows stay listed.
        r = tr.read_results(LBNL_SILL)
        tags = [t for c in r["cases"] for t in c["tags"]]
        self.assertFalse(any("degenerate_projections" in t for t in tags))

    def test_thmz_still_uses_81_reader(self):
        self.assertEqual(tr.read_results(LBNL_SILL), tr.read_thmz(LBNL_SILL))
        self.assertNotIn("therm_format", tr.read_results(LBNL_SILL))


if __name__ == "__main__":
    unittest.main()
