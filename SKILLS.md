# The seven THERM skills

This file describes each THERM skill: what it does, what it reads and writes, its main command, and where it sits in the chain. The install steps and the first run are in `README.md`. A plain-English overview is in `EXPLAINER.md`.

The skills are small Python scripts that Claude Code runs for you. They need Python 3.10 or newer and the standard library only. Only therm-setup and therm-run need Windows and a THERM install. The other five also run on Linux.

The default THERM version is 7.8.80, the version NFRC certification uses. THERM 8.1 also works, but it is a beta. This file follows the 7.8 path. Each `SKILL.md` covers the 8.1 options.

## The chain

A typical job runs left to right. You can stop after any step.

```
therm-setup -> therm-library -> therm-glazing -> therm-model -> therm-run -> therm-results -> therm-frame-calc
  (once)        (optional)       (optional)                                                    (optional)
```

- therm-setup runs once on a new PC and writes `env.json`.
- therm-library collects materials and boundary conditions (BCs) into a project library `.json`.
- therm-glazing turns the glazing region of a section spec into glass and gap solids, or a calibration panel.
- therm-model builds the `.thmx` from the spec.
- therm-run solves it.
- therm-results reads the U-factors and temperatures.
- therm-frame-calc turns those results into ISO 10077-2 Uf and psi, or the NFRC frame and edge table.

You can also start from a section you drew in THERM. Save it as a `.thmx`, then start at therm-glazing (if it has an IGU), at therm-model (to change a value), or at therm-run.

In the commands below, `$S` is the skills folder, as in the README first run.

## therm-setup

**What it does.** It checks that your PC can solve THERM files with no window, then solves a sample as a test. It finds THERM 7.8 and 8.1 installs and checks the VC++ x86 and Intel Fortran runtimes, the settings INI and its libraries, and whether `THERM_CLI_PW` is set. It never prints the password. Every failed check names its fix. It does not install THERM or change any setting.

**Inputs.** The local THERM install, its settings INI and libraries, and `THERM_CLI_PW`. No input files.

**Outputs.** `env.json` (with `--out`), and the same JSON on stdout. Each failed check prints `FAIL <check>` and its fix to stderr. Exit 0 means every check passed.

**Main command.**

```
python "$S\therm-setup\scripts\therm_setup.py" --out env.json
```

The 7.8 smoke solve uses a copy of THERM's own `Samples\sample-sill.thmx`. It must give Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m2K (U-factor case, Projected Y) within 0.5 %.

**Chain.** Before: none. After: therm-library, therm-glazing or therm-model. therm-run reads `env.json` with `--env`. therm-setup calls therm-run and therm-results through their command lines, so keep the seven skill folders side by side.

## therm-library

**What it does.** It looks up THERM materials and BCs by name, and builds a project library `.json` from them. It can add new solid materials and BCs, and it writes ready-made NFRC 100-2010 and ISO 10077-2 BC sets. It never writes into THERM's own library folder.

On 7.8, THERM's `.lib` files are binary. The skill lists their names but cannot read their values. It ships its own 7.8 records in `assets/records-78.json` (the NFRC BC set and the materials of LBNL's sample sill), and these are written inline into the `.thmx`.

**Inputs.** The bundled records, a `.thmx`, an earlier project `.json`, or (names only) the `.lib` files that the 7.8 INI names.

**Outputs.** A project library `.json` (`--out`). `list` and `find` print JSON to stdout.

**Main commands.**

```
python "$S\therm-library\scripts\therm_library.py" presets nfrc --out lib.json
python "$S\therm-library\scripts\therm_library.py" presets iso --out lib.json
python "$S\therm-library\scripts\therm_library.py" add material --name "PA66 GF25 thermal break" --conductivity 0.3 --out lib.json
python "$S\therm-library\scripts\therm_library.py" find "NFRC 100-2010" --kind bc
```

`presets nfrc` writes 10 BCs: NFRC 100-2010 Exterior, Adiabatic and 8 interior frame films. `presets iso` writes Rse 0.04 (h 25), Rsi 0.13 normal (h 7.692308), Rsi 0.20 reduced radiation (h 5.0) and Adiabatic. The ISO values carry an engineer-check flag.

**Chain.** Before: therm-setup. After: therm-glazing `solid` on a spec (optional), then therm-model. Both take the project `.json` with `--library`.

## therm-glazing

**What it does.** On 7.8 it models the glazing as equivalent solids. Glass panes become solids with k = 1.0 W/mK and emissivity 0.84. Each gap becomes one solid with an effective conductivity that you give, for example from WINDOW or EN 673. On a section spec it can also replace a rectangular region with one ISO calibration panel for a Uf run. `show` lists the glass and gap polygons.

**Inputs.** A therm-model spec `.json`, or a `.thmx` saved from the THERM GUI with an IGU. A change set `.json` (`--changes`). Optional `--library` sources such as a therm-library project `.json`.

**Outputs.** A new spec `.json` (`--spec`) or a new `.thmx` (`-o`, default `<stem>_solid.thmx`). A report JSON on stdout or `--out`. Changed files lose their old mesh and results.

**Main commands.**

```
python "$S\therm-glazing\scripts\therm_glazing.py" solid SPEC.json --changes CH.json --spec OUT.json
python "$S\therm-glazing\scripts\therm_glazing.py" solid IN.thmx --changes "{\"gaps\": [{\"index\": 0, \"conductivity_w_mk\": 0.049352}]}" -o OUT.thmx
```

On a `.thmx`, every gap needs `conductivity_w_mk`. A gas name alone is an error. The calibration panel works in spec mode only. Run on LBNL's `sample-sill.thmx` with the stored gap value 0.049352, `solid` gave THERM's own Frame, Edge and SHGC Exterior values.

**Chain.** Before: therm-library (optional), then a spec `.json` not yet built or a GUI-saved `.thmx`. After: therm-model `build` for a spec, or therm-run for a `.thmx`. A `.thmx` built by therm-model has no glass or gap polygons, so to change its glazing you edit the spec and build again.

## therm-model

**What it does.** It builds a THERM section from a JSON spec, shows what is in a section, or edits one. It needs no THERM install. Before it writes, `build` checks that each polygon is closed and does not cross itself, that polygons do not overlap or leave holes, that boundaries lie on the outside, and that every material and BC resolves. `patch` can set one record value, swap a material, change a BC, retag boundaries, or move and scale the geometry. It leaves everything else as it was.

**Inputs.** A section spec `.json` (`build`), or a `.thmx` (`inspect`, `patch`). Optional `--library` sources such as a therm-library project `.json`. A spec lists polygons in mm with materials, and boundary segments with BCs and U-factor tags. See `examples/wall-78.json`.

**Outputs.** The file named by `-o`. The extension picks the format: `.thmx` is THERM 7.8, `.thmz` is THERM 8.1. A result JSON on stdout or `--out`. `patch` drops the old mesh and results unless you pass `--keep-results`.

**Main commands.**

```
python "$S\therm-model\scripts\therm_model.py" build wall-78.json -o wall_78.thmx --library lib.json
python "$S\therm-model\scripts\therm_model.py" inspect sill.thmx --out sill.inspect.json
python "$S\therm-model\scripts\therm_model.py" patch sill.thmx -o sill_wood028.thmx --set-record "Wood (Pine, Spruce, Fir)" Conductivity 0.28
```

Checked in THERM 7.8.80, the `examples/wall-78.json` wall solved to U = 1.132974 W/m2K against the analytic 1.13299. The patch above on LBNL's `sample-sill.thmx` gave Frame 2.887787 W/m2K (Projected Y), the same as a hand edit.

**Chain.** Before: therm-library and therm-glazing `solid` on the spec (both optional). After: therm-run.

## therm-run

**What it does.** It solves `.thmx` files with THERM 7.8 and no window, one file at a time, under a timeout. It solves a copy in a temp folder and copies the solved files out only after a good solve. It decides pass or fail from THERM's log, not the exit code. It kills a THERM that hangs on a dialog and reports the dialog text.

Before it starts THERM, it refuses a file name with extra periods, a non-ASCII path, two inputs with the same stem, a missing settings INI, or a `THERM7.exe` that is already running.

**Inputs.** One or more `.thmx` files or folders (folders are not searched below the top level). Optional `env.json` from therm-setup (`--env`). The password from `THERM_CLI_PW`.

**Outputs.** For each good solve, `<out>/<stem>.thmx` (the model with results and mesh) and `<out>/<stem>.thm`. One `.log` per file. A run JSON on stdout or `--out`, with each file's `status` (`solved`, `failed`, `timeout`, `error` or `crashed`), THERM's error text, wall time and solved file. Exit 0 means every file solved, 2 means at least one did not.

**Main command.**

```
python "$S\therm-run\scripts\therm_run.py" sill.thmx --env env.json --out-dir solved --out run.json
```

In testing, THERM 7.8.80 solved a copy of its `sample-sill.thmx` in 0.6 s.

**Chain.** Before: therm-model, or therm-glazing on a GUI-saved `.thmx`. After: therm-results on each solved `.thmx`.

## therm-results

**What it does.** It reads the results that THERM saved into a solved `.thmx`. For each case and each U-factor tag it reports delta T, heat flow, each projection's length and U, the displayed U-factor, and the tag temperatures. It needs no THERM install. It does not solve and does not read transient results.

**Inputs.** One solved `.thmx` from therm-run. The extension picks the reader: `.thmx` is 7.8, anything else is 8.1.

**Outputs.** A results JSON on stdout or `--out`, and an optional flat CSV (`--csv`) with one row per tag projection. The input is not changed.

On 7.8, `displayed` is Projected Y. The "Projected in glass plane" value is never shown, because it falls back to Projected X. `simulation_error_pct` is null. A tag's `tmean_c` is the plain average of its node temperatures, not weighted by boundary length.

**Main command.**

```
python "$S\therm-results\scripts\therm_results.py" solved\sill.thmx --out sill.results.json --csv sill.csv
```

On THERM 7.8's sample sill this gives Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m2K (U-factor case, Projected Y).

**Chain.** Before: therm-run. After: therm-frame-calc.

## therm-frame-calc

**What it does.** It does arithmetic on therm-results output. It runs no THERM.

- `iso` works out ISO 10077-2 Uf from a calibration-panel run, and the linear thermal transmittance psi from a glazed run. It writes a calc sheet with every input, formula and intermediate value.
- `nfrc` puts the Frame and Edge U-factors and lengths into a table for entering or checking values in WINDOW's frame library.

The formulas `iso` uses are L2D = sum of |q| / delta T over the chosen tags, Uf = (L2D_panel - Up x bp) / bf, and psi = L2D_glazed - Uf x bf - Ug x bg.

**Inputs.** therm-results JSON. `iso` needs a panel run (`--panel`), a glazed run (`--glazed`), the tags (`--tag`), the widths `--bf`, `--bp` and optional `--bg` in mm, `--ug`, and either `--up` or `--panel-thickness`. `nfrc` needs one results file. On 7.8 it uses Projected Y for a Sill, and other sections must pass `--projection`.

**Outputs.** A calc sheet JSON on stdout or `--out`, and an optional Markdown sheet (`--markdown`). Exit 1 names the input problem, for example a missing tag or Uf of 0 or less.

**Main commands.**

```
python "$S\therm-frame-calc\scripts\therm_frame_calc.py" nfrc sill.results.json --markdown sill-nfrc.md
python "$S\therm-frame-calc\scripts\therm_frame_calc.py" iso --panel panel.json --glazed glazed.json --tag Frame --tag Edge --bf 72 --bp 190 --ug 1.1 --panel-thickness 28 --out sill-iso.json --markdown sill-iso.md
```

**Chain.** Before: therm-results. After: none. The engineer checks the values, or enters the NFRC table in WINDOW.

## Limits on 7.8

- **Glazing is equivalent solids only.** A `.thmx` holds no glazing-system data. Glass is a solid with k = 1.0 W/mK, and each gap is one solid with the effective conductivity you supply. THERM then runs no Condensation Resistance case.
- **Read Projected Y on a sill.** THERM's "Projected in glass plane" value falls back to Projected X, which is wrong (Frame 1.216 instead of 2.016 on the sample sill). The skills never show it. Head, jamb and divider sections were not checked, so therm-frame-calc asks you to choose the projection for them.
- **No error norm.** THERM 7.8 writes its error energy norm to no file the skills can read, so `simulation_error_pct` is empty. Open the solved file in THERM to check the mesh.
- **One THERM at a time.** Every THERM 7.8 process on a PC, including an open THERM window, shares one `Sim` working folder. A second solve at the same time can fail or change results slightly. therm-run refuses to start while `THERM7.exe` is running.
- **No periods in file names.** THERM hangs on a name such as `gap18.51.thmx`. therm-run refuses it and suggests `gap18p51.thmx`. Periods in folder names are fine. Non-ASCII paths are refused because they are untested.
- **Built models are solid polygons and ISO 15099 frame cavities only.** Frame cavities must be Air. Inline BCs with automatic radiation need a therm-library record.
- **The calibration panel needs spec mode.** therm-glazing cannot put a panel into an existing `.thmx`. Use a spec and therm-model.
- **Untested on 7.8.** Parallel solves, runs as a normal (non-admin) user, triple-glazed IGUs and divider sections.
- **ISO 10077-2 values need an engineer's check.** The formulas, the 0.035 W/mK panel conductivity and the surface resistances were not checked against the standard's text. Every ISO calc sheet lists its inputs and formulas so you can check them.
- **NFRC values are for checking.** The NFRC table is for entering or checking values in WINDOW. Certification still needs a full NFRC model made in the THERM window, with its glazing system.
- **Licence.** THERM's licence does not allow service-bureau use. Run the skills for your own work on your own PC.
