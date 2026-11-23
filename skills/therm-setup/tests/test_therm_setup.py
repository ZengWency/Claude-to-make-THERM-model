"""Tests for therm-setup. Run from the skill folder: python -m unittest discover -s tests

They run on Windows or Linux: THERM, the registry and the solve are replaced with temp files
and small fake scripts. The INI text is trimmed from the THERM 8.1.68 test host.
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "therm_setup.py")
spec = importlib.util.spec_from_file_location("therm_setup", SCRIPT)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

INI_81 = textwrap.dedent("""\
    [THERM]
    BCLibName={d}\\Lib\\BoundaryConditionsTransient.xml
    UfactorLibName={d}\\Lib\\UFactor.xml
    DocPath={d}
    SimPath={d}\\Sim
    [WINDOW8]
    DrawGrid=false
    [THERM]
    DocPath=ignored-second-section
    [XMLLibraries]
    BoundaryCondtionsSteadyStateXML={d}\\Lib\\BoundaryConditionsSteadyState.xml
    MaterialXML={d}\\Lib\\Materials.xml
    """)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def write(self, rel, text=""):
        p = os.path.normpath(os.path.join(self.dir, rel))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p


class Ini(Base):
    def setUp(self):
        super().setUp()
        d = self.dir.replace("/", "\\") if os.name == "nt" else self.dir
        self.ini = self.write("therm8.1.ini", INI_81.format(d=d).replace("\\", os.sep))
        self.write("Lib/BoundaryConditionsSteadyState.xml")
        self.write("Lib/BoundaryConditionsTransient.xml")

    def test_first_section_wins(self):
        self.assertEqual(ts.parse_ini(self.ini)["THERM"]["DocPath"].replace("\\", os.sep), self.dir)

    def test_81_bc_library_comes_from_xml_libraries(self):
        info, warnings = ts.check_ini("8.1", self.ini, "test")
        self.assertEqual(info["bc_library"]["file"].split(os.sep)[-1], "BoundaryConditionsSteadyState.xml")
        self.assertEqual(info["bc_library"]["key"], "BoundaryCondtionsSteadyStateXML")
        self.assertTrue(info["bc_library"]["exists"])
        # Materials.xml is missing -> warning; the legacy UFactor.xml key is not warned on 8.1
        self.assertEqual(len(warnings), 1)
        self.assertIn("MaterialXML", warnings[0])

    def test_78_uses_legacy_keys(self):
        info, warnings = ts.check_ini("7.8", self.ini, "test")
        self.assertEqual(info["bc_library"]["key"], "BCLibName")
        self.assertTrue(any("UfactorLibName" in w for w in warnings))

    def test_missing_ini(self):
        info, warnings = ts.check_ini("8.1", os.path.join(self.dir, "nope.ini"), "test")
        self.assertFalse(info["exists"])


class Runtimes(Base):
    def test_intel_dlls_found_in_install_dir_or_path(self):
        inst = os.path.dirname(self.write("THERM8.1/libifcoremd.dll"))
        self.write("THERM8.1/libmmd.dll")
        other = os.path.dirname(self.write("rt/svml_dispmd.dll"))
        ok, detail, missing = ts.check_intel(inst, path_dirs=[other], extra_dirs=[])
        self.assertTrue(ok, detail)
        ok, detail, missing = ts.check_intel(inst, path_dirs=[], extra_dirs=[])
        self.assertEqual((ok, missing), (False, ["svml_dispmd.dll"]))

    def test_password_states_never_expose_value(self):
        self.assertEqual(ts.check_password({"THERM_CLI_PW": "x1"}, {}), (True, "process environment", None))
        ok, source, fix = ts.check_password({}, {"THERM_CLI_PW": "x1"})
        self.assertEqual((ok, fix), (False, ts.FIX_PW_RESTART))
        ok, source, fix = ts.check_password({}, {})
        self.assertFalse(ok)
        self.assertIn("SetEnvironmentVariable('THERM_CLI_PW'", fix)
        self.assertNotIn("x1", source + fix)


class Installs(Base):
    def test_default_folder_and_override(self):
        exe8 = self.write("lbnl/THERM8.1/THERM8.exe")
        self.write("lbnl/THERM7.8/THERM7.exe")
        found = ts.find_installs(["8.1", "7.8"], [], program_dirs=[os.path.join(self.dir, "lbnl")])
        self.assertEqual([i["version"] for i in found], ["7.8", "8.1"])
        self.assertEqual(found[1]["exe"], exe8)
        other = self.write("elsewhere/THERM8.exe")
        found = ts.find_installs(["8.1"], [other], program_dirs=[os.path.join(self.dir, "lbnl")])
        self.assertEqual([(i["exe"], i["source"]) for i in found], [(other, "--exe")])


FAKE_RUN = textwrap.dedent("""\
    import json, sys, zipfile
    a = sys.argv[1:]
    f, out = a[0], a[a.index("--out") + 1]
    ok = {ok!r}
    with open(out, "w") as fh:
        json.dump({{"files": [{{"status": "solved" if ok else "failed", "ok": ok, "wall_time_s": 1.2,
                   "exit_code": 4294967295, "log_text": "x" if ok else None,
                   "error": None if ok else "THERM wrote no log"}}]}}, fh)
    """)
FAKE_RESULTS = textwrap.dedent("""\
    import json
    tags = {tags!r}
    print(json.dumps({{"cases": [{{"case": "U-factor", "tags": [
        {{"tag": k, "displayed": {{"u_factor": v}}}} for k, v in tags.items()]}}]}}))
    """)


class Smoke(Base):
    def smoke(self, ok=True, tags=None):
        run = self.write("run.py", FAKE_RUN.format(ok=ok))
        res = self.write("res.py", FAKE_RESULTS.format(tags=tags or dict(ts.SMOKE_EXPECTED)))
        return ts.smoke_solve({"version": "8.1", "exe": "THERM8.exe"}, 10, run_script=run, results_script=res)

    def test_bundled_sample_is_unsolved(self):
        with zipfile.ZipFile(ts.SMOKE_8) as zf:
            names = zf.namelist()
        self.assertIn("Model.xml", names)
        self.assertFalse([n for n in names if "Results" in n or n.startswith("Mesh")])

    def test_ok_matches_lbnl(self):
        r = self.smoke()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["matches_lbnl"])

    def test_u_mismatch_fails(self):
        r = self.smoke(tags={"Frame": 2.1, "Edge": 2.34569, "SHGC Exterior": 1.812795})
        self.assertFalse(r["ok"])
        self.assertIn("Frame", r["error"])

    def test_no_log_points_at_password(self):
        r = self.smoke(ok=False)
        self.assertFalse(r["ok"])
        self.assertIn("THERM_CLI_PW", r["fix"])

    def test_missing_therm_run(self):
        r = ts.smoke_solve({"version": "8.1", "exe": "x"}, 10, run_script=os.path.join(self.dir, "none.py"))
        self.assertIn("therm-run", r["fix"])


FAKE_RUN_78 = textwrap.dedent("""\
    import json, sys
    a = sys.argv[1:]
    f, out = a[0], a[a.index("--out") + 1]
    assert a[a.index("--therm-version") + 1] == "7.8"
    proj = "".join('<U-factors><Tag>%s</Tag><Projection><Length-type>Projected Y</Length-type>'
                   '<U-factor units="W/m2-K" value="%s"/></Projection></U-factors>' % kv for kv in {tags!r}.items())
    with open(f, "w") as fh:
        fh.write('<THERM-XML xmlns="http://windows.lbl.gov"><Results><Case><ModelType>U-factor</ModelType>'
                 + proj + '</Case></Results></THERM-XML>')
    with open(out, "w") as fh:
        json.dump({{"files": [{{"status": "solved", "ok": True, "wall_time_s": 0.6, "exit_code": 4294967295,
                   "log_text": "x", "solved_file": f}}]}}, fh)
    """)


class Smoke78(Base):
    def smoke(self, tags, file_version="7.8.80.0"):
        doc = os.path.dirname(os.path.dirname(self.write("doc/Samples/sample-sill.thmx", "<x/>")))
        run = self.write("run78.py", FAKE_RUN_78.format(tags=tags))
        inst = {"version": "7.8", "exe": "THERM7.exe", "file_version": file_version, "ini": {"doc_path": doc}}
        return ts.smoke_solve(inst, 10, run_script=run)

    def test_ok_matches_78_reference(self):
        r = self.smoke(dict(ts.SMOKE_EXPECTED_78))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["matches_reference"])
        self.assertTrue(r["sample"].endswith("sample-sill.thmx"))

    def test_mismatch_on_7880_fails(self):
        r = self.smoke(dict(ts.SMOKE_EXPECTED_78, Frame=2.05))
        self.assertFalse(r["ok"])
        self.assertIn("Frame", r["error"])

    def test_mismatch_on_other_build_warns(self):
        r = self.smoke(dict(ts.SMOKE_EXPECTED_78, Frame=2.05), file_version="7.8.74.0")
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["matches_reference"])
        self.assertIn("7.8.74.0", r["warning"])

    def test_within_half_percent_passes(self):
        r = self.smoke(dict(ts.SMOKE_EXPECTED_78, Edge=2.337976 * 1.004))
        self.assertTrue(r["ok"], r)


class MainContract(Base):
    def test_env_json_and_fixes(self):
        exe = self.write("bin/THERM8.exe")
        out = os.path.join(self.dir, "env.json")
        so, se = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"THERM_CLI_PW": ""}), mock.patch.object(ts, "reg_values", return_value={}), \
                contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = ts.main(["--therm-version", "8.1", "--exe", exe, "--skip-smoke", "--out", out])
        self.assertEqual(code, 1)
        with open(out, encoding="utf-8") as fh:
            env = json.load(fh)
        self.assertEqual((env["skill"], env["version"], env["ok"]), ("therm-setup", 1, False))
        self.assertEqual(env["installs"][0]["exe"], exe)
        self.assertEqual(env["default"]["exe"], exe)
        self.assertIn(ts.FIX_PW, env["fixes"])
        self.assertIn("fix:", se.getvalue())

    def test_78_is_default_when_both_installed(self):
        exe8, exe7 = self.write("bin/THERM8.exe"), self.write("bin/THERM7.exe")
        so = io.StringIO()
        with mock.patch.dict(os.environ, {"THERM_CLI_PW": "x"}), mock.patch.object(ts, "reg_values", return_value={}), \
                contextlib.redirect_stdout(so), contextlib.redirect_stderr(io.StringIO()):
            ts.main(["--exe", exe8, "--exe", exe7, "--skip-smoke"])
        env = json.loads(so.getvalue())
        self.assertEqual(env["default"], {"version": "7.8", "exe": exe7})
        self.assertIn("file_version", env["installs"][0])

    def test_conrad_output_warns_on_78(self):
        ini = self.write("t.ini", "[THERM]\nConradOutput=1\n")
        _, warnings = ts.check_ini("7.8", ini, "test")
        self.assertTrue(any("ConradOutput=1" in w for w in warnings))

    def test_ini_needs_one_version(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ts.main(["--ini", "x.ini"]), 1)


if __name__ == "__main__":
    unittest.main()
