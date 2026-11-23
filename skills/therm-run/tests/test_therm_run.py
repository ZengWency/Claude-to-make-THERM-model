"""Tests for therm-run with a fake THERM process. Run from the skill folder: python -m unittest discover -s tests

The log texts below are copied from real THERM 8.1.68 and 7.8.80 runs on the test host (19 Nov 2026).
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "therm_run.py")
spec = importlib.util.spec_from_file_location("therm_run", SCRIPT)
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)

PW = "s3cret-pw"
LOG_OK_81 = "Thu Nov 19 09:28:26 2026\r\n{f}\r\nCalculation complete.  Calculation complete.  \r\n0"
LOG_FAIL_78 = ("Thu Nov 19 09:31:20 2026\r\n\r\nYou need to have at least two non-adiabatic boundary conditions "
               "to do a simulation.  Check your boundary condition definitions.Calculation not successful\r\n2")


# Real THERM 7.8.80 log of r4badmat (export errors before the timestamp), and a trimmed results block.
LOG_BADMAT_78 = ("Error when creating XML export file: material No Such Material was not found in the library\r\r\n"
                 "\r\r\nFri Nov 20 00:48:26 2026\r\n\r\nError: Unknown material: No Such MaterialCalculation "
                 "not successful\r\n2\r\n")
THMX = '<?xml version="1.0"?>\n<THERM-XML xmlns="http://windows.lbl.gov">\n<Units>SI</Units>\n'
THMX_RESULTS = ("<Results><Case><ModelType>U-factor</ModelType><U-factors><Tag>Frame</Tag>"
                "<Projection><Length-type>Projected Y</Length-type><U-factor units=\"W/m2-K\" value=\"2.016345\"/>"
                "</Projection></U-factors></Case></Results>\n")
INI_78 = "[THERM]\r\nSaveXML=0\r\nDocPath=C:\\x\r\n[WINDOW7]\r\nSaveXML=0\r\n"


class FakeProc:
    """Stands in for THERM: writes the log (and results) named in argv, or hangs."""
    calls = []

    def __init__(self, argv, behaviour, **_):
        FakeProc.calls.append(argv)
        self.pid = 4242
        self.behaviour = behaviour
        flag = "-thmz" if "-thmz" in argv else "-thmx"
        target = argv[argv.index(flag) + 1]
        log = argv[argv.index("-log") + 1]
        if behaviour == "ok":
            with open(log, "w") as fh:
                fh.write(LOG_OK_81.format(f=target))
            with zipfile.ZipFile(target, "a") as zf:
                zf.writestr("SteadyStateResults.xml", "<SteadyStateResults/>")
        elif behaviour == "fail":
            with open(log, "w") as fh:
                fh.write(LOG_FAIL_78)
        elif behaviour in ("ok78", "ok78_noresults"):
            FakeProc.ini_text = open(argv[argv.index("-ini") + 1], encoding="latin-1").read()
            with open(log, "w") as fh:
                fh.write("Thu Nov 19 09:31:21 2026\r\n\r\nCalculation complete.  \r\n0")
            with open(os.path.splitext(target)[0] + ".thm", "wb") as fh:
                fh.write(b"THM")
            if behaviour == "ok78":
                with open(target, "w") as fh:  # THERM rewrites the .thmx with results
                    fh.write(THMX + THMX_RESULTS + "</THERM-XML>\n")
        elif behaviour == "badmat78":
            with open(log, "w") as fh:
                fh.write(LOG_BADMAT_78)
        elif behaviour == "crash":
            self.code = 0xC0000374
        elif behaviour == "nofortran":
            self.code = 0xC0000135
        # "nolog": wrong password, THERM exits at once and writes nothing

    def wait(self, timeout=None):
        if self.behaviour == "hang":
            raise subprocess.TimeoutExpired("THERM8.exe", timeout)
        return getattr(self, "code", 4294967295)  # THERM 8.1.68/7.8.80 return 0xFFFFFFFF even after a good solve


def popen_for(behaviour):
    return lambda argv, **kw: FakeProc(argv, behaviour, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.exe8 = self.touch("bin8", "THERM8.exe")
        self.exe7 = self.touch("bin7", "THERM7.exe")
        FakeProc.calls = []
        patcher = mock.patch.dict(os.environ, {"THERM_CLI_PW": PW})
        patcher.start()
        self.addCleanup(patcher.stop)

    def touch(self, sub, name):
        d = os.path.join(self.dir, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        open(p, "wb").close()
        return p

    def model(self, name="m.thmz", sub="in"):
        p = os.path.join(self.dir, sub, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("Model.xml", "<Model/>")
        return p

    def run_main(self, argv, behaviour="ok"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tr.main(argv, popen=popen_for(behaviour))
        return code, out.getvalue(), err.getvalue()


class ParseLog(unittest.TestCase):
    def test_ok_81(self):
        self.assertEqual(tr.parse_log(LOG_OK_81.format(f=r"C:\a\b.thmz"), r"C:\a\b.thmz"), (True, None, 0))

    def test_fail_78_keeps_message(self):
        ok, msg, code = tr.parse_log(LOG_FAIL_78, "x.thmx")
        self.assertFalse(ok)
        self.assertEqual(code, 2)
        self.assertTrue(msg.startswith("You need to have at least two non-adiabatic"))
        self.assertTrue(msg.endswith("Calculation not successful"))

    def test_no_log_points_at_password(self):
        ok, msg, code = tr.parse_log(None, "x")
        self.assertFalse(ok)
        self.assertIn("THERM_CLI_PW", msg)


class Run(Base):
    def test_out_dir_solves_copy_and_masks_password(self):
        src = self.model()
        code, out, err = self.run_main([src, "--exe", self.exe8, "--out-dir", os.path.join(self.dir, "out")])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual((r["skill"], r["version"], r["summary"]["solved"]), ("therm-run", 1, 1))
        f = r["files"][0]
        self.assertEqual(f["status"], "solved")
        self.assertEqual(f["solved_file"], os.path.join(self.dir, "out", "m.thmz"))
        self.assertIn("SteadyStateResults.xml", f["result_entries"])
        self.assertEqual(f["exit_code"], 4294967295)
        with zipfile.ZipFile(src) as zf:  # input untouched
            self.assertEqual(zf.namelist(), ["Model.xml"])
        self.assertNotIn(PW, out + err)
        self.assertIn("-pw ***", r["command"])
        argv = FakeProc.calls[0]
        self.assertEqual(argv[1:3], ["-pw", PW])
        self.assertEqual(argv[3], "-thmz")
        self.assertEqual(argv[-2:], ["-calc", "-exit"])
        self.assertTrue(os.path.isabs(argv[4]))

    def test_folder_is_sequential_and_sorted(self):
        self.model("b.thmz")
        self.model("a.thmz")
        code, out, _ = self.run_main([os.path.join(self.dir, "in"), "--exe", self.exe8])
        self.assertEqual(code, 0)
        self.assertEqual([os.path.basename(f["input"]) for f in json.loads(out)["files"]], ["a.thmz", "b.thmz"])

    def test_failure_reports_therm_text_and_exit_2(self):
        code, out, err = self.run_main([self.model(), "--exe", self.exe8], behaviour="fail")
        self.assertEqual(code, 2)
        f = json.loads(out)["files"][0]
        self.assertEqual((f["status"], f["log_code"]), ("failed", 2))
        self.assertIn("non-adiabatic boundary conditions", f["error"])
        self.assertIn("non-adiabatic", err)

    def test_wrong_password_no_log(self):
        code, out, _ = self.run_main([self.model(), "--exe", self.exe8], behaviour="nolog")
        self.assertEqual(code, 2)
        self.assertIn("THERM_CLI_PW", json.loads(out)["files"][0]["error"])

    def test_log_without_results_entry_is_a_failure(self):
        src = self.model()

        def popen(argv, **kw):
            with open(argv[argv.index("-log") + 1], "w") as fh:
                fh.write(LOG_OK_81.format(f=src))
            return FakeProc(argv, "nolog")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = tr.main([src, "--exe", self.exe8], popen=popen)
        self.assertEqual(code, 2)
        self.assertIn("no SteadyStateResults", json.loads(out.getvalue())["files"][0]["error"])

    def test_stale_log_is_removed(self):
        src = self.model()
        with open(os.path.splitext(src)[0] + ".log", "w") as fh:
            fh.write(LOG_OK_81.format(f=src))
        code, out, _ = self.run_main([src, "--exe", self.exe8], behaviour="nolog")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["files"][0]["status"], "failed")

    def test_timeout_kills_and_reports(self):
        with mock.patch.object(tr, "kill_tree") as kill, \
                mock.patch.object(tr, "window_texts", return_value=["Error | OK | Invalid Filename"]):
            code, out, _ = self.run_main([self.model(), "--exe", self.exe8, "--timeout", "1"], behaviour="hang")
        self.assertEqual(code, 2)
        kill.assert_called_once()
        f = json.loads(out)["files"][0]
        self.assertEqual(f["status"], "timeout")
        self.assertIn("killed", f["error"])
        self.assertIn("Invalid Filename", f["error"])

    def test_period_in_name_refused_without_launch(self):
        code, out, _ = self.run_main([self.model("gap18.51.thmz"), "--exe", self.exe8])
        self.assertEqual(code, 2)
        f = json.loads(out)["files"][0]
        self.assertEqual(f["status"], "error")
        self.assertIn("refused before starting THERM", f["error"])
        self.assertIn("gap18p51.thmz", f["error"])
        self.assertEqual(FakeProc.calls, [])

    def test_period_in_name_refused_with_out_dir(self):
        out = os.path.join(self.dir, "solved")
        code, res, _ = self.run_main([self.model("gap18.51.thmz"), "--exe", self.exe8, "--out-dir", out])
        self.assertEqual(code, 2)
        self.assertIn("refused before starting THERM", json.loads(res)["files"][0]["error"])
        self.assertEqual(FakeProc.calls, [])
        self.assertFalse(os.path.exists(os.path.join(out, "gap18.51.thmz")))


class Therm78(Base):
    def setUp(self):
        super().setUp()
        self.ini = os.path.join(self.dir, "therm7.8.ini")
        with open(self.ini, "w", newline="") as fh:
            fh.write(INI_78)
        for name, val in (("main_ini_78", lambda env_ini=None: self.ini), ("therm_running", lambda image: False)):
            p = mock.patch.object(tr, name, val)
            p.start()
            self.addCleanup(p.stop)

    def thmx(self, name="s.thmx", sub="in"):
        p = os.path.join(self.dir, sub, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(THMX + "</THERM-XML>\n")
        return p

    def test_solves_scratch_copy_and_writes_thmx_and_thm(self):
        src = self.thmx()
        out = os.path.join(self.dir, "out")
        with open(self.ini, "rb") as fh:
            ini_before = fh.read()
        code, res, err = self.run_main([src, "--exe", self.exe7, "--out-dir", out], behaviour="ok78")
        self.assertEqual(code, 0, err)
        r = json.loads(res)
        f = r["files"][0]
        self.assertEqual((r["therm"]["version"], f["status"], f["has_results"]), ("7.8", "solved", True))
        self.assertEqual(f["solved_file"], os.path.join(out, "s.thmx"))
        self.assertEqual(f["thm_file"], os.path.join(out, "s.thm"))
        self.assertIn("<Results>", open(f["solved_file"]).read())
        self.assertNotIn("<Results>", open(src).read())  # input untouched
        argv = FakeProc.calls[0]
        self.assertEqual(argv[1:3], ["-pw", PW])
        self.assertEqual(argv[-2:], ["-calc", "-exit"])
        work = argv[argv.index("-thmx") + 1]
        self.assertTrue(work.startswith(tempfile.gettempdir()))
        self.assertFalse(os.path.exists(os.path.dirname(work)))  # scratch removed
        self.assertIn("SaveXML=1", FakeProc.ini_text.split("[WINDOW7]")[0])
        self.assertIn("ExportMeshToTHMX=1", FakeProc.ini_text)
        self.assertIn("[WINDOW7]\nSaveXML=0", FakeProc.ini_text)  # other sections kept
        with open(self.ini, "rb") as fh:
            self.assertEqual(fh.read(), ini_before)  # main INI untouched
        self.assertNotIn(PW, res + err)

    def test_success_log_without_results_fails(self):
        src = self.thmx()  # shipped samples carry an old <Results> block: it must not count
        with open(src, "w") as fh:
            fh.write(THMX + THMX_RESULTS + "<Mesh><MeshInput/></Mesh>\n</THERM-XML>\n")
        code, res, _ = self.run_main([src, "--exe", self.exe7], behaviour="ok78_noresults")
        self.assertEqual(code, 2)
        self.assertIn("no <Results>", json.loads(res)["files"][0]["error"])

    def test_export_errors_before_timestamp(self):
        ok, msg, code = tr.parse_log_78(LOG_BADMAT_78)
        self.assertEqual((ok, code), (False, 2))
        self.assertTrue(msg.startswith("Error: Unknown material: No Such Material"))
        code, res, _ = self.run_main([self.thmx(), "--exe", self.exe7], behaviour="badmat78")
        f = json.loads(res)["files"][0]
        self.assertEqual((code, f["status"], f["log_code"], f["solved_file"]), (2, "failed", 2, None))

    def test_crash_and_missing_fortran(self):
        code, res, _ = self.run_main([self.thmx(), "--exe", self.exe7], behaviour="crash")
        f = json.loads(res)["files"][0]
        self.assertEqual((code, f["status"]), (2, "crashed"))
        self.assertIn("0xC0000374", f["error"])
        code, res, _ = self.run_main([self.thmx(), "--exe", self.exe7], behaviour="nofortran")
        f = json.loads(res)["files"][0]
        self.assertEqual(f["status"], "crashed")
        self.assertIn("windows.lbl.gov/redistributable-packages", f["error"])

    def test_wrong_password_no_log(self):
        code, res, _ = self.run_main([self.thmx(), "--exe", self.exe7], behaviour="nolog")
        self.assertEqual(code, 2)
        self.assertIn("THERM_CLI_PW", json.loads(res)["files"][0]["error"])

    def test_period_in_name_refused_without_launch(self):
        code, res, _ = self.run_main([self.thmx("sill.v2.thmx"), "--exe", self.exe7])
        f = json.loads(res)["files"][0]
        self.assertEqual((code, f["status"]), (2, "error"))
        self.assertIn("sillpv2.thmx", f["error"])
        self.assertEqual(FakeProc.calls, [])

    def test_duplicate_stems_refused(self):
        code, _, err = self.run_main([self.thmx("m.thmx", "x"), self.thmx("m.thmx", "y"), "--exe", self.exe7])
        self.assertEqual(code, 1)
        self.assertIn("share the file stem", err)

    def test_non_ascii_path_refused(self):
        code, res, _ = self.run_main([self.thmx("s.thmx", "caf\u00e9"), "--exe", self.exe7])
        f = json.loads(res)["files"][0]
        self.assertEqual((code, f["status"]), (2, "error"))
        self.assertIn("non-ASCII", f["error"])
        self.assertEqual(FakeProc.calls, [])

    def test_running_therm7_refused(self):
        with mock.patch.object(tr, "therm_running", lambda image: True):
            code, _, err = self.run_main([self.thmx(), "--exe", self.exe7])
        self.assertEqual(code, 1)
        self.assertIn("already running", err)
        self.assertEqual(FakeProc.calls, [])

    def test_temp_ini_adds_missing_keys(self):
        src = os.path.join(self.dir, "a.ini")
        with open(src, "w") as fh:
            fh.write("[THERM]\nDocPath=x\nConradOutput=1\n")
        dst = os.path.join(self.dir, "b.ini")
        self.assertTrue(tr.write_temp_ini(src, dst))
        text = open(dst, encoding="latin-1").read()
        self.assertIn("SaveXML=1", text)
        self.assertIn("ExportMeshToTHMX=1", text)


class VersionChoice(Base):
    def args(self, *argv):
        return tr.build_parser().parse_args(list(argv))

    def test_78_picked_when_both_installed(self):
        with mock.patch.dict(tr.DEFAULT_EXE, {"7.8": self.exe7, "8.1": self.exe8}):
            folder = os.path.join(self.dir, "empty")
            os.makedirs(folder)
            self.assertEqual(tr.resolve_therm(self.args(folder))[:2], (self.exe7, "7.8"))
            env = os.path.join(self.dir, "env.json")
            with open(env, "w") as fh:
                json.dump({"skill": "therm-setup", "installs": [{"version": "8.1", "exe": self.exe8},
                                                               {"version": "7.8", "exe": self.exe7}]}, fh)
            self.assertEqual(tr.resolve_therm(self.args(folder, "--env", env))[:2], (self.exe7, "7.8"))

    def test_81_when_78_missing(self):
        with mock.patch.dict(tr.DEFAULT_EXE, {"7.8": os.path.join(self.dir, "none.exe"), "8.1": self.exe8}):
            folder = os.path.join(self.dir, "empty")
            os.makedirs(folder)
            self.assertEqual(tr.resolve_therm(self.args(folder))[1], "8.1")

    def test_extension_picks_version(self):
        with mock.patch.dict(tr.DEFAULT_EXE, {"7.8": self.exe7, "8.1": self.exe8}):
            self.assertEqual(tr.resolve_therm(self.args(self.model("a.thmz")))[1], "8.1")
            x = os.path.join(self.dir, "a.thmx")
            open(x, "w").close()
            self.assertEqual(tr.resolve_therm(self.args(x))[1], "7.8")


class Refusals(Base):
    def test_no_password(self):
        with mock.patch.dict(os.environ, {"THERM_CLI_PW": ""}):
            code, out, err = self.run_main([self.model(), "--exe", self.exe8])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("SetEnvironmentVariable('THERM_CLI_PW'", err)

    def test_missing_exe(self):
        code, _, err = self.run_main([self.model(), "--exe", os.path.join(self.dir, "THERM8.exe")])
        self.assertEqual(code, 1)
        self.assertIn("THERM not found", err)

    def test_out_dir_name_clash(self):
        a, b = self.model("m.thmz", "x"), self.model("m.thmz", "y")
        code, _, err = self.run_main([a, b, "--exe", self.exe8, "--out-dir", os.path.join(self.dir, "o")])
        self.assertEqual(code, 1)
        self.assertIn("both be written", err)

    def test_env_json_from_setup(self):
        env = os.path.join(self.dir, "env.json")
        with open(env, "w") as fh:
            json.dump({"skill": "therm-setup", "version": 1, "installs": [{"version": "8.1", "exe": self.exe8}]}, fh)
        code, out, _ = self.run_main([self.model(), "--env", env])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["therm"]["exe"], os.path.abspath(self.exe8))


if __name__ == "__main__":
    unittest.main()
