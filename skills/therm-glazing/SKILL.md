---
name: therm-glazing
description: Shows, patches or replaces the glazing system (IGU) in an LBNL THERM 8.1 .thmz, and turns the IGU of a THERM 7.8 .thmx into equivalent solids, with no THERM install. show lists each IGU's layers, gaps, thicknesses, conductivities, emissivities, U-values, film coefficients, sight-line and edge-of-glass heights and the polygons that use it. patch edits a GUI-imported IGU from a small JSON change set and moves the glass and gap polygons to match new thicknesses, refusing if they would hit the frame. solid swaps the IGU for plain glass and gap solids or an ISO 10077-2 calibration panel, in a .thmz or in a therm-model spec. Use for "what glazing is in this thmz", "change the IGU", "thicker gap", "swap to argon", "change the low-e emissivity", "IGU film coefficient", "replace glazing with a calibration panel", "ISO 10077-2 Uf panel", or "model the glass as solids". It does not import from WINDOW, compute IGU U-values or solve.
---

# therm-glazing

Reads and edits `GlazingSystems` in a THERM 8.1 `.thmz`, and the glass and gap polygons of a THERM 7.8 `.thmx` (equivalent solid only, see "THERM 7.8"). It needs Python 3.10+ and the standard library only, and runs on Windows or Linux. Building frames is therm-model's job, BC and material records come from therm-library, and reading results is therm-results.

## Contract

- **Reads:** a `.thmz` (8.1), a `.thmx` (7.8; `show` and `solid` only), or a therm-model spec `.json` (`solid` only), plus a change set `.json` (`--changes`) and optional `--library` sources. On 7.8, `--library` takes a therm-library project `.json` whose records carry a `thmx` dict (the therm-library 7.8 record shape), or another `.thmx`.
- **Writes:** a new `.thmz` (`--thmz`, or `--in-place`), a new `.thmx` (`-o`/`--thmx`, default `<stem>_solid.thmx` beside the input), or a new spec `.json` (`--spec`), and a report JSON on stdout or `--out`. The output's extension picks the format: `.thmx` = THERM 7.8, `.thmz` = THERM 8.1. A `.thmx` input always gives a `.thmx` output, and its file stem may not contain a period.
- **Before:** therm-library (optional). Then either a therm-model spec `.json` that has not been built yet, or a `.thmx` / `.thmz` saved from the THERM GUI with an IGU. On 7.8, a `.thmx` built by therm-model has no glass or gap polygons, so it cannot come here; change the spec instead.
- **After:** therm-model `build` for a spec `.json`; therm-run to solve a `.thmx` or `.thmz`.

## Commands

```
python scripts/therm_glazing.py show  IN.thmz [--out report.json]
python scripts/therm_glazing.py patch IN.thmz --changes CH.json --thmz OUT.thmz [--library SRC ...] [--out report.json]
python scripts/therm_glazing.py solid IN.thmz --changes CH.json --thmz OUT.thmz [--library SRC ...] [--out report.json]
python scripts/therm_glazing.py solid SPEC.json --changes CH.json --spec OUT.json
python scripts/therm_glazing.py show  IN.thmx [--out report.json]
python scripts/therm_glazing.py solid IN.thmx --changes CH.json [-o OUT.thmx] [--library SRC ...] [--out report.json]
```

`--changes` takes a file, `-` for stdin, or an inline JSON object. Use `--in-place` instead of `--thmz` to overwrite the input; without it, the script refuses to write over the input. `--library` adds gases and BCs from a therm-library project `.json`, another `.thmz`, a library `.xml` or a THERM `lib` folder.

The JSON report goes to stdout or `--out`, and always has `"skill": "therm-glazing"` and `"version": 1`. Errors and warnings go to stderr. Exit codes are 0 for success, 1 for an input error (including a refused move), and 2 when an entry cannot be parsed.

## show

Each entry in `glazing_systems` has `index`, `id`, `name`, `layers` (thickness, conductivity, surface 1 and 2 emissivity and temperature), `gaps` (thickness, effective conductivity, gas components), `properties` (Winter and Summer U-value plus interior and exterior `h`, `hc`, `hr` and temperature), `sight_line_height_mm`, `eog_height_mm`, `igu_bc` (the IGU-surface BC record), `interior_direction` (for example `+x`), and `polygons` with their geometric thickness.

A polygon's `GlazingSystem/ID` holds the system's `Index`, not its `ID`. On sample-sill, the system has ID 8 and Index 1, and its polygons say 1.

## patch (option A)

The change set uses these keys, and every one is optional:

```json
{
  "glazing_system": 1,
  "name": "new name",
  "layers": [{"index": 1, "thickness_mm": 6, "conductivity_w_mk": 1.0, "emissivity_1": 0.037, "emissivity_2": 0.84, "name": "..."}],
  "gaps": [{"index": 0, "thickness_mm": 18, "conductivity_w_mk": 0.0339, "gas": "Argon"}],
  "properties": {"winter": {"u_value": 1.6, "interior": {"hc": 2.7, "hr": 4.2, "temperature": 21}, "exterior": {"hc": 26, "hr": 3.2}}},
  "also_shift": [77, 42]
}
```

- `glazing_system` is an Index, ID or name. It is required only when the file has more than one IGU.
- Layer and gap `index` count from the exterior, starting at 0. Surface 1 is the exterior face.
- `gas` is a pure gas or mixture name, or `[{"gas": "Argon", "fraction": 0.9}, {"gas": "Air", "fraction": 0.1}]`, looked up in the file's `Gases.xml` and `--library`. THERM uses the gap's `conductivity_w_mk`, so set it as well.
- `h` must equal `hc + hr`. Give `hc` and `hr`, and `h` follows.
- A thickness change shifts every point on the interior side of that layer's or gap's centre line by the difference. This covers the glazing polygons, their boundaries, and the glazing origin. Frame polygons stay put unless their IDs are in `also_shift`. Points of a listed polygon past the centre line move, so a spacer that spans the gap stretches. `"all"` stretches the whole section.
- If a moved polygon would overlap a fixed one, the patch is refused and the error names both polygons. A warning reports a fixed polygon that loses contact with the moved glazing, because that usually means a void.
- The script keeps these records consistent with the change: the gap's `Cavity` record (emissivities, thickness dimension, area), the glazing-cavity and glass-face boundary emissivities, and the IGU-surface BC. That BC's film coefficient is the Winter interior `hc` for a comprehensive BC, or `h` for a simplified one, and its temperature follows too. Exterior coefficients change only in the IGU record, because the exterior BC is shared with the frame.
- An empty change set copies the file unchanged.

## solid (option C, ISO 10077-2 style)

When the input is a `.thmz`, every polygon of the IGU becomes a `Material` polygon, and the geometry is not changed. Glass gets a default conductivity of 1.0 W/mK and emissivity of 0.84 (`"glass": {"conductivity_w_mk", "emissivity"}` overrides both). Each gap becomes a solid whose conductivity is the effective value from `"gaps": [{"index", "conductivity_w_mk"}]`, or the value the IGU record stores. `"panel": {"thickness_mm", "conductivity_w_mk": 0.035}` makes every IGU polygon one calibration-panel material for the Uf run, and the thickness must equal the IGU's. `"interior_bc"` (required) names the BC that replaces the IGU film on the glass. It comes from the file or `--library`, for example therm-library's `presets iso`. The script also removes the glazing-cavity boundaries, the gap cavity, the `GlazingSystem` and the now-unused IGU BC.

When the input is a spec `.json`, `"region"` names a rectangular polygon in a therm-model spec. `"interior"` (`+x`, `-x`, `+y` or `-y`) gives the direction from exterior to interior. The script replaces the region with stacked rectangles: `"layers": [{"thickness_mm"}]` and `"gaps": [{"thickness_mm", "conductivity_w_mk"}]`, or one `"panel"`. The total thickness must equal the region's width, so the spec's boundaries still meet the faces.

**Option C loses THERM's glass-plane projection and the automatic IGU film BC.** Read U on the projection you need and check the glass BC yourself.

## THERM 7.8 (.thmx): equivalent solid only

A THERM 7.8 `.thmx` stores no IGU layers, gaps, gas fills or IGU properties, and THERM 7.8 turns an imported IGU into plain solids anyway. So on 7.8:

- `solid` is the only edit. Glass panes (polygon `Type` 11 and 9, or materials named `<IGU>_Glass_<n>`) become solids with conductivity 1.0 W/mK and emissivity 0.84 (`"glass"` overrides both). Each gap (`Type` 5, or `<IGU>_Gap_<n>`) becomes one solid.
- **Every gap needs `conductivity_w_mk`**, its effective conductivity in W/mK: `{"gaps": [{"index": 0, "conductivity_w_mk": 0.049352}]}`. A `.thmx` has no gas field, so a gas name alone is an error that names `conductivity_w_mk`. The value the file already stores is not reused. Compute the value yourself, for example with pywincalc or EN 673, outside this skill.
- The inside film stays on the glass. The IGU's own "U-factor Inside Film" BC is kept as an ordinary convection BC, or `"interior_bc"` replaces it with a BC from the file or a `--library` record that has a `thmx` dict.
- The script removes the glazing-cavity boundaries, empties `GlazingSystems`, drops the old `<IGU>_Glass_<n>`/`_Gap_<n>` records and strips stale `Mesh` and `Results`. `panel` is not available on a `.thmx`; use spec mode and therm-model for an ISO calibration panel.
- `patch` (option A) refuses a `.thmx`. `show` lists the glass and gap polygons with their conductivities.

**THERM 7.8 gives no Condensation Resistance case and no "Projected in glass plane" U-factor for these models.** THERM reports a glass-plane value, but it falls back to Projected X. On a sill, read Projected Y.

Checked in THERM 7.8.80 (`skills/therm-model/PORT-78.md`): `solid` on LBNL's `sample-sill.thmx` with the stored gap value 0.049352 solved to Frame 2.016345, Edge 2.337976 and SHGC Exterior 1.876081 W/m²K (Projected Y), the same as THERM's own import. With half that value, Edge U fell to 1.837270.

## Results and calculation-ready

Any file the script changes loses `Mesh.xml`, `SteadyStateResults.xml` and `SteadyStateMeshResults.xml`, so nobody can read stale results. `CalculationReady` is left as it is. THERM's own unsolved files (LBNL `sample-sill-no-results`, fairyfly `test_no_result`) keep `true`, so it does not mean the results are fresh.

## Example

```
python scripts/therm_glazing.py show sample-sill.thmz
python scripts/therm_glazing.py patch sample-sill.thmz --changes examples/patch-lowe-argon.json --library "C:\Users\Public\LBNL\THERM8.1\lib" --thmz sill-argon.thmz
python ../therm-library/scripts/therm_library.py presets iso --out lib.json
python scripts/therm_glazing.py solid sample-sill.thmz --library lib.json --changes "{\"panel\": {\"thickness_mm\": 26.51}, \"interior_bc\": \"ISO Interior Rsi 0.13 normal (20 C)\"}" --thmz sill-panel.thmz
```

On sample-sill, a 2 mm thicker gap is refused because the inner pane would overlap glazing tape 77 and wood stop 42. With `"also_shift": [77, 42]`, the inner pane moves +2.0 mm in x, and the script warns that the spacer polygons 64 and 66 and frame cavity 83 lose contact.

## Limits

- THERM does not recompute an IGU. After a patch, the U-value, films and surface temperatures are whatever you supply. Get them from WINDOW, or from pywincalc as an external tool (this script never imports it).
- Glazing must be axis-aligned rectangles, as THERM imports them. Rotated IGUs and shade layers are not handled.
- A thinner layer is refused if a moved point would cross the centre line.
- The overlap test samples vertices, edge midpoints and centroids. It is exact for rectangles, but could miss a thin sliver between two concave shapes.
- Stretched frame cavities keep their stored dimensions. THERM is expected to recompute them at solve time, which is not yet verified.
- Only `.xml` entries are read. A `.thmz` with LibraryFEMTherm `.json` entries is refused.
- Renaming the IGU leaves the IGU BC's name unchanged.
- 8.1: tested on LBNL `sample-sill.thmz` and `sample-sill-no-results.thmz` (THERM 8.1.30 layout).
- 7.8: tested on LBNL's shipped `sample-sill.thmx` (7.8.55) and solved in THERM 7.8.80. Other IGU layouts (triple glazing, dividers) are untested.

Tests: run `python -m unittest discover -s tests` from this folder.
