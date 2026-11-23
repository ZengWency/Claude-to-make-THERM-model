---
name: therm-run
description: Solves LBNL THERM 7.8 .thmx files (default) or THERM 8.1 .thmz files headless on the engineer's own Windows machine. For 7.8 it solves a copy in a temp folder with `THERM7.exe -pw %THERM_CLI_PW% -log <log> -ini <temp ini> -thmx <copy> -calc -exit` and writes the solved `<stem>.thmx` and `<stem>.thm`; for 8.1 it runs `THERM8.exe -pw %THERM_CLI_PW% -thmz <file> -log <log> -calc -exit`. One file at a time, under a timeout. It decides pass or fail from THERM's log, not the exit code, and kills a THERM that hangs on a modal dialog. JSON per file gives status, THERM's error text, exit code, wall time, log path and solved file. Use after therm-model or therm-glazing, or when asked to "solve this thmz", "run THERM", "calculate these sections", "batch solve a folder of THERM files", or "THERM -calc headless". It does not build models or read U-factors; use therm-results on the solved file.
---

# therm-run

Runs THERM's command-line mode on files you already have. It needs Windows, a THERM install (check with therm-setup), Python 3.10+ (standard library only) and the CLI password in `THERM_CLI_PW`. Nothing leaves the machine: no cloud, no remote host.

## Contract

- **Reads:** `.thmx` files or folders (7.8) or `.thmz` (8.1); optional `env.json` from therm-setup (`--env`). 7.8 also reads its settings INI (env.json's, else the registry's) and never writes it.
- **Writes:** 7.8: `<out>/<stem>.thmx` (the solved model with `<Results>` and the mesh) and `<out>/<stem>.thm`, only after a good solve. 8.1: the solved `.thmz` (a copy in `--out-dir`, or the input itself without it). Both: one `.log` per file, and a run JSON on stdout or `--out`.
- **Version:** `--therm-version` wins, then the `--exe` name, then the inputs' extension (`.thmx` 7.8, `.thmz` 8.1), else 7.8 when it is installed, else 8.1.
- **Stable CLI for therm-setup:** `therm_run.py <file> --exe <exe> --therm-version <ver> --timeout <s> --out <json>`.
- **Before:** therm-model, or therm-glazing on a GUI-saved `.thmx` / `.thmz` (or therm-setup on a new machine).
- **After:** therm-results on each solved `.thmx` or `.thmz`.

## Input

- One or more `.thmx` (7.8) or `.thmz` (8.1) files or folders. Folders are not recursed and are searched for the chosen version's extension; files run in name order.
- `--out-dir DIR`: 7.8: where `<stem>.thmx` and `<stem>.thm` go (default: the input's folder, so the input is replaced by the solved model only after a good solve). Two inputs with the same file stem are refused. 8.1: copy each input into DIR and solve the copy. Without it THERM writes the results **into the input file**, which is THERM's own behaviour. Two inputs with the same name are refused.
- `--log-dir DIR`: where logs go. Default: `<solved file>.log` next to the solved file. An old log of the same name is deleted first, because THERM appends.
- `--exe PATH`, or `--env env.json` from therm-setup (its 7.8 install first). Default: `C:\Program Files (x86)\lbnl\THERM7.8\THERM7.exe` when it exists, else `...\THERM8.1\THERM8.exe`.
- `--timeout S`: seconds per file before THERM is killed (default 600). The samples used in testing solve in 0.2 to 1.5 s.

## How a THERM 7.8 run works

1. Refuse before launch: a file name with extra periods, a non-ASCII character in the input, output, log or `%TEMP%` path, two inputs with the same stem, no settings INI, or a `THERM7.exe` already running (GUI or another run: they share THERM's `Sim` folder).
2. Copy the input into a new scratch folder under `%TEMP%`, dropping any old `<Results>` and `<Mesh>` block so only fresh results count.
3. Copy the settings INI into the scratch folder with `SaveXML=1` and `ExportMeshToTHMX=1`, and pass it with `-ini`. THERM writes its INI changes to that copy; the main INI stays byte-identical.
4. Run THERM on the copy. THERM rewrites the copy with results and writes `<stem>.thm` beside it.
5. Copy both to the output folder only when the run passed, then delete the scratch folder.

## Output

JSON on stdout, or in the file given by `--out`. Top-level keys: `skill`, `version` (1), `therm` (`exe`, `version`), `command` (with the password masked), `timeout_s`, `summary` (`total`, `solved`, `failed`) and `files`. Each file has:

- `status`: `solved`, `failed` (THERM ran and did not solve), `timeout` (killed) or `error` (not started, for example a bad file name).
- `ok`, `error` (THERM's log message, or why the run was refused or killed), `log_text`, `log_code` (the number THERM writes as the log's last line: 0 after a good solve, 2 after "Calculation not successful").
- `exit_code`, `wall_time_s`, `log`, `input`, `solved_file`.
- 8.1: `result_entries`, the results entries found in the solved `.thmz`. A log that says `Calculation complete.` without a `SteadyStateResults` entry counts as failed.
- 7.8: `thm_file` (the `.thm` written), `has_results` (the solved copy has a `<Results>` U-factor case). `solved_file` is the `.thmx` in the output folder, or null when the run failed.
- `status` can also be `crashed` (7.8): THERM exited with a code other than 0xFFFFFFFF, for example 0xC0000374 (heap corruption from a malformed `.thmx`). Exit 0xC0000135 means a DLL is missing; the error names LBNL's redistributables page, https://windows.lbl.gov/redistributable-packages.
- `timeout` only: `dialogs`, the text of every window THERM had open when it was killed.

8.1: a file is `solved` only when the log contains `Calculation complete.` and its last line is 0. The exit code is not used. 7.8: `solved` needs all three of `Calculation complete.`, a last log line of 0, and a `<Results>` U-factor case in the solved copy, plus exit code 0xFFFFFFFF. THERM 8.1.68 and 7.8.80 both exit 0xFFFFFFFF (4294967295) after a good solve and after a failed one. A 7.8 log may start with `Error when creating XML export file` lines before its timestamp; they are added to `error`.

Errors and per-file failures go to stderr. Exit codes: 0 every file solved. 1 nothing was run: `THERM_CLI_PW` is not set (the message gives the one-liner that sets it), THERM is not found, a path is missing, `--out-dir` names clash, or (7.8) two inputs share a stem, the settings INI is missing or `THERM7.exe` is already running. 2 at least one file failed, timed out or was refused; the JSON still lists every file.

## Example

```
python scripts\therm_run.py C:\projects\facade\sections --out-dir C:\projects\facade\solved --out run.json
python ..\therm-results\scripts\therm_results.py C:\projects\facade\solved\sill.thmx --out sill.json
```

In testing, THERM 7.8.80 solves a copy of its `Samples\sample-sill.thmx` in 0.6 s with Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m2K (Projected Y), and six edited copies match the probe's values to every printed digit (`HOST-RESULTS-78.md`). THERM 8.1.68 solves LBNL's sample-sill with its results and mesh removed in 1.2 s, giving LBNL's Frame 2.003613, Edge 2.34569 and SHGC Exterior 1.812795 (`HOST-RESULTS.md`).

## Where to run it

Run it from your own logged-on terminal or from Claude Code. No desktop interaction is needed: a good `-calc -exit` run needs no clicks and exits by itself. In testing it solved as SYSTEM in session 0, with no logged-on user, so a scheduled task or service account also works. A normal user can write THERM's working folders: `C:\Users\Public\LBNL\THERM7.8\Sim` (7.8), `C:\Users\Public\LBNL\THERM8.1\Sim` (8.1) and the settings INI grant Modify to INTERACTIVE users.

## Password handling

- The password is read only from `THERM_CLI_PW` (see therm-setup for how to set it). It is passed to THERM as an argument list with no shell, so it is never echoed, and it is replaced by `***` in `command`, `log_text`, `error` and stderr.
- **Trade-off:** THERM takes the password only as `-pw <value>` on its command line. While THERM runs (about a second per file), any process on the machine that can list command lines (Task Manager details, `Get-CimInstance Win32_Process`, EDR or process-audit logs) can see it. On a single-user workstation this is acceptable for LBNL's free, published licence key. Don't run it on a shared machine whose process auditing ships command lines to others.

## Limits

- **Sequential only.** THERM 7.8 names its engine files in its shared `Sim` folder by file stem, and therm-run refuses to start while `THERM7.exe` runs. Parallel 8.1 runs were tested: 8 copies of the same model solved at once all reported success, but 3 of 24 got a different mesh (427 or 428 nodes instead of 425, Frame U off by up to 0.002 %). Sequential runs repeated exactly. All THERM 8.1 processes share `C:\Users\Public\LBNL\THERM8.1\Sim`, which is the likely cause. There is no `--jobs` option for this reason.
- **No periods in file names.** THERM 8.1.68 and 7.8.80 show a modal `Invalid Filename ... cannot contain periods` and hang on a name such as `gap18.51.thmz`. therm-run refuses such a file without starting THERM and suggests a new name (`gap18p51.thmz`). Periods and spaces in folder names are fine.
- **Wrong password:** THERM exits at once and writes no log. The error says so.
- **THERM 7.8 `.thmx` limits:** an imported `.thmx` loses the glazing-system identity, so there is no Condensation Resistance case and "Projected in glass plane" falls back to Projected X. Use Projected Y for a sill.
- **Non-ASCII paths** are refused for 7.8 until a run proves they work.
- Relative paths are made absolute before the call, because THERM resolves them against its own DocPath.
- The timeout is the only hang detector. A model that THERM rejects with a modal dialog waits until the timeout, then is killed and reported with the dialog text.

Tests: run `python -m unittest discover -s tests` from this folder. They use a fake THERM process and run on Windows or Linux.
