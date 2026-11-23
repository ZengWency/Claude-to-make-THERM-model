"""Check that this Windows machine can solve LBNL THERM files headless, and write env.json.

Checks, per THERM install found (7.8 and 8.1; 7.8 is the default when installed):
  - THERM8.exe / THERM7.exe (standard folders, registry, or --exe)
  - VC++ 2015-2022 x86 runtime and the 32-bit Intel Fortran runtime DLLs
  - the settings INI and the library files it points to, including the BC library in use
  - THERM_CLI_PW is set (the value is never printed)
  - a smoke solve through therm-run (7.8: a copy of THERM's Samples\\sample-sill.thmx, checked against
    the 7.8.80 values; 8.1: bundled LBNL sample-sill, checked against LBNL's U-factors)
Every failed check carries the exact fix.

Standard library only. Exit codes: 0 every check passed, 1 a check failed (see "fixes"),
2 therm-run output could not be parsed.
"""
import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SKILL = "therm-setup"
VERSION = 1

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
THERM_RUN = os.path.join(SKILLS, "therm-run", "scripts", "therm_run.py")
THERM_RESULTS = os.path.join(SKILLS, "therm-results", "scripts", "therm_results.py")
SMOKE_8 = os.path.join(os.path.dirname(HERE), "assets", "smoke-sample-sill.thmz")
# LBNL's solved sample-sill.thmz (LibraryFEMTherm), U-factor case, W/m2K. THERM 8.1.68
# reproduces these to all printed digits from the bundled copy with results and mesh removed.
SMOKE_EXPECTED = {"Frame": 2.003613, "Edge": 2.34569, "SHGC Exterior": 1.812795}
SMOKE_TOL = 0.01
# THERM 7.8.80 solving a copy of its own Samples\sample-sill.thmx (saved by 7.8.55) with SaveXML=1:
# U-factor case, Projected Y, W/m2K. A .thmx import has no glass-plane projection.
SMOKE_EXPECTED_78 = {"Frame": 2.016345, "Edge": 2.337976, "SHGC Exterior": 1.876081}
SMOKE_TOL_78 = 0.005
SMOKE_BUILD_78 = "7.8.80.0"
THMX_NS = "{http://windows.lbl.gov}"

PROGRAM_DIRS = [r"C:\Program Files (x86)\lbnl", r"C:\Program Files\lbnl"]
EXE_NAME = {"8.1": "THERM8.exe", "7.8": "THERM7.exe"}
DEFAULT_INI = r"C:\Users\Public\LBNL\Settings\therm{ver}.ini"
LBNL_REG = r"SOFTWARE\WOW6432Node\LBNL\THERM"
UNINSTALL_REG = [r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"]
VC_REG = r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x86"
INTEL_DLLS = ["libifcoremd.dll", "libmmd.dll", "svml_dispmd.dll"]
INTEL_DIRS = [r"C:\Program Files (x86)\Common Files\Intel\Shared Libraries\ia32",
              r"C:\Program Files (x86)\Common Files\Intel\Shared Libraries\redist\ia32\compiler"]
# 8.1 reads its XML libraries from [XMLLibraries]; 7.8 (and 8.1's legacy keys) use [THERM] *LibName.
XML_LIB_KEYS = ["BoundaryCondtionsSteadyStateXML", "BoundaryCondtionsTransientXML", "MaterialXML", "GasesXML"]
LEGACY_LIB_KEYS = ["BCLibName", "MaterialLibName", "GasLibName", "UfactorLibName", "AttributeLibName"]

FIX_PW = ("set THERM_CLI_PW with: [Environment]::SetEnvironmentVariable('THERM_CLI_PW', "
          "[System.Net.NetworkCredential]::new('', (Read-Host 'THERM CLI password' -AsSecureString))"
          ".Password, 'User')  then open a new terminal. Get the password from LBNL "
          "(see 'CLI password' in therm-setup's SKILL.md)")
FIX_PW_RESTART = ("THERM_CLI_PW is saved for your user but this terminal started before it was set: "
                  "close and reopen the terminal (or Claude Code)")
FIX_INSTALL = ("install THERM 7.8 (or 8.1) from https://windows.lbl.gov/therm-software-downloads "
               "(default folder), or pass --exe C:\\path\\to\\THERM7.exe")
FIX_VC = ("install the Microsoft VC++ 2015-2022 x86 redistributable: "
          "https://aka.ms/vs/17/release/vc_redist.x86.exe (THERM is 32-bit, so x86 even on 64-bit Windows)")
FIX_INTEL = ("install the 32-bit Intel Fortran runtime 2023.1.0 named on "
             "https://windows.lbl.gov/redistributable-packages, or copy %s into {dir}" % ", ".join(INTEL_DLLS))
FIX_SMOKE_RUN = "therm-run is missing: keep the therm-run skill folder next to therm-setup ({path})"


def winreg_mod():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def reg_values(path, hive="HKLM"):
    """Return {name: value} for a registry key, or {} if absent or not on Windows."""
    wr = winreg_mod()
    if not wr:
        return {}
    root = wr.HKEY_LOCAL_MACHINE if hive == "HKLM" else wr.HKEY_CURRENT_USER
    try:
        with wr.OpenKey(root, path) as key:
            out, i = {}, 0
            while True:
                try:
                    name, value, _ = wr.EnumValue(key, i)
                except OSError:
                    return out
                out[name] = value
                i += 1
    except OSError:
        return {}


def reg_subkeys(path):
    wr = winreg_mod()
    if not wr:
        return []
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, path) as key:
            out, i = [], 0
            while True:
                try:
                    out.append(wr.EnumKey(key, i))
                except OSError:
                    return out
                i += 1
    except OSError:
        return []


def file_version(path):
    """PE file version string (Windows only), e.g. '8.1.68.0'; None if unavailable."""
    if os.name != "nt":
        return None
    import ctypes
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return None
    ptr, n = ctypes.c_void_p(), ctypes.c_uint()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)):
        return None
    ms, ls = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32))[2:4]
    return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def version_of_exe(exe):
    name = os.path.basename(exe).lower()
    for ver, n in EXE_NAME.items():
        if name == n.lower():
            return ver
    return None


def registry_versions():
    """{'8.1': '8.1.68', ...} from the Uninstall entries THERM's installer writes."""
    found = {}
    for base in UNINSTALL_REG:
        for sub in reg_subkeys(base):
            v = reg_values(base + "\\" + sub)
            name = str(v.get("DisplayName", ""))
            if name.upper().startswith("THERM") and v.get("DisplayVersion"):
                found.setdefault(name[5:].strip(), str(v["DisplayVersion"]))
    return found


def find_installs(versions, exe_overrides, program_dirs=PROGRAM_DIRS):
    """Return [{version, exe, source}] for the requested versions, 7.8 first (the default)."""
    out = []
    for exe in exe_overrides:
        ver = version_of_exe(exe)
        out.append({"version": ver, "exe": os.path.abspath(exe), "source": "--exe"})
    have = {i["version"] for i in out}
    for ver in versions:
        if ver in have:
            continue
        hits = [p for d in program_dirs for p in glob.glob(os.path.join(d, "THERM" + ver, EXE_NAME[ver]))]
        if hits:
            out.append({"version": ver, "exe": hits[0], "source": "default folder"})
    return sorted(out, key=lambda i: {"7.8": 0, "8.1": 1}.get(i["version"], 2))


def parse_ini(path):
    """Return {section: {key: value}} keeping the first occurrence, as GetPrivateProfileString does."""
    sections, cur = {}, None
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                cur = sections.setdefault(line[1:-1], {})
            elif cur is not None and "=" in line and not line.startswith(";"):
                k, v = line.split("=", 1)
                cur.setdefault(k.strip(), v.strip())
    return sections


def ini_path_for(ver, override=None):
    if override:
        return override, "--ini"
    reg = reg_values(LBNL_REG + "\\" + ver)
    if reg.get("INIPath") and reg.get("INIFileName"):
        return os.path.join(reg["INIPath"], reg["INIFileName"]), "registry"
    return DEFAULT_INI.format(ver=ver), "default path"


def check_ini(ver, path, source):
    info = {"path": path, "source": source, "exists": os.path.isfile(path)}
    if not info["exists"]:
        return info, []
    ini = parse_ini(path)
    therm = ini.get("THERM", {})
    info["doc_path"] = therm.get("DocPath")
    info["sim_path"] = therm.get("SimPath")
    libs, warnings = {}, []
    keys = [("XMLLibraries", k) for k in XML_LIB_KEYS] if ver == "8.1" else []
    keys += [("THERM", k) for k in LEGACY_LIB_KEYS]
    for section, key in keys:
        p = ini.get(section, {}).get(key)
        if p:
            libs[key] = {"path": p, "exists": os.path.isfile(p), "section": section}
    info["libraries"] = libs
    bc_key = "BoundaryCondtionsSteadyStateXML" if ver == "8.1" and "BoundaryCondtionsSteadyStateXML" in libs else "BCLibName"
    if bc_key in libs:
        info["bc_library"] = {"key": bc_key, "file": os.path.basename(libs[bc_key]["path"]),
                              "path": libs[bc_key]["path"], "exists": libs[bc_key]["exists"]}
    if ver == "7.8" and therm.get("ConradOutput", "0").strip() == "1":
        warnings.append("THERM 7.8 settings INI %s has ConradOutput=1, which stops headless solves "
                        "(no 'Calculation complete.'); set it to 0" % path)
    for key, lib in libs.items():
        legacy_on_8 = ver == "8.1" and lib["section"] == "THERM"
        if not lib["exists"] and not legacy_on_8:
            warnings.append("THERM %s library %s=%s does not exist" % (ver, key, lib["path"]))
    return info, warnings


def check_vc():
    reg = reg_values(VC_REG)
    if reg.get("Installed") == 1:
        return True, "VC++ x86 runtime %s (registry)" % reg.get("Version", "?")
    sysdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "SysWOW64")
    dlls = [os.path.join(sysdir, d) for d in ("vcruntime140.dll", "msvcp140.dll")]
    if all(os.path.isfile(d) for d in dlls):
        return True, "vcruntime140.dll and msvcp140.dll in %s" % sysdir
    return False, "no VC++ 2015-2022 x86 runtime found"


def check_intel(install_dir, path_dirs=None, extra_dirs=INTEL_DIRS):
    """Return (ok, detail, missing): each DLL must be in the install dir, an Intel dir or PATH."""
    path_dirs = path_dirs if path_dirs is not None else os.environ.get("PATH", "").split(os.pathsep)
    where, missing = {}, []
    for dll in INTEL_DLLS:
        hit = next((d for d in [install_dir] + list(extra_dirs) + path_dirs
                    if d and os.path.isfile(os.path.join(d, dll))), None)
        if hit:
            where[dll] = hit
        else:
            missing.append(dll)
    if missing:
        return False, "missing %s" % ", ".join(missing), missing
    return True, "; ".join("%s in %s" % kv for kv in where.items()), []


def check_password(environ=None, user_env=None):
    """Return (ok, source, fix). Never returns or prints the value."""
    environ = os.environ if environ is None else environ
    if environ.get("THERM_CLI_PW"):
        return True, "process environment", None
    user_env = reg_values("Environment", hive="HKCU") if user_env is None else user_env
    if user_env.get("THERM_CLI_PW"):
        return False, "user environment only (not in this process)", FIX_PW_RESTART
    return False, "not set", FIX_PW


def thmx_projected_y(path):
    """{tag: U} for the U-factor case, Projected Y, from a solved THERM 7.8 .thmx."""
    root = ET.parse(path).getroot()
    out = {}
    for case in root.findall(THMX_NS + "Results/" + THMX_NS + "Case"):
        if (case.findtext(THMX_NS + "ModelType") or "").strip() != "U-factor":
            continue
        for uf in case.findall(THMX_NS + "U-factors"):
            for pr in uf.findall(THMX_NS + "Projection"):
                if (pr.findtext(THMX_NS + "Length-type") or "").strip().lower() == "projected y":
                    u = pr.find(THMX_NS + "U-factor")
                    try:
                        out[(uf.findtext(THMX_NS + "Tag") or "").strip()] = float(u.get("value"))
                    except (AttributeError, TypeError, ValueError):
                        pass
    return out


def compare_78(got, file_version):
    """Return (ok, error, warning) for the 7.8 smoke values against the 7.8.80 reference."""
    bad = [k for k, v in SMOKE_EXPECTED_78.items() if k not in got or abs(got[k] - v) / v > SMOKE_TOL_78]
    if not bad:
        return True, None, None
    msg = ("smoke U-factors (Projected Y) differ from THERM 7.8.80's by more than 0.5%%: %s (got %s, expected %s)"
           % (", ".join(bad), got, SMOKE_EXPECTED_78))
    if file_version != SMOKE_BUILD_78 and all(k in got for k in SMOKE_EXPECTED_78):
        return True, None, msg + "; this exe is build %s, not %s, so this is a warning" % (file_version, SMOKE_BUILD_78)
    return False, msg, None


def smoke_solve(install, timeout, run_script=THERM_RUN, results_script=THERM_RESULTS):
    """Solve a sample through therm-run in a temp folder. Returns a dict with ok/error/fix."""
    ver = install["version"]
    if ver == "8.1":
        sample = SMOKE_8
    else:
        doc = (install.get("ini") or {}).get("doc_path") or r"C:\Users\Public\LBNL\THERM7.8"
        sample = os.path.join(doc, "Samples", "sample-sill.thmx")
    res = {"sample": sample, "ok": False}
    if not os.path.isfile(run_script):
        res.update(error="therm-run not found", fix=FIX_SMOKE_RUN.format(path=run_script))
        return res
    if not os.path.isfile(sample):
        res.update(error="smoke sample not found: %s" % sample,
                   fix="restore %s (reinstall THERM %s or re-copy this skill)" % (sample, ver))
        return res
    tmp = tempfile.mkdtemp(prefix="therm-setup-")
    try:
        work = os.path.join(tmp, "smoke" + os.path.splitext(sample)[1])
        shutil.copy2(sample, work)
        report = os.path.join(tmp, "run.json")
        cmd = [sys.executable, run_script, work, "--exe", install["exe"], "--therm-version", ver,
               "--timeout", str(timeout), "--out", report]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 1 or not os.path.isfile(report):
            res.update(error="therm-run refused to start: %s" % proc.stderr.strip(),
                       fix="fix the therm-run error above, then rerun therm-setup")
            return res
        with open(report, encoding="utf-8") as fh:
            rec = json.load(fh)["files"][0]
        res.update(status=rec["status"], wall_time_s=rec["wall_time_s"], exit_code=rec["exit_code"],
                   log_text=rec.get("log_text"))
        if not rec["ok"]:
            fix = ("THERM_CLI_PW is probably wrong: set it again with the password from LBNL "
                   "(see 'CLI password' in therm-setup's SKILL.md)"
                   if rec.get("log_text") is None and rec["status"] == "failed"
                   else "open %s in the THERM GUI to see the error, or rerun with a larger --timeout" % sample)
            res.update(error=rec.get("error"), fix=fix)
            return res
        if ver == "7.8":
            solved = rec.get("solved_file") or work
            got = thmx_projected_y(solved)
            res["u_factors"] = got
            ok, error, warning = compare_78(got, install.get("file_version"))
            if warning:
                res["warning"] = warning
            if not ok:
                res.update(error=error, fix="THERM solved but gave different numbers; check the THERM build and "
                           "its settings INI")
                return res
            res["matches_reference"] = warning is None
        if ver == "8.1" and os.path.isfile(results_script):
            out = subprocess.run([sys.executable, results_script, work], capture_output=True, text=True)
            got = {}
            if out.returncode == 0:
                case = next(c for c in json.loads(out.stdout)["cases"] if c["case"] == "U-factor")
                got = {t["tag"]: t["displayed"]["u_factor"] for t in case["tags"]}
            res["u_factors"] = got
            bad = [k for k, v in SMOKE_EXPECTED.items()
                   if k not in got or abs(got[k] - v) / v > SMOKE_TOL]
            if bad:
                res.update(error="smoke U-factors differ from LBNL's by more than 1%%: %s (got %s, expected %s)"
                           % (", ".join(bad), got, SMOKE_EXPECTED),
                           fix="THERM solved but gave different numbers; check the THERM build and its settings INI")
                return res
            res["matches_lbnl"] = True
        res["ok"] = True
        return res
    except (OSError, ValueError, KeyError, StopIteration, ET.ParseError) as exc:
        res.update(error="cannot read therm-run output: %s" % exc, parse_error=True,
                   fix="rerun therm-run by hand on the sample to see its output")
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_parser():
    p = argparse.ArgumentParser(prog="therm_setup.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--therm-version", choices=["8.1", "7.8", "all"], default="all",
                   help="which THERM to check (default all found; 7.8 is the default install when present)")
    p.add_argument("--exe", action="append", default=[], help="THERM8.exe/THERM7.exe path, repeatable")
    p.add_argument("--ini", help="settings INI to use (only with one --therm-version)")
    p.add_argument("--skip-smoke", action="store_true", help="do not run the smoke solve")
    p.add_argument("--timeout", type=float, default=120, help="smoke solve timeout in seconds (120)")
    p.add_argument("--out", help="also write the JSON to this file (for example env.json)")
    return p


def run_checks(args):
    versions = ["8.1", "7.8"] if args.therm_version == "all" else [args.therm_version]
    checks, warnings = [], []

    def check(name, ok, detail, fix=None):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": None if ok else fix})

    if os.name != "nt":
        warnings.append("not running on Windows (%s); THERM runs on Windows only" % platform.system())
    pw_ok, pw_source, pw_fix = check_password()
    check("THERM_CLI_PW", pw_ok, pw_source, pw_fix)
    vc_ok, vc_detail = check_vc()

    installs = find_installs(versions, args.exe)
    reg_ver = registry_versions()
    for inst in installs:
        if inst["version"] is None:
            check("install %s" % inst["exe"], False, "not THERM8.exe or THERM7.exe",
                  "pass --exe pointing at THERM8.exe or THERM7.exe")
    installs = [i for i in installs if i["version"]]
    for want in ("8.1", "7.8"):
        if args.therm_version == want and not any(i["version"] == want for i in installs):
            check("THERM %s install" % want, False, "%s not found" % EXE_NAME[want], FIX_INSTALL)
    if not installs:
        check("THERM install", False, "no THERM8.exe or THERM7.exe found in %s" % " or ".join(PROGRAM_DIRS),
              FIX_INSTALL)
    for inst in installs:
        ver = inst["version"]
        exe_ok = os.path.isfile(inst["exe"])
        inst["install_dir"] = os.path.dirname(inst["exe"])
        inst["file_version"] = file_version(inst["exe"]) if exe_ok else None
        inst["registry_version"] = reg_ver.get(ver)
        check("THERM %s exe" % ver, exe_ok, inst["exe"], "THERM %s is not at %s; %s" % (ver, inst["exe"], FIX_INSTALL))
        if not exe_ok:
            continue
        check("THERM %s VC++ x86 runtime" % ver, vc_ok, vc_detail, FIX_VC)
        intel_ok, intel_detail, _ = check_intel(inst["install_dir"])
        check("THERM %s Intel Fortran runtime" % ver, intel_ok, intel_detail,
              FIX_INTEL.format(dir=inst["install_dir"]))
        ini_path, ini_src = ini_path_for(ver, args.ini if args.therm_version != "all" else None)
        inst["ini"], ini_warn = check_ini(ver, ini_path, ini_src)
        warnings.extend(ini_warn)
        if not inst["ini"]["exists"]:
            warnings.append("THERM %s settings INI %s not found; THERM writes it on first start" % (ver, ini_path))
        if args.skip_smoke:
            inst["smoke"] = {"ok": None, "skipped": True}
        elif not (pw_ok and vc_ok and intel_ok):
            inst["smoke"] = {"ok": None, "skipped": True, "reason": "fix the failed checks first"}
        else:
            inst["smoke"] = smoke_solve(inst, args.timeout)
            check("THERM %s smoke solve" % ver, inst["smoke"]["ok"],
                  inst["smoke"].get("error") or "solved %s in %ss" % (
                      os.path.basename(inst["smoke"]["sample"]), inst["smoke"].get("wall_time_s")),
                  inst["smoke"].get("fix"))
            if inst["smoke"].get("warning"):
                warnings.append("THERM %s smoke: %s" % (ver, inst["smoke"]["warning"]))
            if not inst["ini"]["exists"]:  # the smoke run may have created it
                inst["ini"], _ = check_ini(ver, ini_path, ini_src)
    ok = all(c["ok"] for c in checks)
    usable = [i for i in installs if (i.get("smoke") or {}).get("ok")] or [i for i in installs if os.path.isfile(i["exe"])]
    env = {
        "skill": SKILL, "version": VERSION, "ok": ok,
        "platform": platform.platform(), "python": platform.python_version(),
        "default": {"version": usable[0]["version"], "exe": usable[0]["exe"]} if usable else None,
        "cli_password": {"set": pw_ok, "source": pw_source},
        "installs": installs,
        "checks": checks,
        "fixes": [c["fix"] for c in checks if not c["ok"] and c["fix"]],
        "warnings": warnings,
    }
    parse_error = any((i.get("smoke") or {}).get("parse_error") for i in installs)
    return env, parse_error


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.ini and args.therm_version == "all":
        print("%s: error: --ini needs --therm-version 8.1 or 7.8" % SKILL, file=sys.stderr)
        return 1
    env, parse_error = run_checks(args)
    for w in env["warnings"]:
        print("%s: warning: %s" % (SKILL, w), file=sys.stderr)
    for c in env["checks"]:
        if not c["ok"]:
            print("%s: FAIL %s: %s\n  fix: %s" % (SKILL, c["name"], c["detail"], c["fix"]), file=sys.stderr)
    text = json.dumps(env, indent=2)
    print(text)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as exc:
            print("%s: error: cannot write %s: %s" % (SKILL, args.out, exc), file=sys.stderr)
            return 1
    if parse_error:
        return 2
    return 0 if env["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
