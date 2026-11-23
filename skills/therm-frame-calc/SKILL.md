---
name: therm-frame-calc
description: Turns therm-results JSON from LBNL THERM 8.1 or 7.8 runs into frame values. `iso` computes ISO 10077-2 Uf from a calibration-panel run and the linear thermal transmittance psi from a glazed run, and writes a calc sheet (JSON, optional Markdown) with every input, formula and intermediate value. `nfrc` collects the Frame and Edge U-factors and lengths into a table for WINDOW's frame library. Use when asked to "calculate Uf", "frame U-value to ISO 10077-2", "psi value of the spacer", "linear thermal transmittance from THERM", "L2D", "calibration panel calc", "NFRC frame and edge U-factor", or "values for the WINDOW frame library". It does no THERM run; solve the models and read them with therm-results first.
---

# therm-frame-calc

Pure arithmetic on therm-results output. It needs no THERM install and runs on Windows or Linux with Python 3.10+ and the standard library only.

## Contract

- **Reads:** therm-results JSON (from a `.thmz` or a `.thmx`): a panel run and a glazed run for `iso`, one run for `nfrc`.
- **7.8 rules** (results with `therm_format` = `thmx-7.8`): `nfrc` defaults to Projected Y for a Sill, refuses other cross-sections unless `--projection` is given, and refuses `--projection glass`. `iso` uses the derived, unsigned heat flow, so the opposite-sign check cannot catch mixed tags.
- **Writes:** a calc sheet JSON on stdout or `--out`, and an optional Markdown sheet (`--markdown`).
- **Before:** therm-results.
- **After:** none. The engineer checks the values, or enters the NFRC table in WINDOW's frame library.

## Inputs

- therm-results JSON files (lengths in mm). The `U-factor` case is used unless you pass `--case`.
- `iso`:
  - `--panel`: results of the run with a calibration panel (λ = 0.035 W/mK) in place of the glazing.
  - `--glazed`: results of the run with the real glazing.
  - `--tag`: the U-factor tag(s) to sum, repeatable. `--glazed-tag` sets different tags for the glazed run.
  - `--bf` projected frame width, `--bp` visible panel width and `--bg` visible glazing width, all in mm. `--bg` defaults to `--bp`.
  - `--ug` in W/m2K.
  - Either `--up` in W/m2K, or `--panel-thickness` in mm. With a thickness, Up = 1/(Rsi + dp/λ + Rse), using `--panel-conductivity` (default 0.035), `--rsi` (0.13) and `--rse` (0.04).
- `nfrc`: one results JSON, `--frame-tag` (default `Frame`), `--edge-tag` (default `Edge`) and `--projection`: `glass` (default for 8.1, projected in glass plane), `displayed`, `y`, `x` or `total`. On 7.8 the default is `y` for a Sill; other sections must pass one, and `glass` is refused.

## Outputs

JSON on stdout, or in the file given by `--out`. `--markdown FILE` also writes a readable calc sheet. Top-level keys are `skill`, `version` (1), `mode`, `standard`, `inputs`, `assumptions` and `warnings`, plus:

- `iso`: `disclaimer`, `steps` (each with `formula`, `values` and `result`) and `results` with `l2d_panel_w_mk`, `uf_w_m2k`, `l2d_glazed_w_mk` and `psi_w_mk`.
  - L2D = Σ |q| / ΔT over the chosen tags.
  - Uf = (L2D_panel − Up·bp) / bf.
  - Ψ = L2D_glazed − Uf·bf − Ug·bg.
- `nfrc`: `note`, `simulation_error_pct`, and `table` with one row each for Frame and Edge: `u_factor_w_m2k`, `length_mm`, `projection`, `delta_t` and `heat_flow_w_m`.

Exit codes: 0 success. 1 for an input error. The message names the problem: a missing file, case or tag, a length unit other than mm, a width that is not positive, tags with opposite heat-flow signs, or Uf ≤ 0. Warnings go to stderr.

## Example

```
python ..\therm-results\scripts\therm_results.py panel.thmz --out panel.json
python ..\therm-results\scripts\therm_results.py glazed.thmz --out glazed.json
python scripts\therm_frame_calc.py iso --panel panel.json --glazed glazed.json --tag Frame --tag Edge --bf 72 --bp 190 --ug 1.1 --panel-thickness 28 --out sill-iso.json --markdown sill-iso.md
python scripts\therm_frame_calc.py nfrc sill.json --markdown sill-nfrc.md
```

On LBNL's `sample-sill.thmz`, `nfrc` reports Frame U = 2.003613 W/m2K over 42.875 mm and Edge U = 2.34569 W/m2K over 63.5 mm, both projected in the glass plane.

## Limits

- **The ISO formulas come from the therm-skills research doc, not the standard text.** Check them, λ = 0.035 and the surface resistances against a licensed copy of ISO 10077-2 before any certification or compliance use.
- therm-results labels `heat_flux_w_m2`, but THERM stores the heat flow per metre of section depth (W/m). The script uses |q|/ΔT and warns if that differs from U × L of the displayed projection by more than 0.5 %.
- THERM's heat-flow sign depends on the boundary direction. Magnitudes are used. Pick tags on one side of the section only, covering the whole heat flow through it.
- Up from thickness uses the normal Rsi, not the reduced-radiation Rsi used at frame edges. Pass `--up` if your panel run used other films.
- NFRC certification currently uses THERM 7 / WINDOW 7. Treat THERM 8.1 values as design checks. WINDOW normally imports the THERM file directly, so use the table to enter or check values by hand.
- Ψ < 0 is reported with a warning, not refused.
- THERM 7.8: no error norm (`simulation_error_pct` is null), and no CR-based outputs. Projected Y equals the glass plane on the sample sill only; head, jamb and divider were not checked.

Tests: run `python -m unittest discover -s tests` from this folder.
