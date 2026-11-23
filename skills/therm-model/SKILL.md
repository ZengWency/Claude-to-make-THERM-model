---
name: therm-model
description: Builds, inspects and patches LBNL THERM cross-section files from Python, with no THERM install - THERM 7.8 .thmx (the default) and THERM 8.1 .thmz, picked by the output file's extension. build turns a JSON section spec (polygons in mm with materials, boundary segments with boundary conditions and U-factor tags, mesh settings) into a .thmx or a .thmz laid out the way THERM saves it, after checking closure, orientation, overlaps, holes and references. inspect summarises any .thmx or .thmz as JSON. patch sets a record value (7.8), swaps a material, changes a BC, retags boundaries or moves/scales geometry and leaves everything else alone. Use for "build a THERM model", "make a thmx/thmz from this section", "create a sill/jamb/head/mullion section", "what's in this thmx/thmz", "change the wood conductivity", "list THERM polygons/materials/tags", "swap the thermal break material", "change the exterior BC to NFRC", "retag the Frame boundary", or before a therm-run solve. It does not solve models, read results or import WINDOW glazing systems.
---

# therm-model

Writes and edits THERM 7.8 `.thmx` files (one XML file, see "THERM 7.8") and THERM 8.1 `.thmz` files (a zip of `Model.xml`, `Materials.xml`, `Gases.xml`, `SteadyStateBC.xml`). Python 3.10+ standard library only, Windows or Linux. Solving is a separate step (`therm-run`), and reading results is `therm-results`.

## Contract

- **Reads:** a section spec `.json` (`build`) or a `.thmx` / `.thmz` (`inspect`, `patch`), plus optional `--library` sources such as a therm-library project `.json`. For 7.8 the records must carry a `thmx` dict; records without one are skipped with a warning.
- **Writes:** the file named by `-o` (aliases `--thmx`, `--thmz`) and a result JSON on stdout or `--out`. **The output's extension picks the format: `.thmx` = THERM 7.8, `.thmz` = THERM 8.1.** With no output, `build` writes `<spec stem>.thmx` and `patch` writes `<input stem>_patched.thmx` beside the input (a `.thmz` patch needs an explicit `--thmz`). `patch` keeps the input's format. A `.thmx` output whose file stem contains a period is refused, because THERM 7.8 hangs on such names.
- **Before:** therm-library for project materials and BCs (optional), and therm-glazing `solid` on the spec when the glazing should be glass and gap solids or a calibration panel (optional).
- **After:** therm-run to solve. On 7.8, a built `.thmx` has no glass or gap polygons and does not go to therm-glazing; to change the glazing, run therm-glazing `solid` on the spec and build again.

## Commands

```
python scripts/therm_model.py build   SPEC.json [-o OUT.thmx|OUT.thmz] [--library FILE ...] [--out result.json]
python scripts/therm_model.py inspect FILE.thmx|FILE.thmz [--detail] [--out result.json]
python scripts/therm_model.py patch   IN.thmx -o OUT.thmx [edits] [--library FILE ...] [--out result.json]
python scripts/therm_model.py patch   IN.thmz --thmz OUT.thmz [edits] [--library FILE ...] [--out result.json]
```

JSON goes to stdout (or `--out`); it always has `"skill": "therm-model"` and `"version": 1`. Errors go to stderr. Exit 0 success, 1 input or validation error (the message names the polygon, boundary or record), 2 internal error.

## Section spec (build)

Coordinates are in mm, y up, exactly as THERM stores them. See `examples/simple-frame.json` and `examples/wall-3poly.json`.

| Key | Meaning |
|---|---|
| `polygons` | `[{id, material, points: [[x,y],...]}]`. `id` is a label for messages. Points may be clockwise (they are reversed and reported) and need not repeat the first point. |
| `material` | A name, `{name, uuid}` to look up, or an inline record `{name, uuid, conductivity, emissivity}`. A frame cavity is `{name, uuid, type: "cavity", cavity_standard: "ISO15099", gas: "Air", emissivity}`. |
| `boundaries` | `[{start, end, bc, tag}]` or `[{points: [...], bc, tag}]` for a polyline. Segments may span several polygon edges. `tag` is the U-factor tag (`Frame`, `Edge`, any string), optional. Outer edges with no segment become `Adiabatic` and are listed in `adiabatic_edges`. |
| `bc` | A name, `{name, uuid}`, or inline `{name, uuid, type: "simplified", temperature, film_coefficient}`. `type: "comprehensive"` (default) adds convection plus `radiation: automatic \| blackbody \| none`. |
| `materials`, `boundary_conditions` | Optional lists of inline records that polygons and boundaries refer to by name. |
| `cross_section` | `Sill` (default), `Jamb`, `Head`, `Horizontal Divider`, `Vertical Divider`, ... `Mullion` and `Transom` map to the dividers. |
| `model_purpose`, `gravity`, `u_value_basis` | Defaults `Window/Transparent Facade`, `Down`, `Y-projected`. |
| `mesh` | `{type: simmetrix \| quadtree, parameter: 6, error_estimator: true, error_norm: 10, max_iterations: 5}` (defaults shown). |
| `title`, `notes`, `glazing_origin`, `created` | Optional file properties. |

`--library` resolves names from a THERM lib XML (`C:\Users\Public\LBNL\THERM8.1\lib\Materials.xml`, `BoundaryConditionsSteadyState.xml`, `Gases.xml`), another `.thmz`, or JSON `{"materials": [...], "boundary_conditions": [...], "gases": [...]}` whose records use the inline fields above or `{"xml": "<Material>...</Material>"}`. Spec records win over library records. Only the records the model uses are copied into the zip, plus `Adiabatic` and, for cavities, the gas mixture and its pure gases (`Air` is built in).

Before writing, build checks: each polygon is closed, has area and does not cross itself; polygons do not overlap, leave no hole and form one region; neighbours share vertices (T-junction vertices are inserted and reported in `fixes`); each boundary segment lies on the outside; frame cavities are enclosed by solids; every reference resolves with a consistent UUID and name; at least two different non-adiabatic BCs exist.

## THERM 7.8 (.thmx)

`build` and `patch` follow what THERM 7.8.80 accepts headless:

- Geometry is in mm (`units="mm"`), y up, exactly as the spec gives it.
- `Polygon` and `BCPolygon` IDs share one namespace: polygons are numbered 1..n and boundary segments continue from n+1. `inspect` and `patch` report any reused ID, because THERM crashes on one.
- Materials and BCs are written **inline** from records' `thmx` dicts (therm-library's 7.8 record shape), so THERM never needs its binary `.lib` files. Spec records may instead use the inline fields above: solids (`conductivity`, `emissivity`, `color`), frame cavities, and BCs of `type: "simplified"` (or `radiation: "none"`). Other BC kinds, such as `comprehensive` with automatic radiation, need a `thmx` record from therm-library.
- Frame cavities are written with `CavityModel` 4 (ISO 15099) only, with a `Frame Cavity Surface` segment on each cavity edge (`EnclosureID` = cavity number). `cavity_standard` other than `ISO15099` and gases other than Air are refused: a `.thmx` has no gas field. THERM computes the cavity conductivity at solve time.
- Names are matched through one rule: strip every leading `<token>:` prefix, then every trailing `_Cavity_<n>`, then trim (`fromx:sample-sill:Wood (Pine, Spruce, Fir)` matches `Wood (Pine, Spruce, Fir)`). Matching is case-sensitive.
- `mesh.parameter`, `error_estimator`, `error_norm` and `max_iterations` go to `MeshControl`. `mesh.type`, `gravity`, `model_purpose`, `u_value_basis` and `glazing_origin` are not stored in a `.thmx`; build lists them in `warnings`. Solver tolerances come from the ini THERM runs with.
- `cross_section` must be one of the window sections (`Sill`, `Jamb`, `Head`, dividers, meeting rails, `Common Frame`, `Spacer`).

`patch` on a `.thmx` adds `--set-record NAME ATTR VALUE`, which sets one attribute of an inline material or BC record, for example `--set-record "Wood (Pine, Spruce, Fir)" Conductivity 0.28`. Allowed attributes are the record attributes THERM writes, except `Name` and `Index`. `--swap-material` and `--set-bc` copy the new record inline from `--library` (a project `.json` or another `.thmx`) or from the file itself, and drop the old record when nothing uses it. `--scale` and `--offset` move every polygon and boundary point. Stale `Mesh` and `Results` are dropped unless `--keep-results`.

A `.thmx` model loses the glazing-system identity when THERM imports it: THERM runs no Condensation Resistance case and its "Projected in glass plane" U-factor falls back to Projected X. Read Projected Y on a sill.

Checked in THERM 7.8.80 (`PORT-78.md`): the `examples/wall-78.json` wall solved to U = 1.132974 W/m²K against the analytic 1.13299, and `patch --set-record "Wood (Pine, Spruce, Fir)" Conductivity 0.28` on LBNL's `sample-sill.thmx` gave Frame 2.887787 W/m²K (Projected Y), the same as the hand edit.

## Patch edits

Applied in this order. A selector is a BC name, `tag:<tag>` or `id:<boundary ID>`.

- `--swap-material OLD NEW`: solid materials only. Updates adjacent boundary emissivities and replaces the old record if nothing else uses it.
- `--set-bc SELECTOR BC`: sets the BC name and colour; copies the record in from `--library` if needed.
- `--retag SELECTOR TAG`: sets the U-factor tag (`""` clears it).
- `--scale S`, `--offset DX,DY` (write `--offset=-10,5` for a negative DX): moves points, boundary ends, origins and the glazing origin. Scale also updates cavity areas and refuses files with a glazing system.

Untouched entries are copied byte for byte, and untouched XML keeps its layout. Results (`Mesh.xml`, `SteadyStateResults.xml`, `SteadyStateMeshResults.xml`) are dropped because they are stale; `--keep-results` keeps them.

## Example

THERM 7.8:

```
python scripts/therm_model.py build examples/wall-78.json -o out/wall_78.thmx
python scripts/therm_model.py patch sample-sill.thmx -o out/sill_wood028.thmx --set-record "Wood (Pine, Spruce, Fir)" Conductivity 0.28
python scripts/therm_model.py inspect out/sill_wood028.thmx
```

THERM 8.1:

```
python scripts/therm_model.py build examples/simple-frame.json --thmz out/simple-frame.thmz
python scripts/therm_model.py patch out/simple-frame.thmz --thmz out/simple-frame-nfrc.thmz --library "C:\Users\Public\LBNL\THERM8.1\lib\BoundaryConditionsSteadyState.xml" --set-bc "ISO 10077-2 Exterior (Rse 0.04)" "NFRC 100-2010 Exterior"
python scripts/therm_model.py inspect out/simple-frame-nfrc.thmz
```

The first command reports 14 polygons, 3 frame cavities, 29 boundaries, `Frame` 71 mm and `Edge` 63.5 mm.

## Limits

- 8.1: the layout copies THERM 8.1.30 saves (fairyfly and LBNL samples), not the libraryfemtherm 0.24.0 schemas.
- 7.8: build writes solid polygons (`Type` 1) and ISO 15099 frame cavities only, no glazing systems (use therm-glazing on the `.thmx`, or a spec region). Built models were solved in THERM 7.8.80; parallel solves and normal-user runs are untested.
- 8.1 build writes `Material` polygons and ISO 15099 frame cavities only: no glazing systems (use `therm-glazing`), no glazing or shade cavities, no polygons with holes (split them), no cavities touching the outside or each other. CEN cavities are written as plain polygons, as fairyfly does; that path is unverified.
- `EdgeID` is always 0 and UUIDs are derived from the spec, so the same spec gives the same file apart from dates. `Directory` records the folder the file was built in.
- Inspect reads XML and LibraryFEMTherm JSON entries; patch edits XML files only.
- Overlap checking compares edges and edge midpoints. It catches crossings and containment, not every touching configuration.

Tests: run `python -m unittest` from this folder.
