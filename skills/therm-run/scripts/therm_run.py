"""Solve LBNL THERM files headless: one THERM process per file, under a timeout.

Runs, for each file:
  THERM7.exe -pw <THERM_CLI_PW> -log <log> -ini <temp ini> -thmx <copy> -calc -exit   (THERM 7.8)
  THERM8.exe -pw <THERM_CLI_PW> -thmz <file> -log <log> -calc -exit                   (THERM 8.1)
and decides pass/fail from the log ("Calculation complete."), not the exit code:
THERM 8.1.68 and 7.8.80 exit 0xFFFFFFFF even after a good solve.

With no --therm-version: .thmx inputs pick 7.8 and .thmz inputs pick 8.1; otherwise
7.8 when it is installed, else 8.1. THERM 7.8 solves a copy in a scratch folder under
%TEMP% with a temporary INI (SaveXML=1, ExportMeshToTHMX=1), then writes
<out>/<stem>.thmx (with results) and <stem>.thm.

The password comes only from the THERM_CLI_PW environment variable. It is passed to
THERM as an argument list (no shell, nothing echoed) and is masked in every output.
It is still visible in the OS process list while THERM runs.

Standard library only. Exit codes: 0 every file solved, 1 user/setup error before any
run (no password, no THERM, bad path; 7.8 also: duplicate stems, no settings INI, THERM7.exe
already running), 2 at least one file failed, crashed, timed out or was refused.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

SKILL = "therm-run"
VERSION = 1

DEFAULT_EXE = {
    "8.1": r"C:\Program Files (x86)\lbnl\THERM8.1\THERM8.exe",
    "7.8": r"C:\Program Files (x86)\lbnl\THERM7.8\THERM7.exe",
}
FILE_FLAG = {"8.1": "-thmz", "7.8": "-thmx"}
SUFFIX = {"8.1": ".thmz", "7.8": ".thmx"}
SUCCESS = "Calculation complete."
# THERM 8.1 writes these entries into the .thmz when a steady-state solve succeeds.
RESULT_ENTRIES = ("SteadyStateResults.xml", "SteadyStateResults.json")
PW_ENV = "THERM_CLI_PW"
PW_FIX = ("set THERM_CLI_PW with: [Environment]::SetEnvironmentVariable('THERM_CLI_PW', "
          "[System.Net.NetworkCredential]::new('', (Read-Host 'THERM CLI password' -AsSecureString))"
          ".Password, 'User')  then open a new terminal")
MASK = "***"
# Seen on THERM 8.1.68 and 7.8.80: a wrong -pw makes THERM exit at once without a log.
NO_LOG = "THERM wrote no log; this is what a wrong THERM_CLI_PW does (THERM exits at once). Check the password"

# THERM 7.8 settings and result checks (seen on 7.8.80).
INI_REG_78 = r"SOFTWARE\WOW6432Node\LBNL\THERM\7.8"
DEFAULT_INI_78 = r"C:\Users\Public\LBNL\Settings\therm7.8.ini"
INI_FORCE_78 = {"SaveXML": "1", "ExportMeshToTHMX": "1"}
THMX_NS = "{http://windows.lbl.gov}"
EXIT_NORMAL = 0xFFFFFFFF  # what THERM returns after a good and after a failed solve
EXIT_NO_FORTRAN = 0xC0000135  # STATUS_DLL_NOT_FOUND
FORTRAN_FIX = ("THERM 7.8 could not start (exit 0xC0000135, a DLL is missing): install the 32-bit Intel "
               "Fortran runtime and VC++ x86 redistributables from https://windows.lbl.gov/redistributable-packages")


class UserError(Exception):
    pass


def ext_version(paths):
    """7.8 when every file argument is .thmx, 8.1 when every one is .thmz, else None."""
    exts = {os.path.splitext(p)[1].lower() for p in paths if os.path.isfile(p)}
    return {frozenset([".thmx"]): "7.8", frozenset([".thmz"]): "8.1"}.get(frozenset(exts))


def version_for_exe(exe):
    name = os.path.basename(exe).lower()
    if name.startswith("therm8"):
        return "8.1"
    if name.startswith("therm7"):
        return "7.8"
    raise UserError("cannot tell the THERM version from %s; pass --therm-version" % exe)


def resolve_therm(args):
    """Return (exe, version, ini) from --exe, --env (therm-setup env.json) or the default path.

    With no version given: the input extension decides (.thmx 7.8, .thmz 8.1), else 7.8 when
    it is installed, else 8.1. ini is the settings INI env.json names for that install, or None.
    """
    exe, ver, ini = args.exe, args.therm_version, None
    if not ver and exe:
        ver = version_for_exe(exe)
    ver = ver or ext_version(args.paths)
    if not exe and args.env:
        try:
            with open(args.env, encoding="utf-8") as fh:
                env = json.load(fh)
        except (OSError, ValueError) as exc:
            raise UserError("cannot read env file %s: %s" % (args.env, exc))
        if env.get("skill") != "therm-setup":
            raise UserError("%s is not therm-setup output" % args.env)
        installs = env.get("installs") or []
        pick = [i for i in installs if i.get("exe") and (not ver or i.get("version") == ver)]
        if not pick:
            raise UserError("%s has no THERM install%s" % (args.env, " for " + ver if ver else ""))
        pick.sort(key=lambda i: i.get("version") != "7.8")  # 7.8 first when no version was asked for
        exe, ver = pick[0]["exe"], pick[0]["version"]
        ini = (pick[0].get("ini") or {}).get("path")
    if not exe:
        ver = ver or ("7.8" if os.path.isfile(DEFAULT_EXE["7.8"]) else "8.1")
        exe = DEFAULT_EXE[ver]
    ver = ver or version_for_exe(exe)
    if not os.path.isfile(exe):
        raise UserError("THERM not found at %s; install it or pass --exe (run therm-setup to check)" % exe)
    return os.path.abspath(exe), ver, ini


def collect_files(paths, suffix):
    files = []
    for p in paths:
        if os.path.isdir(p):
            found = sorted(os.path.join(p, n) for n in os.listdir(p)
                           if n.lower().endswith(suffix) and os.path.isfile(os.path.join(p, n)))
            if not found:
                raise UserError("no %s files in folder %s" % (suffix, p))
            files.extend(found)
        elif os.path.isfile(p):
            files.append(p)
        else:
            raise UserError("not found: %s" % p)
    # THERM resolves relative paths against its DocPath, so always pass absolute ones.
    return [os.path.abspath(f) for f in files]


def plan_targets(files, out_dir):
    """Map each input to the file THERM will solve. Refuse name clashes in out_dir."""
    if not out_dir:
        return [(f, f) for f in files]
    out_dir = os.path.abspath(out_dir)
    targets, seen = [], {}
    for f in files:
        t = os.path.join(out_dir, os.path.basename(f))
        key = os.path.normcase(t)
        if key in seen:
            raise UserError("two inputs would both be written to %s (%s and %s)" % (t, seen[key], f))
        if os.path.normcase(t) == os.path.normcase(f):
            raise UserError("--out-dir is the input's own folder; omit --out-dir to solve in place")
        seen[key] = f
        targets.append((f, t))
    return targets


def read_log(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def parse_log(text, target):
    """Return (ok, message, code) from a THERM log.

    Observed layout (8.1.68 and 7.8.80): line 1 a timestamp, line 2 the model path
    (8.1) or blank (7.8), then THERM's message, then a numeric code on the last line
    (0 after a good solve, 2 after "Calculation not successful").
    """
    if text is None:
        return False, NO_LOG, None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    code = None
    if len(lines) > 1 and lines[-1].lstrip("-").isdigit():
        code = int(lines.pop())
    body = [ln for ln in lines[1:] if os.path.normcase(ln) != os.path.normcase(target)]
    msg = " ".join(body).replace(SUCCESS, "").strip()
    ok = SUCCESS in text and code in (None, 0)
    return ok, (msg or None), code


def result_entries(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return [n for n in zf.namelist() if "Results" in n]
    except (OSError, zipfile.BadZipFile):
        return None


# ---- THERM 7.8 only ---------------------------------------------------------------------------

def main_ini_78(env_ini=None):
    """THERM 7.8's settings INI: env.json's, else the registry's, else the default path."""
    if env_ini:
        return env_ini
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, INI_REG_78) as key:
            return os.path.join(winreg.QueryValueEx(key, "INIPath")[0], winreg.QueryValueEx(key, "INIFileName")[0])
    except (ImportError, OSError):
        return DEFAULT_INI_78


def write_temp_ini(main_ini, path):
    """Copy main_ini to path with INI_FORCE_78 set in its first [THERM] section. main_ini is only read."""
    with open(main_ini, encoding="latin-1") as fh:
        lines = fh.read().splitlines()
    out, section, seen, done = [], None, set(), False
    for line in lines + ["[__end__]"]:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if section == "THERM" and not done:
                out.extend("%s=%s" % (k, v) for k, v in INI_FORCE_78.items() if k not in seen)
                done = True
            section = s[1:-1]
            if section == "__end__":
                break
        elif section == "THERM" and not done and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in INI_FORCE_78:
                if key in seen:
                    continue
                seen.add(key)
                line = "%s=%s" % (key, INI_FORCE_78[key])
        out.append(line)
    if not done:
        out += ["[THERM]"] + ["%s=%s" % kv for kv in INI_FORCE_78.items()]
    with open(path, "w", encoding="latin-1", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")
    conrad = any(ln.replace(" ", "").lower() == "conradoutput=1" for ln in out)
    return conrad


def parse_log_78(text):
    """Return (ok, message, code) from a THERM 7.8 log.

    Observed layout (7.8.80): optional "Error when creating XML export file" lines, a timestamp,
    a blank line (or the model path for -thm), the message, then the numeric code.
    """
    if text is None:
        return False, NO_LOG, None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    code = None
    if lines and lines[-1].lstrip("-").isdigit():
        code = int(lines.pop())
    stamp = next((i for i, ln in enumerate(lines)
                  if len(ln.split()) == 5 and ln.split()[3].count(":") == 2 and ln.split()[4].isdigit()), None)
    pre, body = (lines[:stamp], lines[stamp + 1:]) if stamp is not None else ([], lines)
    msg = " ".join(body).replace(SUCCESS, "").strip()
    ok = SUCCESS in " ".join(body) and code == 0
    if not ok and pre:
        msg = (msg + " (" if msg else "(") + "export errors before the run: " + "; ".join(sorted(set(pre))) + ")"
    return ok, (msg or None), code


def thmx_ufactor_case(path):
    """True when the .thmx has a top-level <Results> with a U-factor case holding U-factors."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False
    res = root.find(THMX_NS + "Results")
    if res is None:
        return False
    return any((c.findtext(THMX_NS + "ModelType") or "").strip() == "U-factor"
               and c.find(THMX_NS + "U-factors") is not None for c in res.findall(THMX_NS + "Case"))


STALE_78 = re.compile(r"\r?\n?<(Results|Mesh)>.*?</\1>", re.S)


def copy_without_results(src, dst):
    """Copy a .thmx, dropping any <Results> and <Mesh> block, so the post-solve check sees only fresh results."""
    with open(src, "rb") as fh:
        text = fh.read().decode("latin-1")
    with open(dst, "wb") as fh:
        fh.write(STALE_78.sub("", text).encode("latin-1"))


def therm_running(image="THERM7.exe"):
    """True when a process with this image name runs (Windows only; False elsewhere)."""
    if os.name != "nt":
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image, "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return image.lower() in out.lower()


def non_ascii(*paths):
    return [p for p in paths if p and not p.isascii()]


def stem_clashes(files):
    seen, clashes = {}, []
    for f in files:
        key = os.path.normcase(os.path.splitext(os.path.basename(f))[0])
        if key in seen:
            clashes.append((seen[key], f))
        seen.setdefault(key, f)
    return clashes


def run_one_78(src, out_dir, exe, password, timeout, log_dir, main_ini, popen=subprocess.Popen):
    """Solve a copy of src in a scratch folder under %TEMP%, then write <out>/<stem>.thmx and .thm."""
    out_dir = os.path.abspath(out_dir or os.path.dirname(src))
    stem = os.path.splitext(os.path.basename(src))[0]
    final = os.path.join(out_dir, stem + ".thmx")
    rec = {"input": src, "solved_file": None, "thm_file": None, "log": None, "status": None, "ok": False,
           "exit_code": None, "log_code": None, "wall_time_s": None, "error": None, "log_text": None,
           "has_results": None}
    if "." in stem:
        # THERM 7.8.80 shows a modal "Invalid Filename ... cannot contain periods" and hangs.
        rec.update(status="error", error="refused before starting THERM, which hangs on a modal dialog when a "
                   "file name has extra periods; rename %s to %s"
                   % (os.path.basename(src), stem.replace(".", "p") + os.path.splitext(src)[1]))
        return rec
    log = os.path.join(os.path.abspath(log_dir) if log_dir else out_dir, stem + ".log")
    scratch_root = tempfile.gettempdir()
    bad = non_ascii(src, final, log, scratch_root)
    if bad:
        # shortcut: non-ASCII paths are untested with THERM 7.8; lift once a run proves them.
        rec.update(status="error", error="refused before starting THERM: non-ASCII characters in %s; "
                   "use a folder whose path is plain ASCII (also check %%TEMP%%)" % ", ".join(bad))
        return rec
    rec["log"] = log
    scratch = None
    try:
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(log):
            os.remove(log)  # THERM appends; a stale "Calculation complete." must not count
        scratch = tempfile.mkdtemp(prefix="therm-run-")
        work = os.path.join(scratch, stem + ".thmx")
        copy_without_results(src, work)
        ini = os.path.join(scratch, "run.ini")
        conrad = write_temp_ini(main_ini, ini)
    except OSError as exc:
        rec.update(status="error", error="cannot prepare scratch copy, temp INI or log: %s" % exc)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        return rec
    try:
        argv = [exe, "-pw", password, "-log", log, "-ini", ini, "-thmx", work, "-calc", "-exit"]
        start = time.monotonic()
        try:
            proc = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=scratch)
        except OSError as exc:
            rec.update(status="error", error="cannot start THERM: %s" % exc)
            return rec
        timed_out = False
        try:
            rec["exit_code"] = proc.wait(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            rec["dialogs"] = window_texts(proc.pid)
            kill_tree(proc)
        rec["wall_time_s"] = round(time.monotonic() - start, 2)
        text = read_log(log)
        rec["log_text"] = text.strip() if text else None
        ok, err, rec["log_code"] = parse_log_78(text)
        code = None if rec["exit_code"] is None else rec["exit_code"] & 0xFFFFFFFF
        rec["has_results"] = thmx_ufactor_case(work)
        if timed_out:
            rec.update(status="timeout", error="THERM still running after %ss and was killed (hung or a modal "
                       "dialog is open)%s" % (timeout, ": " + " / ".join(rec["dialogs"]) if rec["dialogs"] else ""))
        elif code == EXIT_NO_FORTRAN:
            rec.update(status="crashed", error=FORTRAN_FIX)
        elif code != EXIT_NORMAL:
            rec.update(status="crashed", error="THERM crashed with exit code 0x%08X%s" % (
                code, " (log: %s)" % err if text else ""))
        elif ok and not rec["has_results"]:
            rec.update(status="failed", error="log says '%s' but the solved .thmx has no <Results> U-factor case"
                       % SUCCESS)
        elif ok:
            thm = os.path.join(out_dir, stem + ".thm")
            shutil.copy2(work, final)
            if os.path.isfile(os.path.splitext(work)[0] + ".thm"):
                shutil.copy2(os.path.splitext(work)[0] + ".thm", thm)
                rec["thm_file"] = thm
            rec.update(status="solved", ok=True, solved_file=final, error=err if err else None)
        else:
            if text is not None and conrad and SUCCESS not in text:
                err = (err or "") + " (the settings INI has ConradOutput=1, which stops THERM 7.8 solving; set it to 0)"
            rec.update(status="failed", error=err or "log has no '%s'" % SUCCESS)
        return rec
    except OSError as exc:
        rec.update(status="error", ok=False, error="cannot write results to %s: %s" % (out_dir, exc))
        return rec
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def window_texts(pid):
    """Text of every window owned by pid, children included (Windows only; [] elsewhere)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out = []

    def text(h):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        return buf.value

    def top(h, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == pid:
            parts = [text(h)]

            def child(c, _):
                t = text(c)
                if t:
                    parts.append(t)
                return True
            user32.EnumChildWindows(h, proc(child), 0)
            joined = " | ".join(p for p in parts if p)
            if joined:
                out.append(joined)
        return True
    user32.EnumWindows(proc(top), 0)
    return out


def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def run_one(src, target, exe, ver, password, timeout, log_dir, popen=subprocess.Popen):
    rec = {"input": src, "solved_file": target, "log": None, "status": None, "ok": False,
           "exit_code": None, "log_code": None, "wall_time_s": None, "error": None, "log_text": None,
           "result_entries": None}
    stem = os.path.splitext(os.path.basename(target))[0]
    if "." in stem:
        # THERM 8.1.68 shows a modal "Invalid Filename ... cannot contain periods" and hangs.
        rec.update(status="error", error="refused before starting THERM, which hangs on a modal dialog when a "
                   "file name has extra periods; rename %s to %s"
                   % (os.path.basename(target), stem.replace(".", "p") + os.path.splitext(target)[1]))
        return rec
    try:
        if target != src:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
        base = os.path.splitext(os.path.basename(target))[0] + ".log"
        log = os.path.join(os.path.abspath(log_dir), base) if log_dir else os.path.splitext(target)[0] + ".log"
        rec["log"] = log
        if os.path.exists(log):
            os.remove(log)  # THERM appends; a stale "Calculation complete." must not count
    except OSError as exc:
        rec.update(status="error", error="cannot prepare file or log: %s" % exc)
        return rec
    argv = [exe, "-pw", password, FILE_FLAG[ver], target, "-log", log, "-calc", "-exit"]
    start, wall_start = time.monotonic(), time.time()
    try:
        proc = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=os.path.dirname(target))
    except OSError as exc:
        rec.update(status="error", error="cannot start THERM: %s" % exc)
        return rec
    try:
        rec["exit_code"] = proc.wait(timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        dialogs = window_texts(proc.pid)
        kill_tree(proc)
        rec["dialogs"] = dialogs
    rec["wall_time_s"] = round(time.monotonic() - start, 2)
    text = read_log(log)
    rec["log_text"] = text.strip() if text else None
    ok, err, rec["log_code"] = parse_log(text, target)
    if ver == "8.1":
        rec["result_entries"] = result_entries(target)
        if rec["result_entries"] is None and not ok and not timed_out:
            note = "%s is not a valid .thmz zip" % target
            err = "%s (%s)" % (err, note) if err else note
    if timed_out:
        rec.update(status="timeout", ok=False,
                   error="THERM still running after %ss and was killed (hung or a modal dialog is open)%s"
                   % (timeout, ": " + " / ".join(rec["dialogs"]) if rec["dialogs"] else ""))
    elif ok and ver == "8.1" and not any(n in RESULT_ENTRIES for n in (rec["result_entries"] or [])):
        rec.update(status="failed", error="log says '%s' but %s has no SteadyStateResults entry"
                   % (SUCCESS, target))
    elif ok:
        rec.update(status="solved", ok=True, error=err if err else None)
    else:
        rec.update(status="failed", error=err or "log has no '%s'" % SUCCESS)
    return rec


def build_parser():
    p = argparse.ArgumentParser(prog="therm_run.py", description=__doc__.split("\n\n")[0])
    p.add_argument("paths", nargs="+", help=".thmz/.thmx file(s) or folder(s); folders are not recursed")
    p.add_argument("--out-dir", help="8.1: copy each input here and solve the copy (default: solve in place). "
                   "7.8: write <stem>.thmx and <stem>.thm here (default: the input's folder)")
    p.add_argument("--log-dir", help="write logs here (default: next to the solved file)")
    p.add_argument("--exe", help="THERM7.exe or THERM8.exe (default: the standard install path)")
    p.add_argument("--env", help="env.json from therm-setup, used for the exe when --exe is not given")
    p.add_argument("--therm-version", choices=sorted(DEFAULT_EXE), help="7.8 or 8.1 (default: .thmx inputs 7.8, .thmz inputs 8.1, else 7.8 when installed)")
    p.add_argument("--timeout", type=float, default=600, help="seconds per file before THERM is killed (600)")
    p.add_argument("--out", help="write the JSON report here instead of stdout")
    return p


def main(argv=None, popen=subprocess.Popen):
    args = build_parser().parse_args(argv)
    try:
        if args.timeout <= 0:
            raise UserError("--timeout must be > 0")
        password = os.environ.get(PW_ENV)
        if not password:
            raise UserError("THERM_CLI_PW is not set; " + PW_FIX)
        exe, ver, env_ini = resolve_therm(args)
        files = collect_files(args.paths, SUFFIX[ver])
        if ver == "7.8":
            clashes = stem_clashes(files)
            if clashes:
                raise UserError("two inputs share the file stem, so both would be written as the same "
                                "<stem>.thmx/.thm: %s and %s" % clashes[0])
            main_ini = main_ini_78(env_ini)
            if not os.path.isfile(main_ini):
                raise UserError("THERM 7.8 settings INI not found at %s; start THERM 7.8 once, or run "
                                "therm-setup and pass --env env.json" % main_ini)
            if therm_running("THERM7.exe"):
                raise UserError("THERM7.exe is already running; close THERM 7.8 (GUI or another run) first, "
                                "because runs share its Sim folder")
        else:
            targets = plan_targets(files, args.out_dir)
        if args.log_dir:
            os.makedirs(args.log_dir, exist_ok=True)
    except (UserError, OSError) as exc:
        print("%s: error: %s" % (SKILL, exc), file=sys.stderr)
        return 1

    # Sequential on purpose: parallel THERM 8.1.68 runs share the Sim folder and gave
    # non-repeatable meshes on the test host (see HOST-RESULTS.md). THERM 7.8 names its
    # engine files in Sim by file stem, so it is sequential too.
    if ver == "7.8":
        records = [run_one_78(f, args.out_dir, exe, password, args.timeout, args.log_dir, main_ini, popen)
                   for f in files]
    else:
        records = [run_one(src, tgt, exe, ver, password, args.timeout, args.log_dir, popen)
                   for src, tgt in targets]
    for r in records:
        for key in ("log_text", "error"):
            if r.get(key):
                r[key] = r[key].replace(password, MASK)
        if not r["ok"]:
            print("%s: %s %s: %s" % (SKILL, r["status"], r["input"], r["error"]), file=sys.stderr)
    report = {
        "skill": SKILL, "version": VERSION,
        "therm": {"exe": exe, "version": ver},
        "command": ('"%s" -pw %s -log <log> -ini <temp ini> -thmx <scratch copy> -calc -exit' % (exe, MASK)
                    if ver == "7.8" else '"%s" -pw %s %s <file> -log <log> -calc -exit' % (exe, MASK, FILE_FLAG[ver])),
        "timeout_s": args.timeout,
        "summary": {"total": len(records), "solved": sum(r["ok"] for r in records),
                    "failed": sum(not r["ok"] for r in records)},
        "files": records,
    }
    text = json.dumps(report, indent=2)
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        else:
            print(text)
    except OSError as exc:
        print("%s: error: cannot write output: %s" % (SKILL, exc), file=sys.stderr)
        return 1
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
