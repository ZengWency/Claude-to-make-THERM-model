---
name: therm-library
description: Looks up LBNL THERM 7.8 or 8.1 materials, gases and boundary conditions by name (or UUID on 8.1), and builds a project library JSON from them. On 7.8 it lists the names in the binary .lib files the ini names and ships its own JSON records for writing inline into a .thmx. It reads the installed THERM lib folder, the copies embedded in a .thmz, or an earlier project JSON. It can add new solid materials and simplified or comprehensive BCs with fresh UUIDs, and it emits ready-made NFRC 100-2010 and ISO 10077-2 / EN ISO 6946 BC sets. Use when asked to "find a THERM material", "what conductivity does THERM use for", "list THERM boundary conditions", "which BCs are in this thmz", "add a material to THERM", "ISO 10077-2 boundary conditions", "Rsi 0.13 / Rse 0.04 in THERM", or "NFRC BCs for THERM". It never writes into the THERM lib folder and does not build or solve models.
---

# therm-library

Searches THERM 7.8 or 8.1 library records and writes project library JSON files. A therm-model spec can load these files as a reference source. It needs no THERM install and runs on Windows or Linux with Python 3.10+ and the standard library only.

## Contract

- **Version:** `--therm-version 7.8|8.1`. Without it: a `.thmx`, `.lib` or `.ini` source (or `--ini`) means 7.8; a `.thmz` or `.xml` source, or a lib folder of `.xml` files, means 8.1; a project `.json` (as `--source`, or an existing `--out`) keeps its own version; otherwise 7.8 when THERM 7.8 is installed, else 8.1.
- **Reads (8.1):** a THERM `lib` folder, a library `.xml`, a `.thmz` or a project `.json` (`--source`, `--lib`).
- **Reads (7.8):** the bundled records `assets/records-78.json`, a `.thmx` (its inline Material and BoundaryCondition elements), a project `.json`, and, names only, the binary `.lib` files named by the ini's `MaterialLibName`, `BCLibName` and `GasLibName` (`--ini`, default `C:\Users\Public\LBNL\Settings\therm7.8.ini`) or a `.lib` given with `--source`.
- **Writes:** a project library `.json` (`--out`); `list` and `find` print JSON to stdout. THERM's own library files are never written. 7.8 records follow `research/library-record-78.md`: `{"kind", "name", "thmx": {...}}`, where `thmx` holds THERM 7.8's attribute names and string values in THERM's order and `name` is normalised (`<stem>:` prefixes and `_Cavity_<n>` suffixes stripped).
- **Before:** therm-setup.
- **After:** therm-glazing `solid` on a spec (optional), then therm-model; both take the project `.json` with `--library`. On 7.8 they write each `thmx` record inline into the `.thmx`; an inline record wins over THERM's library.

## Inputs

`--source` accepts any of the following, and can be repeated. Without it, the script reads `--lib`, which defaults to `C:\Users\Public\LBNL\THERM8.1\lib`.

- A THERM lib folder holding `Materials.xml`, `Gases.xml`, and `BoundaryConditionsSteadyState.xml` (THERM 8.1.30) or `BoundaryConditions.xml` (newer builds, LibraryFEMTherm unified format). The script reads either BC file name.
- A `.thmz` file. The script reads the `Materials.xml`, `Gases.xml` and `SteadyStateBC.xml` copies embedded in it.
- A single library `.xml` file.
- A project `.json` file written by this script, or by hand in the same shape.

## Commands

| Command | What it does |
|---|---|
| `list` | Lists every record. `--kind material\|pure_gas\|gas\|bc` filters by kind. |
| `find TEXT` | Matches a case-insensitive substring of the name or UUID. `--full` adds each record's raw XML. |
| `export --uuid U --name N ... --out p.json` | Copies the chosen records into a project file. `--name` must match exactly. A cavity material also brings its gas, and a gas mixture brings its pure gases, because THERM resolves them by name. |
| `add material --name N --conductivity K [--emissivity E] [--emissivity-back E]` | Creates a solid material. Conductivity must be greater than 0 W/mK, and emissivities must be between 0 and 1 (default 0.9). |
| `add bc --name N --temperature T --film-coefficient H [--rh 0.5]` | Creates a simplified BC, where H is the combined film coefficient and 0 means adiabatic. Add `--comprehensive [--radiation automatic\|blackbody] [--emissivity E] [--rad-temperature T] [--view-factor F] [--flux Q]` for separate convection and radiation. |
| `presets nfrc` | Copies 10 BCs from the THERM lib by UUID: NFRC 100-2010 Exterior, Adiabatic, and the 8 interior frame BCs (convection only). |
| `presets iso [--exterior-temp 0] [--interior-temp 20]` | Generates 3 simplified BCs plus Adiabatic: Rse 0.04 (h 25), Rsi 0.13 normal (h 7.692308), Rsi 0.20 reduced radiation (h 5.0). |

`export`, `add` and `presets` merge into `--out` when the file exists. Records with the same kind and UUID are replaced. A new record whose name matches an existing record of the same kind but whose UUID differs is refused with exit 1, because THERM matches BCs by name. ISO preset UUIDs are fixed (uuid5), so you can re-run a preset safely. `--out` must be a `.json` file, and the script refuses to write into any folder that holds THERM library files.

## THERM 7.8

THERM 7.8 libraries are binary files that cannot be edited and whose values this script does not read. On 7.8:

- `list` and `find` show the bundled records (with values) and, with `library_only: true` and no values, every name in the libraries the ini names. Names come from both string encodings THERM uses (UTF-16LE in `material.lib` and `BC.lib`, 8-bit in `material-NFRC.lib` and the `-Arch` files).
- `export --name N` takes a name with or without a `<stem>:` prefix and writes the matching record. A name that exists only in a binary library is refused with exit 1: name it in the model with no inline record and THERM fills it from its library, or create it with `add`.
- `add material` writes a solid (`Type` 0). `add bc` writes a combined-film BC (`Type` 1, or `Type` 0 Adiabatic when h is 0); `--comprehensive` is 8.1 only.
- `presets nfrc` writes the 10 NFRC 100-2010 BCs: Exterior, Adiabatic and the 8 interior frame films, copied from `.thmx` files THERM 7.8.80 wrote (THERM's own spelling, including `Interior (20 tilt) Interior Wood/Vinyl Frame (convection only)`).
- `presets iso` writes the same 3 ISO films plus Adiabatic as 8.1, with the same names, values and `preset` engineer-check flags, as `Type` 1 combined films (`RadiationModel` 0, `Hr` 0) and `Type` 0 Adiabatic.
- Gases: 7.8 has no readable gas records; a 7.8 gap is a solid with an effective conductivity (contract section 4).
- 7.8 records carry no UUID, so merging matches by kind and name.

## Output: project library JSON

The same shape is used for stdout and for `--out`. therm-model's `Library.load` reads it as-is.

```json
{
  "skill": "therm-library", "version": 1, "format": "therm-library-project",
  "counts": {"materials": 1, "pure_gases": 0, "gases": 0, "bcs": 1},
  "materials": [ RECORD, ... ],
  "boundary_conditions": [ RECORD, ... ],
  "gases": [ RECORD, ... ],
  "warnings": []
}
```

`gases` holds both pure gases and mixtures. `find` and `list` also report `sources`, `query`, `kind` and `total` (the counts before filtering). Each RECORD looks like this:

```json
{
  "kind": "bc", "uuid": "1810f37a-de4d-4e2d-95d5-09fe01157c34", "name": "NFRC 100-2010 Exterior",
  "type": "comprehensive", "protected": true, "color": "0x0080C0", "igu_surface": false,
  "properties": {"relative_humidity": 0.5, "convection_temperature_c": -18.0, "film_coefficient_w_m2k": 26.0,
                 "flux_w_m2": 0.0, "radiation_model": "black_body", "radiation_temperature_c": -18.0,
                 "emissivity": 1.0, "view_factor": 1.0},
  "source": {"path": "...lib", "entry": "BoundaryConditionsSteadyState.xml"},
  "xml_format": "therm-8.1",
  "xml": "<BoundaryCondition>...</BoundaryCondition>"
}
```

- `kind` is one of `material`, `pure_gas`, `gas` (mixture) or `bc`.
- `type` depends on the kind:
  - Materials: `solid`, `cavity` or `radiation_enclosure`.
  - Gases: `pure` or `mixture`.
  - BCs: `simplified`, `comprehensive`, `radiation_surface`, plus `adiabatic` and `prescribed` (unified file only).
- `properties` uses SI units, with the unit in each key name:
  - Solid: `conductivity_w_mk`, `emissivity_front`, `emissivity_back`.
  - Cavity: `cavity_standard`, `gas`, `emissivity_side1`, `emissivity_side2`.
  - Pure gas: `conductivity_abc` and related coefficient lists.
  - Mixture: `components`.
  - Simplified BC: `temperature_c`, `film_coefficient_w_m2k`, `relative_humidity`, and `adiabatic` when h is 0.
  - Comprehensive BC: the keys shown in the example above.
- `xml` is the THERM record, ready to drop into a `.thmz` library entry. `xml_format` is `therm-8.1` or `libraryfemtherm-unified`. `export`, `add` and `presets` always include `xml`. `find` and `list` omit it unless you pass `--full`.
- Preset records also carry `preset: {set, needs_engineer_check, checks: [{field, value, source, confirmed}]}`.
- When a hand-written record has `xml` but no `properties`, the script parses it from the XML.

Exit codes: 0 success; 1 for an input error (missing source, bad argument, validation failure, name clash, refused output path); 2 when a THERM library XML exists but cannot be parsed.

## Example

```
python scripts/therm_library.py find "NFRC 100-2010" --kind bc
python scripts/therm_library.py export --name "Aluminum Alloy (painted)" --name "Frame Cavity NFRC 100" --out C:\proj\lib.json
python scripts/therm_library.py presets iso --out C:\proj\lib.json
python scripts/therm_library.py add material --name "PA66 GF25 thermal break" --conductivity 0.3 --out C:\proj\lib.json
python scripts/therm_library.py list --source C:\proj\lib.json
```

On the shipped 8.1 lib copies, `list` counts 102 materials, 4 pure gases, 5 gas mixtures and 14 BCs. On LBNL's `sample-sill.thmz`, it counts 8 materials, 1 pure gas, 1 gas mixture and 4 BCs, including `Sample GlzSys (ID:8) U-factor Inside Film`.

## Where the preset numbers come from

| Value | Source | Confirmed |
|---|---|---|
| NFRC 100-2010 set (Exterior −18 °C, h 26, black-body radiation; interior frame films at 21 °C) | Copied as-is from the THERM 8.1 shipped `BoundaryConditionsSteadyState.xml` | yes (LBNL library) |
| Rse = 0.04 m²K/W | ISO 6946:2017 Table 5, official preview ([iteh sample](https://cdn.standards.iteh.ai/samples/65708/fcbae03d539c493d89aa2b10bee7691c/ISO-6946-2017.pdf)) | yes for ISO 6946; ISO 10077-2 Annex E not read |
| Rsi = 0.13 m²K/W (normal) | ISO 6946:2017 Table 5 gives only the 0.1–0.2 range and points to 6.8, which is not in the preview | **engineer check** |
| Rsi = 0.20 m²K/W (reduced radiation/convection at edges and corners) | ISO 10077-2:2017 Annex E "Surface resistances", which is paywalled; only the table of contents was seen ([iteh sample](https://cdn.standards.iteh.ai/samples/64995/9230537d19e742a981d750afde502240/ISO-10077-2-2017.pdf)) | **engineer check** |
| 0 °C exterior / 20 °C interior | Common convention, not read from the standard. Uf and Ψ do not depend on these temperatures in a linear steady-state model; surface temperatures do. | **engineer check** |
| RH 0.5, Adiabatic record | THERM defaults and shipped record (same UUID) | yes |

The ISO BCs are simplified (combined) films with h = 1/R. Check them against a licensed copy of ISO 10077-2 before using them for certification work.

## Limits

- The script never writes into a THERM lib folder. To get project records into a model, use therm-model.
- `.thmz` files that store `.json` entry variants (LibraryFEMTherm) are not read. Only the `.xml` entries are read.
- `add` creates solid materials and steady-state BCs only. It does not create cavity materials, gases, transient BCs or radiation-surface BCs. Export those from a library instead.
- Transient BC files are not read.
- THERM 8.1.30 BC names contain the typo "Inerior"; the script keeps names verbatim. `presets nfrc` selects records by UUID, so a build with corrected names still works.
- Tested against the THERM 8.1.30 lib copies, LBNL `sample-sill.thmz`, and the LibraryFEMTherm v0.24.0 unified `BoundaryConditions.xml`. The exact BC file name in 8.1.68 is unknown.

- 7.8 bundled records cover the NFRC BC set and the solid and cavity materials of LBNL's sample sill. Other `material.lib` entries are names only.

Tests: run `python -m unittest discover -s tests` from this folder. `PORT-78.md` records the 7.8 port checks.
