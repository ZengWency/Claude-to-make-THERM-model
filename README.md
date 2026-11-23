# THERM skills for Claude Code

These skills let Claude Code build, solve and read LBNL THERM frame sections on your own Windows PC. The default is **THERM 7.8.80**, the version NFRC certification uses. THERM 8.1 also works (see "THERM 8.1 (optional)" below). THERM runs in the background with no clicks, and you open THERM only to review a solved file. Nothing is sent to the cloud or another computer.

For a plain-English overview, read `EXPLAINER.md` first.

## What the skills do

| Skill | What it does |
|---|---|
| therm-setup | Checks your PC can run THERM headless and writes `env.json`. Run it first. |
| therm-library | Lists THERM materials and boundary conditions, and makes a project library file (NFRC and ISO 10077-2 presets included). |
| therm-model | Builds a `.thmx` section from a JSON spec, shows what is in a `.thmx`, or edits one (a record value, a material, a BC, tags, a move). |
| therm-glazing | Turns the glazing (IGU) into equivalent solids, or fills a spec region with glass and gap solids or a calibration panel. |
| therm-run | Solves `.thmx` files with THERM, one at a time, and reports which solved. |
| therm-results | Reads U-factors and tag temperatures from a solved `.thmx` into JSON or CSV. |
| therm-frame-calc | Turns results into ISO 10077-2 Uf and psi, or an NFRC frame and edge table. |

How they chain:

```
therm-setup ─► therm-library ─► therm-glazing ─► therm-model ─► therm-run ─► therm-results ─► therm-frame-calc
  (once)        (optional)       (optional)                                                     (optional)
```

therm-glazing works on a section spec before therm-model builds it, or on a `.thmx` that already has an IGU (for example one saved from the THERM GUI). Each skill's `SKILL.md` has a **Contract** section with the files it reads and writes.

## What you need

- **Windows 10 or 11.**
- **THERM 7.8.80** from LBNL's [THERM downloads page](https://windows.lbl.gov/therm-software-downloads) (`THERM7_8_80_SetupFull.exe`). Run the installer and accept the defaults. It puts THERM in `C:\Program Files (x86)\lbnl\THERM7.8` and its samples and libraries in `C:\Users\Public\LBNL\THERM7.8`.
- **The runtimes THERM needs.** THERM is a 32-bit program. It needs the Microsoft VC++ 2015-2022 x86 runtime ([vc_redist.x86.exe](https://aka.ms/vs/17/release/vc_redist.x86.exe)) and the 32-bit Intel Fortran runtime from LBNL's [redistributables page](https://windows.lbl.gov/redistributable-packages). Without the Fortran runtime THERM stops at once with code 0xC0000135. You don't need to guess: therm-setup checks both and names the one to install.
- **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/windows/). Tick "Add python.exe to PATH" in the installer. No extra Python packages are needed.
- **Claude Code.**

## Set the THERM command-line password

THERM only runs without its window when you give it LBNL's command-line password. LBNL's registration form is broken (it returned HTTP 404 on 19 Nov 2026). Robin Mitchell, LBNL's THERM developer, posted the password for all users in January 2025 in the forum thread [THERM Command line password](https://groups.google.com/g/LBNL-THERM/c/gvjfCHY7Cz8). Take it from there, or ask in the [LBNL-THERM forum](https://groups.google.com/g/LBNL-THERM). Don't copy a password from other people's code. The same password works for 7.8 and 8.1.

Save it once. Open PowerShell and paste this line. It asks for the password and does not show what you type:

```powershell
[Environment]::SetEnvironmentVariable('THERM_CLI_PW', [System.Net.NetworkCredential]::new('', (Read-Host 'THERM CLI password' -AsSecureString)).Password, 'User')
```

Close and reopen PowerShell and Claude Code. To check it is set (prints `True`, not the password):

```powershell
[bool][Environment]::GetEnvironmentVariable('THERM_CLI_PW', 'User')
```

The password is stored in your Windows user settings. While THERM runs, other programs on your PC can see it in the process list, so don't use these skills on a shared machine.

## Install into Claude Code

Copy the seven folders inside `skills\` to one of these places:

- **One project only:** `<your project folder>\.claude\skills\`
- **All your projects:** `%USERPROFILE%\.claude\skills\`

Keep the seven folders side by side, because therm-setup calls therm-run and therm-results from the same place. Restart Claude Code. It then picks the right skill when you ask, for example, "solve the sections in this folder".

## First run

This solves THERM 7.8's own sample sill and reads its U-factors. Close the THERM window if it is open, then run these lines in PowerShell. Change `$S` if you installed into a project folder.

```powershell
$S = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force C:\thermwork | Out-Null; Set-Location C:\thermwork

# 1. Check the PC. "ok": true means every check passed; a failed check prints FAIL and its fix.
python "$S\therm-setup\scripts\therm_setup.py" --out env.json

# 2. Copy THERM's sample sill and look at what is in it (materials, BCs, U-factor tags).
Copy-Item "C:\Users\Public\LBNL\THERM7.8\Samples\sample-sill.thmx" sill.thmx
python "$S\therm-model\scripts\therm_model.py" inspect sill.thmx --out sill.inspect.json

# 3. Solve it. The solved model goes to solved\sill.thmx; sill.thmx is left as it was.
python "$S\therm-run\scripts\therm_run.py" sill.thmx --env env.json --out-dir solved --out run.json

# 4. Read the results, then make the NFRC frame and edge table.
python "$S\therm-results\scripts\therm_results.py" solved\sill.thmx --out sill.results.json --csv sill.csv
python "$S\therm-frame-calc\scripts\therm_frame_calc.py" nfrc sill.results.json --markdown sill-nfrc.md
```

`sill.results.json` should show, for the U-factor case and Projected Y, **Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m2K**. These are THERM 7.8.80's own values for this sample. `sill-nfrc.md` lists the Frame and Edge rows. Each solve takes about a second.

5. **Review in THERM.** Open `C:\thermwork\solved\sill.thm` in THERM 7.8 and look at the mesh, isotherms and U-factors. Close THERM before you solve again.

In Claude Code you can ask for the same steps in plain words, such as "check my THERM setup", "solve sill.thmx" or "what is the frame U-factor in solved\sill.thmx".

## Known limits (THERM 7.8)

- **Equivalent-solid glazing only.** A `.thmx` file has no glazing-system data, so the glass and gaps are plain solids: glass k = 1.0 W/mK and one solid per gap with the gap's effective conductivity, which you supply (for example from WINDOW or EN 673). THERM then runs no Condensation Resistance case and gives no "Projected in glass plane" U-factor.
- **Read Projected Y on a sill.** The glass-plane value THERM writes falls back to Projected X, which is wrong (Frame 1.216 instead of 2.016 on the sample sill). The skills never show it. On a sill Projected Y equals the glass-plane value. Head, jamb and divider sections were not checked, so therm-frame-calc asks you to choose the projection for them.
- **No error norm.** THERM 7.8 writes no error energy norm to any file the skills can read, so `simulation_error_pct` is empty. Check the mesh in the THERM window if you need it.
- **One THERM at a time on a PC.** Every THERM 7.8 process, including the THERM window you have open, shares one `Sim` working folder. A second solve at the same time can fail ("Error writing to the disk") or change results slightly. therm-run refuses to start while THERM7.exe is running, so close the THERM window first.
- **No periods in file names.** THERM hangs on a name such as `gap18.51.thmx`. therm-run refuses such a file before starting THERM and suggests a safe name (`gap18p51.thmx`). Periods in folder names are fine. Paths with non-ASCII characters are refused too, because they are untested.
- **ISO 10077-2 values need an engineer's check.** The formulas, the panel conductivity (0.035 W/mK) and the surface resistances have not been checked against the standard's text. Every ISO calc sheet from therm-frame-calc says so and lists each input and formula so you can check them.
- **The THERM licence prohibits service-bureau use.** Run the skills for your own work on your own PC.

## THERM 8.1 (optional)

THERM 8.1.68 is a beta and its file format changes between builds. Use it for design checks only. To use it, install it from the same downloads page next to 7.8. The skills choose 8.1 from a `.thmz` file name, or with `--therm-version 8.1`. therm-setup checks every install it finds.

First run for 8.1, using LBNL's sample sill bundled with therm-setup:

```powershell
$S = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force C:\thermwork81 | Out-Null; Set-Location C:\thermwork81
python "$S\therm-setup\scripts\therm_setup.py" --therm-version 8.1 --out env.json
Copy-Item "$S\therm-setup\assets\smoke-sample-sill.thmz" sill.thmz
python "$S\therm-run\scripts\therm_run.py" sill.thmz --env env.json --out-dir solved --out run.json
python "$S\therm-results\scripts\therm_results.py" solved\sill.thmz --out sill.results.json --csv sill.csv
```

`sill.results.json` should show Frame 2.003613, Edge 2.34569 and SHGC Exterior 1.812795 W/m2K, the values LBNL stores in its own solved sample. 8.1 keeps the glazing system, so it has the Condensation Resistance case, the glass-plane projection and the error norm. NFRC certification does not accept 8.1 results yet.

## Running the tests

`python run_tests.py` runs every skill's tests. They need no THERM install and run on Windows or Linux.
