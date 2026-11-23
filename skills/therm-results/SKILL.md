---
name: therm-results
description: Reads a solved LBNL THERM 8.1 .thmz or THERM 7.8 .thmx file and writes its results as JSON, with an optional flat CSV. For each case (U-factor, and Condensation Resistance when it was run) and each U-factor tag, it reports DeltaT, heat flux, every projection (length and U), the displayed U-factor, tag and global temperatures, and the error norm. Use after a THERM -calc run, or when asked to "read THERM results", "get the U-factor from a thmz" or "from a thmx", "frame/edge U-value", "Uf from THERM", "THERM surface temperatures", or "export THERM results to CSV". It does not solve models and does not read transient results.
---

# therm-results

Reads the results that THERM saved into a `.thmz` (8.1) or `.thmx` (7.8) after `-calc`. The reader is picked by extension: `.thmx` = 7.8, anything else = 8.1. It needs no THERM install and runs on Windows or Linux with Python 3.10+ and the standard library only.

## Contract

- **Reads:** one solved `.thmz` (THERM 8.1) or `.thmx` (THERM 7.8, saved with `SaveXML=1`; temperatures need `ExportMeshToTHMX=1`).
- **Writes:** a results JSON on stdout or `--out`, and an optional flat CSV (`--csv`). The input is not changed. 7.8 output has the same shape plus `therm_format` (`thmx-7.8`), `therm_version`, `cross_section`, and per case `frame_cavities` and `surface_nodes_on_segments`.
- **7.8 rules:** `Projected in glass plane` is never shown or listed (it falls back to Projected X after a `.thmx` import). `displayed` is Projected Y; a cross-section other than Sill gets a warning. `simulation_error_pct` is null (error norm unavailable). `heat_flux_w_m2` is derived as U × length × ΔT (W/m, unsigned). Material names are normalised with the rule in `research/library-record-78.md` section 3.
- **Before:** therm-run.
- **After:** therm-frame-calc for Uf, psi or the NFRC frame and edge table.

## Input

- One solved file: a `.thmx` (THERM 7.8) or a `.thmz` (THERM 8.1). The extension picks the reader. For a `.thmz`, the script reads the XML entries that THERM writes, and also the `.json` entry variants that LibraryFEMTherm can write.

## THERM 7.8 `.thmx`

- U-factors come from `Results/Case/U-factors`, all six projections, every case present. The 7.8 labels (`Total length`, `Projected X`, `Projected Y`, `Custom length`, `Projected in glass plane`, `Custom rotation`) are matched case-insensitively and written with the 8.1 names (`Total Length`, `Custom Length`, `Custom Rotation`, ...).
- Tag temperatures: the convection-BC mesh nodes are placed in model mm as node m × 1000 + centre of the polygon bounding box, then matched to the `BCPolygon` segments that carry the tag (within 0.05 mm). `surface_nodes_on_segments` reports how many surface nodes landed on any segment; under 100 % gives a warning. The rule was checked on THERM-written meshes only.
- `frame_cavities` lists each cavity's `name`, `base_name` (normalised) and THERM's recomputed `conductivity_w_mk`.
- Degenerate projections (flagged as below; on built models typically `Custom Length`, `Custom Rotation` or `Projected X` with a length near 0) are never displayed. On 7.8 they are left out of `projections` and the CSV and listed under each tag's `degenerate_projections` instead. The 8.1 output is unchanged and keeps them in `projections`.

## Output

JSON on stdout, or in the file given by `--out`. Top-level keys are `skill`, `version` (1), `input`, `u_value_basis`, `units`, `notes`, `cases` and `warnings`. Each case has:

- `case`: `U-factor` or `Condensation Resistance`. A case that THERM wrote with no tags and no nodes is left out.
- `simulation_error_pct`: THERM's error energy norm, in percent.
- `mesh_nodes`, and `temperature` with `tmin_c` and `tmax_c` over all nodes.
- `tags`: one entry per U-factor tag, each with:
  - `delta_t` and `heat_flux_w_m2`.
  - `projections`: each projection that has values, with `type`, `length_mm`, `u_factor` and `degenerate`. Empty projections are omitted. A projection is flagged degenerate when its length is under 0.001 mm or its U is over 1e6. THERM writes such values when a projection does not apply. On 7.8 these go to `degenerate_projections` instead (see above).
  - `displayed`: the U-factor THERM shows. It uses `DefaultDisplayType`. When that is `Unknown` or degenerate, it uses the model's `UValueBasis` setting, then the first usable projection in this order: glass plane, Y, X, total length. `source` says which rule picked it, and a warning goes to stderr.
  - `temperature`: `nodes`, `tmin_c`, `tmax_c` and `tmean_c` over the tag's nodes. **`tmean_c` is the plain average of node temperatures. It is not weighted by boundary length**, so it can differ from a length-weighted surface average.

`--csv <file>` also writes one row per tag projection, with the tag temperatures and the error norm on each row. The `displayed` column marks the displayed projection.

Exit codes: 0 success. 1 for a user or input error: the file is missing, is not a zip, or has no results (unsolved). The message names the missing entry. 2 when a result entry exists but cannot be parsed.

## Example

```
python scripts/therm_results.py "C:\THERM runs\sample-sill.thmz" --out sill.json --csv sill.csv
```

On LBNL's `sample-sill.thmz`, the U-factor case shows Frame U=2.0036, Edge U=2.3457 and SHGC Exterior U=1.8128 W/m2K (Projected Y), over 425 nodes.

## Limits

- Steady-state results only. A transient `.thmz` exits 1 with a message saying so.
- Units are as THERM stores them: mm, W/m2K, W/m2 and degrees C. Nothing is converted.
- The reader does not check whether results are stale, for example after the geometry was edited without a re-solve.
- Tested against THERM 8.1.30 files and LibraryFEMTherm samples. Later 8.1 builds may rename entries.
- 7.8: tested against THERM 7.8.80 files in `research/samples/therm78/` (see `PORT-78.md`). No error norm. Which projection equals the glass plane on jamb, head and divider sections is not checked.

Tests: run `python -m unittest` from this folder.
