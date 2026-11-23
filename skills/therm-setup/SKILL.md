---
name: therm-setup
description: Checks that an engineer's own Windows machine can solve LBNL THERM files headless, and writes env.json for the other THERM skills. It finds THERM 7.8 and 8.1 installs (7.8 is the default when installed) (standard folders, registry, or --exe), checks the VC++ x86 and Intel Fortran runtimes, the settings INI and the library files it points to (including the boundary-condition library in use), and whether THERM_CLI_PW is set without printing it. Then it smoke-solves a sample through therm-run (7.8: a copy of THERM's own Samples\sample-sill.thmx, checked against the 7.8.80 values; 8.1: a bundled LBNL sample, checked against LBNL's U-factors). Every failed check names the exact fix. Use first on a new machine, or when asked to "set up THERM", "check my THERM install", "why won't THERM run headless", "is the THERM password set", or "which THERM libraries are in use". It does not install THERM or change any setting.
---

# therm-setup

Read-only check of the local machine. It needs Windows, Python 3.10+ (standard library only) and the therm-run skill folder next to this one for the smoke solve. Nothing goes to the cloud or a remote host.

## Contract

- **Reads:** the local THERM install, its settings INI and libraries, and `THERM_CLI_PW`. No input files.
- **Writes:** `env.json` (with `--out`), the same JSON on stdout. Nothing else is changed: the smoke solves a copy in a temp folder, and THERM 7.8's settings INI is left byte-identical.
- **Calls:** therm-run's CLI only (`therm_run.py <copy> --exe <exe> --therm-version <ver> --timeout <s> --out <json>`), and therm-results' CLI for the 8.1 smoke. It imports no other skill's code.
- **Before:** none. Run it first on a new machine.
- **After:** therm-library, therm-glazing or therm-model to make a section; therm-run reads `env.json` with `--env` and picks the 7.8 install from it when no version is given.

## Command

```
python scripts\therm_setup.py [--therm-version 8.1|7.8|all] [--exe PATH ...] [--ini PATH] [--skip-smoke] [--timeout S] [--out env.json]
```

- `--therm-version`: default `all`, which checks every install found. With `8.1` or `7.8`, a missing install is a failed check.
- `--exe`: a THERM8.exe or THERM7.exe in a non-standard place (repeatable). The version comes from the file name.
- `--ini`: a settings INI to use instead of the registry's. It needs a single `--therm-version`.
- `--skip-smoke`: check files only.

## What it checks

| Check | How | Fix it names when it fails |
|---|---|---|
| THERM install | `THERM8.1\THERM8.exe` and `THERM7.8\THERM7.exe` under `C:\Program Files (x86)\lbnl` (or `C:\Program Files\lbnl`), or `--exe`. Reports the exe's file version and the installer's registry version. | Download link, or `--exe` |
| VC++ 2015-2022 x86 runtime | `HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x86`, else `vcruntime140.dll` and `msvcp140.dll` in SysWOW64. THERM is 32-bit. | `vc_redist.x86.exe` link |
| Intel Fortran runtime (32-bit) | `libifcoremd.dll`, `libmmd.dll`, `svml_dispmd.dll` in the THERM folder, Intel's shared `ia32` folder or PATH. Without them THERM exits 0xC0000135 at start. | LBNL redistributables page, or copy the DLLs into the THERM folder |
| Settings INI | `HKLM\SOFTWARE\WOW6432Node\LBNL\THERM\<ver>` `INIPath` + `INIFileName`, else `C:\Users\Public\LBNL\Settings\therm<ver>.ini`. Reports DocPath, SimPath and each library path with whether it exists. A missing library is a warning. | none (THERM writes the INI on first start) |
| BC library in use | 8.1: `[XMLLibraries] BoundaryCondtionsSteadyStateXML` (LBNL's spelling). 7.8: `[THERM] BCLibName`. | none |
| 7.8 `ConradOutput` | `[THERM] ConradOutput=1` stops 7.8 headless solves (no `Calculation complete.`). | warning: set it to 0 |
| `THERM_CLI_PW` | Set in this process? If not, saved under `HKCU\Environment` but missing here (terminal opened before it was set)? The value is never printed. | The one-liner below, or "reopen the terminal" |
| Smoke solve | therm-run on a temp copy of the install's `Samples\sample-sill.thmx` (7.8) or `assets\smoke-sample-sill.thmz` (8.1). 7.8: the solved `.thmx` must give, U-factor case, Projected Y, Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m2K within 0.5 % (THERM 7.8.80's values). On an exe whose file version is not 7.8.80.0, a mismatch is a warning, not a failure. 8.1: therm-results must give Frame 2.003613, Edge 2.34569 and SHGC Exterior 1.812795 W/m2K within 1 %. No log at all means a wrong password. | Named per failure |

The smoke runs only when the password and runtime checks pass.

## Output (env.json)

JSON on stdout and, with `--out`, in that file. Keys: `skill`, `version` (1), `ok`, `platform`, `python`, `default` (`version` and `exe` of the first install whose smoke passed, 7.8 first; else the first install found), `cli_password` (`set`, `source`), `installs` (per install: `version`, `exe`, `source`, `install_dir`, `file_version`, `registry_version`, `ini` with `libraries` and `bc_library`, `smoke` with `status`, `wall_time_s`, `u_factors`, and `matches_reference` (7.8, plus `warning` on another build) or `matches_lbnl` (8.1)), `checks` (`name`, `ok`, `detail`, `fix`), `fixes` (the fix of every failed check) and `warnings`.

therm-run reads it with `--env env.json`.

Each failed check also prints `FAIL <check>: <detail>` and `fix: <fix>` to stderr. Exit codes: 0 every check passed, 1 a check failed, 2 therm-run's output could not be read.

## CLI password

THERM's command-line mode needs LBNL's password. LBNL's registration form is dead (HTTP 404, 19 Nov 2026). Robin Mitchell, LBNL's THERM developer, posted the password for all users in the forum thread [THERM Command line password](https://groups.google.com/g/LBNL-THERM/c/gvjfCHY7Cz8) in January 2025. Take it from there, or ask in the [LBNL-THERM forum](https://groups.google.com/g/LBNL-THERM). Don't copy one out of third-party code.

Save it once as a user environment variable. This PowerShell one-liner prompts without echoing, so the password never lands in your history:

```powershell
[Environment]::SetEnvironmentVariable('THERM_CLI_PW', [System.Net.NetworkCredential]::new('', (Read-Host 'THERM CLI password' -AsSecureString)).Password, 'User')
```

Open a new terminal or Claude Code session afterwards. The value sits in plain text under `HKCU\Environment`, readable by your own account. While THERM runs it is also visible in the process list (see therm-run).

## Example

```
python scripts\therm_setup.py --out env.json
python ..\therm-run\scripts\therm_run.py C:\projects\sections --env env.json --out-dir C:\projects\solved
```

In testing (Windows Server 2022, THERM 8.1.68 BETA and 7.8.80) every check passes and `default` is 7.8. The 7.8 smoke solves in 0.6 to 0.7 s and returns the three 7.8.80 values exactly; the 8.1 smoke solves in 1.3 s and returns LBNL's exactly (`../therm-run/HOST-RESULTS-78.md`).

## Limits

- Windows only. On another OS it reports the failed checks and a warning.
- It does not install or repair anything; it tells you what to do.
- The 7.8 reference comes from a `.thmx` import, which has no glass-plane projection and no Condensation Resistance case, so the check uses Projected Y.
- `assets\smoke-sample-sill.thmz` is LBNL's `sample-sill.thmz` from LibraryFEMTherm with the results and mesh entries removed (BSD licence, `assets\LICENSE-LibraryFEMTherm.txt`).

Tests: run `python -m unittest discover -s tests` from this folder. They use temp files and fake scripts, and run on Windows or Linux.
