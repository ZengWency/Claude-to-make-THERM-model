"""Read a solved LBNL THERM 8.1 .thmz or THERM 7.8 .thmx and emit its results as JSON (and optional CSV).

Standard library only. Reads XML entries (as THERM saves them) and the .json
entry variants that LibraryFEMTherm can write.

Exit codes: 0 success, 1 user/input error (missing file, not a .thmz, unsolved),
2 THERM result error (result entries present but unreadable).
"""
import argparse
import csv
import io
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

SKILL = "therm-results"
VERSION = 1

# Model.xml Preferences/Settings/UValueBasis -> SteadyStateResults Length-type.
BASIS_TO_LENGTH_TYPE = {
    "Total length": "Total Length",
    "X-projected": "Projected X",
    "Y-projected": "Projected Y",
    "Custom": "Custom Length",
    "Glass rotation projected": "Projected in glass plane",
    "Custom rotation projected": "Custom Rotation",
}
# Last-resort order when neither DefaultDisplayType nor UValueBasis gives a usable projection.
FALLBACK_ORDER = ["Projected in glass plane", "Projected Y", "Projected X", "Total Length"]
# THERM writes near-zero lengths (e.g. 3e-5 mm) with U around 1e7 when a projection
# does not apply; anything below these bounds is not a real result.
MIN_LENGTH_MM = 1e-3
MAX_U = 1e6


# THERM 7.8 .thmx: projection labels, matched case-insensitively, mapped to the 8.1 names above.
LABELS_78 = {
    "total length": "Total Length",
    "projected x": "Projected X",
    "projected y": "Projected Y",
    "custom length": "Custom Length",
    "projected in glass plane": "Projected in glass plane",
    "custom rotation": "Custom Rotation",
}
NS_78 = {"t": "http://windows.lbl.gov"}
GLASS_PLANE = "Projected in glass plane"
SEG_TOL_MM = 0.05  # a mesh node lies on a BC segment when it is within this distance


class UserError(Exception):
    pass


class ResultError(Exception):
    pass


def json_to_element(tag, value):
    """Turn a LibraryFEMTherm JSON entry into the same tree shape as its XML twin."""
    el = ET.Element(tag)
    if isinstance(value, dict):
        for key, child in value.items():
            for item in child if isinstance(child, list) else [child]:
                el.append(json_to_element(key, item))
    elif value is not None:
        el.text = str(value)
    return el


def load_entry(zf, names, base):
    """Return the root element of <base>.xml or <base>.json, or None if absent."""
    try:
        if base + ".xml" in names:
            return ET.fromstring(zf.read(base + ".xml"))
        if base + ".json" in names:
            data = json.loads(zf.read(base + ".json").decode("utf-8"))
            if not isinstance(data, dict) or len(data) != 1:
                raise ValueError("expected one top-level key")
            (root_tag, body), = data.items()
            return json_to_element(root_tag, body)
    except (ET.ParseError, ValueError) as exc:
        raise ResultError("cannot parse %s entry: %s" % (base, exc))
    return None


def num(text):
    if text is None or not text.strip():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_projection(proj):
    length = num(proj.findtext("Length"))
    u = num(proj.findtext("U-factor"))
    if length is None and u is None:
        return None  # THERM writes empty projections that do not apply; omit them
    degenerate = (length is None or u is None or not math.isfinite(u)
                  or length < MIN_LENGTH_MM or abs(u) > MAX_U)
    out = {"type": proj.findtext("Length-type"), "length_mm": length,
           "u_factor": u, "degenerate": degenerate}
    angle = num(proj.findtext("ProjectionAngle"))
    if angle is not None:
        out["angle_deg"] = angle
    return out


def pick_displayed(default_type, basis_type, projections):
    usable = {p["type"]: p for p in projections if not p["degenerate"]}
    for source, wanted in (("DefaultDisplayType", default_type), ("UValueBasis", basis_type)):
        if wanted in usable:
            return dict(usable[wanted], source=source)
    for wanted in FALLBACK_ORDER:
        if wanted in usable:
            return dict(usable[wanted], source="fallback")
    return None


def temp_stats(values):
    if not values:
        return None
    return {"nodes": len(values), "tmin_c": min(values), "tmax_c": max(values),
            "tmean_c": sum(values) / len(values)}


def read_thmz(path):
    try:
        zf = zipfile.ZipFile(path)
    except FileNotFoundError:
        raise UserError("file not found: %s" % path)
    except (zipfile.BadZipFile, IsADirectoryError, PermissionError) as exc:
        raise UserError("not a readable .thmz (zip) file: %s (%s)" % (path, exc))
    with zf:
        names = set(zf.namelist())
        results = load_entry(zf, names, "SteadyStateResults")
        if results is None:
            have_mesh = "Mesh.xml" in names or "Mesh.json" in names
            transient = any(n.startswith("transient results/") for n in names)
            hint = (" It holds transient results, which this skill does not read." if transient
                    else " It has a mesh but no solution; run THERM with -calc." if have_mesh
                    else " Run THERM with -calc to solve it.")
            raise UserError("%s has no results: SteadyStateResults.xml (or .json) is missing.%s"
                            % (path, hint))
        mesh = load_entry(zf, names, "SteadyStateMeshResults")
        model = load_entry(zf, names, "Model")
    basis = model.findtext("Preferences/Settings/UValueBasis") if model is not None else None
    basis_type = BASIS_TO_LENGTH_TYPE.get((basis or "").strip())

    temps_by_case, tag_nodes = {}, {}
    if mesh is not None:
        for case in mesh.findall("Case"):
            nodes = case.find("Nodes")
            temps = {}
            for n in (nodes if nodes is not None else []):
                idx, t = n.findtext("Index"), num(n.findtext("Temperature"))
                if idx is not None and t is not None:
                    temps[int(float(idx))] = t
            temps_by_case[case.findtext("ResultsType")] = temps
        for tnc in mesh.findall("TagNodesCase"):
            tags = {}
            for tag in tnc.findall("Tag"):
                tags[tag.findtext("Name")] = [int(float(n.text)) for n in tag.findall("Nodes/Node")
                                              if n.text and n.text.strip()]
            tag_nodes[tnc.findtext("ResultsType")] = tags

    cases, warnings = [], []
    if mesh is None:
        warnings.append("SteadyStateMeshResults entry missing: no temperatures reported")
    for case in results.findall("Case"):
        name = case.findtext("ModelType")
        uf_elems = case.findall("U-factors")
        temps = temps_by_case.get(name, {})
        if not uf_elems and not temps:
            continue  # e.g. Condensation Resistance case written but not run
        tags = []
        for uf in uf_elems:
            tag = uf.findtext("Tag")
            projections = [p for p in (read_projection(e) for e in uf.findall("Projection")) if p]
            default_type = (uf.findtext("DefaultDisplayType") or "").strip()
            displayed = pick_displayed(default_type, basis_type, projections)
            if displayed is None:
                warnings.append("%s / %s: no usable projection" % (name, tag))
            elif displayed["source"] != "DefaultDisplayType":
                warnings.append("%s / %s: DefaultDisplayType is %r; showing %s (from %s)"
                                % (name, tag, default_type, displayed["type"], displayed["source"]))
            ids = tag_nodes.get(name, {}).get(tag, [])
            tags.append({
                "tag": tag, "delta_t": num(uf.findtext("DeltaT")),
                "heat_flux_w_m2": num(uf.findtext("HeatFlux")),
                "default_display_type": default_type, "displayed": displayed,
                "projections": projections,
                "temperature": temp_stats([temps[i] for i in ids if i in temps]),
            })
        cases.append({
            "case": name, "simulation_error_pct": num(case.findtext("SimulationError")),
            "mesh_nodes": len(temps), "temperature": temp_stats(list(temps.values())),
            "tags": tags,
        })
    if not any(c["tags"] for c in cases):
        raise UserError("%s has a SteadyStateResults entry but no U-factor tags in any case; "
                        "the model has no U-factor surface tags or was not solved" % path)
    return {
        "skill": SKILL, "version": VERSION, "input": path,
        "u_value_basis": basis,
        "units": {"length": "mm", "u_factor": "W/m2K", "heat_flux": "W/m2", "temperature": "C"},
        "notes": ["tmean_c is the plain average of node temperatures, not length-weighted"],
        "cases": cases, "warnings": warnings,
    }


def normalise_name(name):
    """THERM 7.8 name rule, copied from the frozen contract research/library-record-78.md
    section 3 (no cross-skill imports): strip every leading '<token>:' prefix (token has no
    colon and no whitespace) and every trailing '_Cavity_<n>' suffix, then trim."""
    name = re.sub(r"^(?:[^\s:]+:)+", "", name.strip())
    return re.sub(r"(?:_Cavity_\d+)+$", "", name).strip()


def _f(el, attr):
    return num(el.get(attr)) if el is not None else None


def _on_seg(p, a, b, tol=SEG_TOL_MM):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    cx, cy = a[0] + t * dx, a[1] + t * dy
    return (p[0] - cx) ** 2 + (p[1] - cy) ** 2 <= tol * tol


def read_projection_78(proj):
    label = (proj.findtext("t:Length-type", namespaces=NS_78) or "").strip()
    length = _f(proj.find("t:Length", NS_78), "value")
    u = _f(proj.find("t:U-factor", NS_78), "value")  # "NA" -> None
    if length is None and u is None:
        return None
    degenerate = (length is None or u is None or not math.isfinite(u)
                  or length < MIN_LENGTH_MM or abs(u) > MAX_U)
    out = {"type": LABELS_78.get(label.lower(), label), "length_mm": length,
           "u_factor": u, "degenerate": degenerate}
    angle = num(proj.findtext("t:ProjectionAngle", namespaces=NS_78))
    if angle is not None:
        out["angle_deg"] = math.degrees(angle)  # 7.8 stores radians
    return out


def tag_temperatures_78(root, case_type):
    """Per-tag and global node temperatures for one case, from Mesh + MeshResults.

    Centred-origin rule (research/therm78-interface.md 3.3): model mm = mesh node m * 1000 +
    centre of the bounding box of all polygon points. Surface nodes are the convection-BC nodes;
    a node belongs to a tag when it lies on a BCPolygon segment carrying that UFactorTag.
    Returns (temps by node index, {tag: [node ids]}, (nodes on any segment, surface nodes)) or None.
    """
    mesh_in = None
    for m in root.findall("t:Mesh/t:MeshInput", NS_78):
        if (m.findtext("t:ModelType", namespaces=NS_78) or "").strip() == case_type:
            mesh_in = m
    case = None
    for c in root.findall("t:Results/t:Case", NS_78):
        if (c.findtext("t:ModelType", namespaces=NS_78) or "").strip() == case_type:
            case = c
    if mesh_in is None or case is None:
        return None
    temps = {}
    for n in case.findall("t:MeshResults/t:NodesOutput/t:NodeResults", NS_78):
        t = num(n.get("temperature"))
        if n.get("index") is not None and t is not None:
            temps[int(n.get("index"))] = t
    if not temps:
        return None
    pts = [(float(p.get("x")), float(p.get("y"))) for p in root.findall("t:Polygons/t:Polygon/t:Point", NS_78)]
    if not pts:
        return None
    cx = (min(x for x, _ in pts) + max(x for x, _ in pts)) / 2
    cy = (min(y for _, y in pts) + max(y for _, y in pts)) / 2
    xy = {int(n.get("index")): (float(n.get("x")) * 1000 + cx, float(n.get("y")) * 1000 + cy)
          for n in mesh_in.findall("t:Nodes/t:Node", NS_78)}
    segs = []
    for b in root.findall("t:Boundaries/t:BCPolygon", NS_78):
        q = [(float(e.get("x")), float(e.get("y"))) for e in b.findall("t:Point", NS_78)]
        if len(q) >= 2:
            segs.append(((b.get("UFactorTag") or "").strip(), q[0], q[1]))
    surf = set()
    for c in mesh_in.findall("t:BCInputs/t:ConvectionBCInput", NS_78):
        surf.update(int(c.get(k)) for k in ("node1", "node2") if c.get(k) is not None)
    per, on_any = {}, 0
    for n in sorted(surf):
        if n not in xy:
            continue
        hit = False
        for name, a, b in segs:
            if _on_seg(xy[n], a, b):
                hit = True
                if name:
                    per.setdefault(name, set()).add(n)
        on_any += hit
    return temps, {k: sorted(v) for k, v in per.items()}, (on_any, len(surf))


def read_thmx(path):
    """Read a solved THERM 7.8 .thmx. Output keeps the 8.1 JSON shape, plus 7.8-only keys."""
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        raise UserError("file not found: %s" % path)
    except (ET.ParseError, IsADirectoryError, PermissionError) as exc:
        raise UserError("not a readable .thmx (XML) file: %s (%s)" % (path, exc))
    results = root.find("t:Results", NS_78)
    if results is None:
        raise UserError("%s has no results: the .thmx has no <Results> block. Solve it with THERM 7.8 "
                        "-calc and SaveXML=1 (therm-run does this)." % path)
    section = (root.findtext("t:CrossSectionType", namespaces=NS_78) or "").strip()
    version = (root.findtext("t:ThermVersion", namespaces=NS_78) or "").strip()
    is_sill = section.lower() == "sill"
    cases, warnings = [], []
    if not is_sill:
        warnings.append("cross-section is %r: on THERM 7.8 the glass-plane projection is unreliable "
                        "and which projection equals it was checked only for a Sill. Projected Y is "
                        "shown; check it before use." % (section or "unknown"))
    have_mesh = root.find("t:Mesh", NS_78) is not None
    if not have_mesh:
        warnings.append("no <Mesh> in the .thmx: no temperatures reported (solve with ExportMeshToTHMX=1)")
    for case in results.findall("t:Case", NS_78):
        name = (case.findtext("t:ModelType", namespaces=NS_78) or "").strip()
        uf_elems = case.findall("t:U-factors", NS_78)
        mesh = tag_temperatures_78(root, name) if have_mesh else None
        temps, tag_nodes = (mesh[0], mesh[1]) if mesh else ({}, {})
        if not uf_elems and not temps:
            continue
        if mesh and mesh[2][0] != mesh[2][1]:
            warnings.append("%s: only %d of %d convection-BC mesh nodes lie on a BC segment; the "
                            "centred-origin rule may not hold for this model, so tag temperatures "
                            "may be incomplete" % (name, mesh[2][0], mesh[2][1]))
        tags = []
        for uf in uf_elems:
            tag = (uf.findtext("t:Tag", namespaces=NS_78) or "").strip()
            all_proj = [p for p in (read_projection_78(e) for e in uf.findall("t:Projection", NS_78)) if p]
            # 7.8: the glass-plane value falls back to Projected X after a .thmx import, so it is
            # never shown or offered (research/round2-plan-review.md 1.1).
            # Degenerate ones (e.g. Custom Rotation with length ~0 on built models) are never shown,
            # so they go to degenerate_projections and stay out of `projections` and the CSV.
            kept = [p for p in all_proj if p["type"] != GLASS_PLANE]
            projections = [p for p in kept if not p["degenerate"]]
            degenerate_projections = [p for p in kept if p["degenerate"]]
            usable = {p["type"]: p for p in projections}
            displayed = None
            for wanted in ("Projected Y", "Projected X", "Total Length"):
                if wanted in usable:
                    displayed = dict(usable[wanted], source="7.8-sill-rule" if is_sill and wanted == "Projected Y"
                                     else "7.8-fallback")
                    break
            if displayed is None:
                warnings.append("%s / %s: no usable projection" % (name, tag))
            elif displayed["type"] != "Projected Y":
                warnings.append("%s / %s: Projected Y unusable; showing %s" % (name, tag, displayed["type"]))
            dt = _f(uf.find("t:DeltaT", NS_78), "value")
            ref = usable.get("Total Length") or next(iter(usable.values()), None)
            q = ref["u_factor"] * ref["length_mm"] / 1000.0 * dt if ref and dt is not None else None
            ids = tag_nodes.get(tag, [])
            tags.append({
                "tag": tag, "delta_t": dt, "heat_flux_w_m2": q,
                "heat_flux_source": "derived: U x length x DeltaT of %s (W per m depth, unsigned)"
                                    % ref["type"] if ref else None,
                "default_display_type": "", "displayed": displayed, "projections": projections,
                "degenerate_projections": degenerate_projections,
                "temperature": temp_stats([temps[i] for i in ids if i in temps]),
            })
        cavities = [{"name": c.get("Name"), "base_name": normalise_name(c.get("Name") or ""),
                     "conductivity_w_mk": num(c.get("Conductivity"))}
                    for c in case.findall("t:FrameCavities/t:FrameCavity", NS_78)]
        out = {"case": name, "simulation_error_pct": None,
               "mesh_nodes": len(temps), "temperature": temp_stats(list(temps.values())),
               "tags": tags, "frame_cavities": cavities}
        if mesh:
            out["surface_nodes_on_segments"] = "%d/%d" % mesh[2]
        cases.append(out)
    if not any(c["tags"] for c in cases):
        raise UserError("%s has a <Results> block but no U-factor tags in any case; the model has no "
                        "U-factor surface tags or was not solved" % path)
    return {
        "skill": SKILL, "version": VERSION, "input": path,
        "u_value_basis": None, "therm_format": "thmx-7.8", "therm_version": version,
        "cross_section": section,
        "units": {"length": "mm", "u_factor": "W/m2K", "heat_flux": "W/m2", "temperature": "C"},
        "notes": ["tmean_c is the plain average of node temperatures, not length-weighted",
                  "error energy norm: unavailable from a THERM 7.8 .thmx (simulation_error_pct is null)",
                  "'Projected in glass plane' is omitted: on 7.8 it falls back to Projected X after a "
                  ".thmx import. Projected Y is the displayed value (checked for a Sill only).",
                  "heat_flux_w_m2 is derived as U x length x DeltaT in W per metre of depth, unsigned; "
                  "a 7.8 .thmx stores no heat flux"],
        "cases": cases, "warnings": warnings,
    }


def read_results(path):
    """Pick the reader by extension: .thmx = THERM 7.8, anything else = 8.1 .thmz."""
    if path.lower().endswith(".thmx"):
        return read_thmx(path)
    return read_thmz(path)


CSV_FIELDS = ["case", "tag", "delta_t", "heat_flux_w_m2", "projection", "length_mm",
              "u_factor", "degenerate", "displayed", "tag_nodes", "tmin_c", "tmax_c",
              "tmean_c", "simulation_error_pct"]


def to_csv(result):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for case in result["cases"]:
        for tag in case["tags"]:
            t = tag["temperature"] or {}
            shown = (tag["displayed"] or {}).get("type")
            for p in tag["projections"]:
                writer.writerow({
                    "case": case["case"], "tag": tag["tag"], "delta_t": tag["delta_t"],
                    "heat_flux_w_m2": tag["heat_flux_w_m2"], "projection": p["type"],
                    "length_mm": p["length_mm"], "u_factor": p["u_factor"],
                    "degenerate": p["degenerate"], "displayed": p["type"] == shown,
                    "tag_nodes": t.get("nodes"), "tmin_c": t.get("tmin_c"),
                    "tmax_c": t.get("tmax_c"), "tmean_c": t.get("tmean_c"),
                    "simulation_error_pct": case["simulation_error_pct"],
                })
    return buf.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="therm-results",
        description="Read U-factors, projections and tag temperatures from a solved THERM .thmz (8.1) or .thmx (7.8).")
    ap.add_argument("input", help="solved .thmz (THERM 8.1) or .thmx (THERM 7.8) file")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--csv", help="also write a flat CSV table (one row per tag projection)")
    args = ap.parse_args(argv)
    try:
        result = read_results(args.input)
    except UserError as exc:
        print("therm-results: error: %s" % exc, file=sys.stderr)
        return 1
    except ResultError as exc:
        print("therm-results: result error: %s" % exc, file=sys.stderr)
        return 2
    for w in result["warnings"]:
        print("therm-results: warning: %s" % w, file=sys.stderr)
    text = json.dumps(result, indent=2)
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        else:
            print(text)
        if args.csv:
            with open(args.csv, "w", encoding="utf-8", newline="") as fh:
                fh.write(to_csv(result))
    except OSError as exc:
        print("therm-results: error: cannot write output: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
