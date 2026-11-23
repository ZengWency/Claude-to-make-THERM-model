"""Show, patch or replace the glazing system (IGU) in an LBNL THERM 8.1 .thmz or a 7.8 .thmx.

On THERM 7.8 (.thmx) only show and solid (equivalent solid, option C) work: a .thmx stores no IGU
layer data, so there is no Condensation Resistance case and no glass-plane projection.

show   lists each GlazingSystem in Model.xml: layers, gaps, films, U-value, sight line,
       edge-of-glass height, and the polygons that reference it.
patch  (research option A) edits a GUI-imported IGU in place: layer and gap values, films,
       U-value, name. Thickness changes move the glass and gap polygon points; frame
       polygons stay put unless listed in also_shift, and a collision is refused.
solid  (research option C, ISO 10077-2 style) turns the IGU into plain Material polygons
       (glass panes, gaps as solids with an effective conductivity) or an ISO calibration
       panel; or fills a rectangular region of a therm-model spec JSON the same way.

Standard library only. Exit codes: 0 success, 1 input error, 2 a THERM entry exists but
cannot be parsed (or an internal consistency check failed).
"""
import argparse
import copy
import json
import math
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

SKILL = "therm-glazing"
VERSION = 1
RESULT_ENTRIES = ("Mesh.xml", "SteadyStateResults.xml", "SteadyStateMeshResults.xml",
                  "Mesh.json", "SteadyStateResults.json", "SteadyStateMeshResults.json")
GLASS_K, GLASS_E = 1.0, 0.84          # ISO 10077-2 glass; THERM's WINDOW import uses the same
PANEL_K = 0.035                       # ISO 10077-2 calibration panel
EPS = 1e-4                            # mm; geometric tolerance for overlap and contact tests
UUID_NS = uuid.UUID("3f6c1e52-8a0d-4f7b-9d21-6b0e4c2a7f13")
ENV = {"winter": "Winter", "summer": "Summer"}


class InputError(Exception):
    pass


class ThermError(Exception):
    pass


# ---------------------------------------------------------------- numbers and XML

def fmt(v):
    """THERM style: integers without '.0', else up to 6 decimals."""
    v = round(float(v), 6)
    return str(int(v)) if v == int(v) else repr(v)


def fnum(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def set_text(parent, path, value):
    el = parent.find(path)
    if el is None:
        raise ThermError("expected <%s> under <%s>" % (path, parent.tag))
    el.text = value if isinstance(value, str) else fmt(value)


def serialize(root):
    # Keep the layout of untouched elements and write <a></a> for empty ones, as THERM does.
    return ET.tostring(root, encoding="unicode", short_empty_elements=False).encode("utf-8") + b"\n"


def append_indented(parent, child, level):
    """Append child with tab indentation that matches a THERM-saved file."""
    child = copy.deepcopy(child)
    ET.indent(child, space="\t", level=level)
    if len(parent):
        parent[-1].tail = "\n" + "\t" * level
    else:
        parent.text = "\n" + "\t" * level
    child.tail = "\n" + "\t" * (level - 1)
    parent.append(child)


def clean_el(xml):
    el = ET.fromstring(xml) if isinstance(xml, (str, bytes)) else copy.deepcopy(xml)
    for e in el.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        e.tail = None
    return el


# ---------------------------------------------------------------- zip I/O

def read_thmz(path):
    if not os.path.isfile(path):
        raise InputError("file not found: %s" % path)
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            data = {i.filename: z.read(i.filename) for i in infos}
    except zipfile.BadZipFile as exc:
        raise InputError("not a readable .thmz (zip) file: %s (%s)" % (path, exc))
    if "Model.xml" not in data:
        if "Model.json" in data:
            # shortcut: LibraryFEMTherm .json entry variants are not edited; resave in THERM as XML.
            raise InputError("%s stores .json entries; this skill reads and writes .xml entries only" % path)
        raise InputError("%s has no Model.xml entry" % path)
    return infos, data


def parse(data, name):
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ThermError("cannot parse %s: %s" % (name, exc))


def write_thmz(path, infos, data, drop=()):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        names = [i.filename for i in infos]
        for name in names + [n for n in data if n not in names]:
            if name in drop or name not in data:
                continue
            info = next((i for i in infos if i.filename == name), None)
            if info is None:
                info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data[name])
    os.replace(tmp, path)


# ---------------------------------------------------------------- model access

def polygons(model):
    return list(model.find("Polygons") if model.find("Polygons") is not None else [])


def boundaries(model):
    return list(model.find("Boundaries") if model.find("Boundaries") is not None else [])


def systems(model):
    gs = model.find("GlazingSystems")
    return list(gs) if gs is not None else []


def poly_points(poly):
    return [(float(p.findtext("x")), float(p.findtext("y"))) for p in poly.findall("Points/Point")]


def poly_ref(poly):
    g = poly.find("GlazingSystem")
    if g is None:
        return None
    return (g.findtext("ID") or "").strip(), int(g.findtext("Index") or 0)


def is_gap(poly):
    return "Gap" in (poly.findtext("Type") or "")


def system_polys(model, gs):
    """Polygons of one glazing system. In THERM 8.1.30 files the polygon's GlazingSystem/ID is the
    system's <Index> (sample-sill: system ID 8, Index 1, polygons ID 1); fall back to <ID>."""
    refs = [poly_ref(p) for p in polygons(model)]
    idx, gid = (gs.findtext("Index") or "").strip(), (gs.findtext("ID") or "").strip()
    known_idx = {(s.findtext("Index") or "").strip() for s in systems(model)}
    out = []
    for p, r in zip(polygons(model), refs):
        if r is None:
            continue
        if r[0] == idx or (r[0] not in known_idx and r[0] == gid):
            out.append(p)
    return out


def select_system(model, sel):
    found = systems(model)
    if not found:
        raise InputError("the file has no glazing system (GlazingSystems is empty)")
    if sel is None:
        if len(found) > 1:
            raise InputError("the file has %d glazing systems; set \"glazing_system\" to an Index, ID or name: %s"
                             % (len(found), [(s.findtext("Index"), s.findtext("ID"), s.findtext("Name")) for s in found]))
        return found[0]
    s = str(sel)
    for key in ("Index", "ID", "Name"):
        hits = [g for g in found if (g.findtext(key) or "").strip() == s]
        if hits:
            return hits[0]
    raise InputError("no glazing system with Index, ID or name %r" % sel)


def layer_poly_map(model, gs):
    layers, gaps = {}, {}
    for p in system_polys(model, gs):
        (gaps if is_gap(p) else layers)[poly_ref(p)[1]] = p
    return layers, gaps


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def layout(model, gs):
    """Thickness axis (0=x, 1=y) and the sign of the interior direction along it."""
    layers, gaps = layer_poly_map(model, gs)
    if 0 not in layers:
        raise InputError("glazing system %s has no glass polygon for layer 0" % gs.findtext("Name"))
    x0, x1, y0, y1 = bbox(poly_points(layers[0]))
    axis = 0 if (x1 - x0) < (y1 - y0) else 1
    c0 = ((x0 + x1) / 2, (y0 + y1) / 2)[axis]
    last = max(layers)
    if last > 0:
        b = bbox(poly_points(layers[last]))
        cl = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2)[axis]
        return axis, (1 if cl > c0 else -1)
    # single pane: the interior IGU film boundary is on the interior face
    names = {interior_bc_name(gs)}
    uuid0 = layers[0].findtext("UUID")
    for b in boundaries(model):
        if b.findtext("Name") in names and uuid0 in [e.text for e in b if e.tag.startswith("NeighborPolygonUUID")]:
            face = float(b.findtext("StartPoint/" + "xy"[axis]))
            return axis, (1 if face > c0 else -1)
    raise InputError("cannot tell which side is interior for single-pane system %s" % gs.findtext("Name"))


def extent(poly, axis):
    b = bbox(poly_points(poly))
    return (b[0], b[1]) if axis == 0 else (b[2], b[3])


def interior_bc_name(gs):
    return (gs.findtext("InteriorBC") or "").strip()


def find_igu_bc(bcs_root, gs):
    """The IGU-surface BC. InteriorBC is '<project>:<name>'; SteadyStateBC.xml stores the name and
    ProjectNameTag separately, and Boundaries use the bare name."""
    want = interior_bc_name(gs)
    if bcs_root is None or not want:
        return None
    hits = []
    for bc in bcs_root.findall("BoundaryCondition"):
        name, tag = bc.findtext("Name") or "", bc.findtext("ProjectNameTag") or ""
        if want in (name, "%s:%s" % (tag, name)) or want.split(":", 1)[-1] == name:
            hits.append(bc)
    hits.sort(key=lambda bc: (bc.findtext("IGUSurface") or "").strip() != "true")
    return hits[0] if hits else None


def coeffs(el):
    return {k.lower(): fnum(el.findtext(k)) for k in ("H", "Hc", "Hr", "Temperature")} if el is not None else None


def bc_summary(bc):
    if bc is None:
        return None
    conv = bc.find("Comprehensive/Convection")
    simp = bc.find("Simplified")
    src = conv if conv is not None else simp
    return {"name": bc.findtext("Name"), "uuid": bc.findtext("UUID"),
            "project_name_tag": bc.findtext("ProjectNameTag"),
            "igu_surface": (bc.findtext("IGUSurface") or "").strip() == "true",
            "type": "comprehensive" if conv is not None else "simplified" if simp is not None else None,
            "film_coefficient_w_m2k": fnum(src.findtext("FilmCoefficient")) if src is not None else None,
            "temperature_c": fnum(src.findtext("Temperature")) if src is not None else None}


def describe(model, gs, bcs_root, tol):
    layers_p, gaps_p = layer_poly_map(model, gs)
    warnings = []
    try:
        axis, sign = layout(model, gs)
    except InputError as exc:
        axis, sign = None, None
        warnings.append(str(exc))
    layers, gaps = [], []
    for i, l in enumerate(gs.findall("Layers/Layer")):
        layers.append({"index": i, "id": l.findtext("ID"), "name": l.findtext("Name"),
                       "thickness_mm": fnum(l.findtext("Thickness")),
                       "conductivity_w_mk": fnum(l.findtext("Conductivity")),
                       "surface1": {"temperature_c": fnum(l.findtext("Surface1/Temperature")),
                                    "emissivity": fnum(l.findtext("Surface1/Emissivity"))},
                       "surface2": {"temperature_c": fnum(l.findtext("Surface2/Temperature")),
                                    "emissivity": fnum(l.findtext("Surface2/Emissivity"))}})
    for i, g in enumerate(gs.findall("Gases/Gas")):
        gaps.append({"index": i, "id": g.findtext("ID"), "name": g.findtext("Name"),
                     "thickness_mm": fnum(g.findtext("Thickness")),
                     "conductivity_w_mk": fnum(g.findtext("Conductivity")),
                     "components": [{"gas": c.findtext("Properties/Name"), "fraction": fnum(c.findtext("Fraction"))}
                                    for c in g.findall("Components/Component")],
                     "cr_bc_name": g.findtext("CRProperties/BCName")})
    props = []
    for p in gs.findall("GlazingSystemProperties/Property"):
        props.append({"environment": p.findtext("EnvironmentalCondition"),
                      "bc_style": p.findtext("BoundaryConditionStyle"),
                      "u_value_w_m2k": fnum(p.findtext("U-value")),
                      "exterior": coeffs(p.find("Coefficients/Exterior")),
                      "interior": coeffs(p.find("Coefficients/Interior")),
                      "solar": fnum(p.findtext("Solar"))})
    polys = []
    for p in system_polys(model, gs):
        kind, idx = ("gap" if is_gap(p) else "layer"), poly_ref(p)[1]
        rec = {"id": p.findtext("ID"), "uuid": p.findtext("UUID"), "type": p.findtext("Type"),
               "element": kind, "index": idx, "cavity_uuid": p.findtext("CavityUUID")}
        if axis is not None:
            lo, hi = extent(p, axis)
            rec["geometric_thickness_mm"] = round(hi - lo, 6)
            stored = (layers if kind == "layer" else gaps)[idx]["thickness_mm"] if idx < len(
                layers if kind == "layer" else gaps) else None
            if stored is not None and abs((hi - lo) - stored) > tol:
                warnings.append("polygon %s (%s %d) is %.6f mm thick but the IGU stores %s mm"
                                % (rec["id"], kind, idx, hi - lo, stored))
        polys.append(rec)
    for kind, have, want in (("layer", layers_p, layers), ("gap", gaps_p, gaps)):
        missing = [i for i in range(len(want)) if i not in have]
        if missing:
            warnings.append("no polygon for %s index %s" % (kind, missing))
    total = sum(x["thickness_mm"] or 0 for x in layers + gaps)
    return {
        "index": gs.findtext("Index"), "id": gs.findtext("ID"), "name": gs.findtext("Name"),
        "source": gs.findtext("Source"),
        "total_thickness_mm": round(total, 6),
        "layers": layers, "gaps": gaps, "properties": props,
        "actual_height_mm": fnum(gs.findtext("ActualHeight")),
        "orientation": gs.findtext("Orientation"),
        "is_nominal_thickness": (gs.findtext("IsNominalThickness") or "").strip() == "true",
        "sight_line_height_mm": fnum(gs.findtext("SiteLineHeight")),
        "eog_height_mm": fnum(gs.findtext("EOGHeight")),
        "edge_glass_tag": gs.findtext("EdgeGlassTag"),
        "interior_bc": interior_bc_name(gs), "exterior_bc": gs.findtext("ExteriorBC"),
        "igu_bc": bc_summary(find_igu_bc(bcs_root, gs)),
        "thickness_axis": None if axis is None else "xy"[axis],
        "interior_direction": None if axis is None else ("+" if sign > 0 else "-") + "xy"[axis],
        "polygons": polys,
        "warnings": warnings,
    }


def float_tol(model):
    return max(fnum(model.findtext("Properties/Miscellaneous/FloatTolerance")) or 0.02, 0.02)


def unresolved_materials(model, mats_root):
    """Material polygons whose MaterialUUID/MaterialName is not in Materials.xml, plus glazing
    polygons that point at a missing glazing system."""
    by_uuid = {(m.findtext("UUID") or "").strip(): (m.findtext("Name") or "") for m in mats_root.findall("Material")}
    names = set(by_uuid.values())
    sys_keys = {(s.findtext("Index") or "").strip() for s in systems(model)} | \
               {(s.findtext("ID") or "").strip() for s in systems(model)}
    bad = []
    for p in polygons(model):
        ref = poly_ref(p)
        if ref is not None:
            if ref[0] not in sys_keys:
                bad.append({"id": p.findtext("ID"), "problem": "glazing system %s not found" % ref[0]})
            continue
        u, n = (p.findtext("MaterialUUID") or "").strip(), (p.findtext("MaterialName") or "").strip()
        if n not in names and n.split(":", 1)[-1] in names:
            n = n.split(":", 1)[-1]  # THERM may save project records as '<project>:<name>'
        if u not in by_uuid and n not in names:
            bad.append({"id": p.findtext("ID"), "problem": "material %r / %s not in Materials.xml" % (n, u)})
        elif u in by_uuid and by_uuid[u] != n:
            bad.append({"id": p.findtext("ID"), "problem": "UUID %s is %r in Materials.xml, polygon says %r"
                        % (u, by_uuid[u], n)})
    return bad


# ---------------------------------------------------------------- geometry

def orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def edges(pts):
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def strictly_inside(p, pts):
    if any(seg_dist(p, a, b) <= EPS for a, b in edges(pts)):
        return False
    inside = False
    for a, b in edges(pts):
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return inside


def proper_cross(a, b, c, d):
    def side(o):
        return 0 if abs(o) <= EPS * 10 else (1 if o > 0 else -1)
    s1, s2 = side(orient(a, b, c)), side(orient(a, b, d))
    s3, s4 = side(orient(c, d, a)), side(orient(c, d, b))
    return s1 * s2 < 0 and s3 * s4 < 0


def overlap(A, B):
    """True when two polygons share interior area. Touching edges and vertices do not count.
    shortcut: samples vertices, edge midpoints and the centroid; exact for the rectangles and
    convex-ish profiles of glazing pockets, could miss a thin sliver between concave shapes."""
    a, b = bbox(A), bbox(B)
    if a[1] <= b[0] + EPS or b[1] <= a[0] + EPS or a[3] <= b[2] + EPS or b[3] <= a[2] + EPS:
        return False
    if any(proper_cross(e[0], e[1], f[0], f[1]) for e in edges(A) for f in edges(B)):
        return True
    for P, Q in ((A, B), (B, A)):
        samples = list(P) + [((e[0][0] + e[1][0]) / 2, (e[0][1] + e[1][1]) / 2) for e in edges(P)]
        c = (sum(p[0] for p in P) / len(P), sum(p[1] for p in P) / len(P))
        if strictly_inside(c, P):
            samples.append(c)
        if any(strictly_inside(s, Q) for s in samples):
            return True
    return False


def contact(A, B):
    """Length of boundary that polygons A and B share (collinear overlapping edges)."""
    total = 0.0
    for a, b in edges(A):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L == 0:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        for c, d in edges(B):
            if abs(orient(a, b, c)) / L > EPS * 10 or abs(orient(a, b, d)) / L > EPS * 10:
                continue
            tc = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
            td = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
            total += max(0.0, min(L, max(tc, td)) - max(0.0, min(tc, td)))
    return total


# ---------------------------------------------------------------- library lookups (gases, BCs)

def library_elements(paths):
    """Records from --library sources: a therm-library project .json, a .thmz, a library .xml or a
    THERM lib folder. Returns {tag: [Element]} for PureGas, Gas and BoundaryCondition."""
    out = {"PureGas": [], "Gas": [], "BoundaryCondition": [], "Material": []}

    def take(root):
        for el in root:
            if el.tag in out:
                out[el.tag].append(el)
    for path in paths or []:
        if os.path.isdir(path):
            for f in ("Gases.xml", "BoundaryConditionsSteadyState.xml", "BoundaryConditions.xml"):
                fp = os.path.join(path, f)
                if os.path.isfile(fp):
                    with open(fp, "rb") as fh:
                        take(parse(fh.read(), fp))
        elif not os.path.isfile(path):
            raise InputError("library not found: %s" % path)
        elif path.lower().endswith(".thmz"):
            _, data = read_thmz(path)
            for f in ("Gases.xml", "SteadyStateBC.xml", "Materials.xml"):
                if f in data:
                    take(parse(data[f], "%s:%s" % (path, f)))
        elif path.lower().endswith(".json"):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError) as exc:
                raise InputError("cannot read library JSON %s: %s" % (path, exc))
            for group in ("gases", "boundary_conditions", "materials"):
                for r in doc.get(group) or []:
                    if isinstance(r, dict) and r.get("xml"):
                        take(parse("<R>%s</R>" % r["xml"], path))
        else:
            with open(path, "rb") as fh:
                take(parse(fh.read(), path))
    return out


def gas_components(spec, pool, where):
    """Component list [(fraction, PureGas element)] for a gas name or [{gas, fraction}]."""
    pure = {(e.findtext("Name") or "").strip(): e for e in pool["PureGas"]}
    mixes = {(e.findtext("Name") or "").strip(): e for e in pool["Gas"]}
    if isinstance(spec, str):
        if spec in pure:
            return [(1.0, pure[spec])]
        if spec in mixes:
            spec = [{"gas": c.findtext("PureGas"), "fraction": fnum(c.findtext("Fraction"))}
                    for c in mixes[spec].findall("Components/Component")]
        else:
            raise InputError("%s: gas %r not found in the file's Gases.xml or --library (have %s)"
                             % (where, spec, sorted(set(pure) | set(mixes))))
    if not isinstance(spec, list) or not spec:
        raise InputError("%s: gas must be a name or a list of {gas, fraction}" % where)
    comps = []
    for c in spec:
        if not isinstance(c, dict) or c.get("gas") not in pure:
            raise InputError("%s: pure gas %r not found in Gases.xml or --library" % (where, c))
        comps.append((number(c.get("fraction"), where + " fraction", lo=0, hi=1, lo_open=True), pure[c["gas"]]))
    if abs(sum(f for f, _ in comps) - 1) > 1e-6:
        raise InputError("%s: gas fractions must add up to 1" % where)
    return comps


def component_el(fraction, pure):
    """Model.xml Gas/Components/Component, laid out like the GUI import."""
    c = ET.Element("Component")
    ET.SubElement(c, "Fraction").text = fmt(fraction)
    pr = ET.SubElement(c, "Properties")
    ET.SubElement(pr, "Name").text = pure.findtext("Name")
    ET.SubElement(pr, "MolecularWeight").text = pure.findtext("Properties/MolecularWeight")
    for k in ("Conductivity", "Viscosity", "SpecificHeat"):
        src = pure.find("Properties/" + k)
        if src is None:
            raise InputError("pure gas %s has no %s coefficients" % (pure.findtext("Name"), k))
        pr.append(clean_el(src))
    return c


# ---------------------------------------------------------------- change-set validation

def number(v, where, lo=None, hi=None, lo_open=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise InputError("%s must be a finite number (got %r)" % (where, v))
    if lo is not None and (v < lo or (lo_open and v == lo)):
        raise InputError("%s must be %s %s (got %s)" % (where, ">" if lo_open else ">=", lo, v))
    if hi is not None and v > hi:
        raise InputError("%s must be <= %s (got %s)" % (where, hi, v))
    return float(v)


def check_keys(d, allowed, where):
    if not isinstance(d, dict):
        raise InputError("%s must be a JSON object" % where)
    extra = set(d) - set(allowed)
    if extra:
        raise InputError("%s: unknown key(s) %s; allowed: %s" % (where, sorted(extra), sorted(allowed)))


def indexed(items, n, where):
    if not isinstance(items, list):
        raise InputError("%s must be a list" % where)
    seen = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not isinstance(it.get("index"), int) or not 0 <= it["index"] < n:
            raise InputError("%s[%d] needs an integer \"index\" from 0 to %d" % (where, i, n - 1))
        if it["index"] in seen:
            raise InputError("%s: index %d appears twice" % (where, it["index"]))
        seen.add(it["index"])
    return items


def load_changes(arg):
    if arg is None:
        return {}
    try:
        if arg == "-":
            return json.load(sys.stdin)
        if arg.lstrip().startswith("{"):
            return json.loads(arg)
        with open(arg, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise InputError("cannot read change set %s: %s" % (arg, exc))


# ---------------------------------------------------------------- patch

class Edit:
    def __init__(self):
        self.log = []

    def set(self, el, path, new, label):
        cur = el.find(path)
        if cur is None:
            raise ThermError("expected <%s> under <%s> for %s" % (path, el.tag, label))
        old = cur.text
        text = new if isinstance(new, str) else fmt(new)
        same = (old == text) if isinstance(new, str) else (fnum(old) is not None and abs(fnum(old) - new) < 1e-9)
        if not same:
            cur.text = text
            self.log.append({"field": label, "old": old, "new": text})


def shift_points(points, cut, axis, sign, delta, where):
    """Move every point on the interior side of the cut by sign*delta along axis.
    points: list of Point-like elements with x/y children."""
    tag = "xy"[axis]
    moved = 0
    for pt in points:
        c = float(pt.findtext(tag))
        beyond = sign * (c - cut)
        if beyond > EPS:
            if delta < 0 and beyond < -delta + EPS:
                raise InputError("%s: a point %.6f mm past the cut would cross it when the layer gets "
                                 "%.6f mm thinner; thin the geometry in the GUI instead" % (where, beyond, -delta))
            pt.find(tag).text = fmt(c + sign * delta)
            moved += 1
    return moved


def cmd_patch(args, warnings):
    if is_thmx(args.input):
        raise InputError("patch (option A) needs THERM 8.1's glazing-system data, which a 7.8 .thmx does not "
                         "store; on 7.8 use solid with each gap's conductivity_w_mk")
    ch = load_changes(args.changes)
    check_keys(ch, ("glazing_system", "name", "layers", "gaps", "properties", "also_shift"), "change set")
    infos, data = read_thmz(args.input)
    out_path = target_path(args, ".thmz")
    model = parse(data["Model.xml"], "Model.xml")
    bcs = parse(data["SteadyStateBC.xml"], "SteadyStateBC.xml") if "SteadyStateBC.xml" in data else None
    gs = select_system(model, ch.get("glazing_system"))
    tol = float_tol(model)
    ed = Edit()
    gname = gs.findtext("Name")
    layers_x, gases_x = gs.findall("Layers/Layer"), gs.findall("Gases/Gas")
    layers_p, gaps_p = layer_poly_map(model, gs)
    pool = None
    if any("gas" in g for g in ch.get("gaps") or [] if isinstance(g, dict)):
        pool = library_elements(args.library)
        if "Gases.xml" in data:
            for el in parse(data["Gases.xml"], "Gases.xml"):
                if el.tag in pool:
                    pool[el.tag].append(el)

    if "name" in ch:
        if not isinstance(ch["name"], str) or not ch["name"].strip():
            raise InputError("name must be a non-empty string")
        ed.set(gs, "Name", ch["name"], "name")

    thick = {}  # ("layer"|"gap", i) -> (old, new)
    for it in indexed(ch.get("layers") or [], len(layers_x), "layers"):
        w = "layers[index=%d]" % it["index"]
        check_keys(it, ("index", "name", "thickness_mm", "conductivity_w_mk", "emissivity_1", "emissivity_2"), w)
        L = layers_x[it["index"]]
        if "name" in it:
            ed.set(L, "Name", str(it["name"]), w + ".name")
        if "thickness_mm" in it:
            t = number(it["thickness_mm"], w + ".thickness_mm", lo=0, lo_open=True)
            thick[("layer", it["index"])] = (fnum(L.findtext("Thickness")), t)
            ed.set(L, "Thickness", t, w + ".thickness_mm")
        if "conductivity_w_mk" in it:
            ed.set(L, "Conductivity", number(it["conductivity_w_mk"], w + ".conductivity_w_mk", lo=0, lo_open=True),
                   w + ".conductivity_w_mk")
        for s in (1, 2):
            if "emissivity_%d" % s in it:
                ed.set(L, "Surface%d/Emissivity" % s,
                       number(it["emissivity_%d" % s], w + ".emissivity_%d" % s, lo=0, hi=1, lo_open=True),
                       w + ".emissivity_%d" % s)
    for it in indexed(ch.get("gaps") or [], len(gases_x), "gaps"):
        w = "gaps[index=%d]" % it["index"]
        check_keys(it, ("index", "thickness_mm", "conductivity_w_mk", "gas"), w)
        G = gases_x[it["index"]]
        if "thickness_mm" in it:
            t = number(it["thickness_mm"], w + ".thickness_mm", lo=0, lo_open=True)
            thick[("gap", it["index"])] = (fnum(G.findtext("Thickness")), t)
            ed.set(G, "Thickness", t, w + ".thickness_mm")
        if "conductivity_w_mk" in it:
            ed.set(G, "Conductivity", number(it["conductivity_w_mk"], w + ".conductivity_w_mk", lo=0, lo_open=True),
                   w + ".conductivity_w_mk")
        if "gas" in it:
            comps = gas_components(it["gas"], pool, w + ".gas")
            old = [(c.findtext("Properties/Name"), c.findtext("Fraction")) for c in G.findall("Components/Component")]
            new = [(p.findtext("Name"), fmt(f)) for f, p in comps]
            if old != new:
                holder = G.find("Components")
                for c in list(holder):
                    holder.remove(c)
                for f, p in comps:
                    append_indented(holder, component_el(f, p), 6)
                ed.log.append({"field": w + ".gas", "old": old, "new": new})
                if "conductivity_w_mk" not in it:
                    warnings.append("%s: gas changed but conductivity_w_mk was not; the effective conductivity "
                                    "THERM uses is still %s W/mK" % (w, G.findtext("Conductivity")))

    props = ch.get("properties") or {}
    check_keys(props, tuple(ENV), "properties")
    for env, p in props.items():
        w = "properties.%s" % env
        check_keys(p, ("u_value", "interior", "exterior"), w)
        P = [x for x in gs.findall("GlazingSystemProperties/Property")
             if (x.findtext("EnvironmentalCondition") or "") == ENV[env]]
        if not P:
            raise InputError("%s: the IGU has no %s property block" % (w, ENV[env]))
        P = P[0]
        if "u_value" in p:
            ed.set(P, "U-value", number(p["u_value"], w + ".u_value", lo=0, lo_open=True), w + ".u_value")
        for side in ("interior", "exterior"):
            if side not in p:
                continue
            c = p[side]
            check_keys(c, ("h", "hc", "hr", "temperature"), "%s.%s" % (w, side))
            C = P.find("Coefficients/" + side.capitalize())
            vals = coeffs(C)
            for k in ("hc", "hr", "h"):
                if k in c:
                    vals[k] = number(c[k], "%s.%s.%s" % (w, side, k), lo=0)
            if "h" not in c and ("hc" in c or "hr" in c):
                vals["h"] = vals["hc"] + vals["hr"]
            if abs(vals["h"] - (vals["hc"] + vals["hr"])) > 1e-3:
                raise InputError("%s.%s: h (%s) must equal hc + hr (%s); give hc and hr"
                                 % (w, side, vals["h"], vals["hc"] + vals["hr"]))
            if "temperature" in c:
                vals["temperature"] = number(c["temperature"], "%s.%s.temperature" % (w, side), lo=-273.15)
            for k, tagname in (("h", "H"), ("hc", "Hc"), ("hr", "Hr"), ("temperature", "Temperature")):
                ed.set(C, tagname, vals[k], "%s.%s.%s" % (w, side, k))
            if side == "exterior":
                warnings.append("%s.exterior changed in the IGU record only; the exterior BC %r is shared with "
                                "the frame and was not modified" % (w, gs.findtext("ExteriorBC")))

    geometry = {"steps": [], "polygons_moved": [], "boundaries_moved": 0}
    if any(abs(n - o) > 1e-9 for o, n in thick.values()):
        geometry.update(move_geometry(model, gs, thick, ch.get("also_shift"), tol, warnings, geometry))
    elif "also_shift" in ch:
        warnings.append("also_shift ignored: no thickness changed")
    bc_changed = False
    if ed.log:  # only re-sync dependants when something changed, so a null patch is a no-op
        sync_glazing_cavities(model, gs, ed)
        bc_changed = sync_igu_bc(bcs, gs, ed, warnings)

    if thick or any(k.startswith(("layers", "gaps")) for k in (e["field"] for e in ed.log)):
        warnings.append("THERM does not recompute the IGU from WINDOW: U-value, film coefficients and the "
                        "stored surface temperatures are as given. Get them from WINDOW or pywincalc")
    if ed.log or geometry["polygons_moved"]:
        data["Model.xml"] = serialize(model)
        if bc_changed:
            data["SteadyStateBC.xml"] = serialize(bcs)
        drop = [n for n in RESULT_ENTRIES if n in data]
    else:
        drop = []
    write_thmz(out_path, infos, data, drop)
    emit({"skill": SKILL, "version": VERSION, "command": "patch", "input": args.input, "output": out_path,
          "changed": bool(ed.log or geometry["polygons_moved"]), "changes": ed.log, "geometry": geometry,
          "stripped_entries": drop, "glazing_system": describe(model, gs, bcs, tol), "warnings": warnings}, args)


def move_geometry(model, gs, thick, also, tol, warnings, geometry):
    axis, sign = layout(model, gs)
    layers_p, gaps_p = layer_poly_map(model, gs)
    by_id = {p.findtext("ID"): p for p in polygons(model)}
    shift_set = {p.findtext("UUID") for p in system_polys(model, gs)}
    if also == "all":
        shift_set = {p.findtext("UUID") for p in polygons(model)}
    elif also is not None:
        if not isinstance(also, list):
            raise InputError("also_shift must be a list of polygon IDs or \"all\"")
        for pid in also:
            if str(pid) not in by_id:
                raise InputError("also_shift: no polygon with ID %s" % pid)
            shift_set.add(by_id[str(pid)].findtext("UUID"))
    moving = [p for p in polygons(model) if p.findtext("UUID") in shift_set]
    fixed = [p for p in polygons(model) if p.findtext("UUID") not in shift_set]
    before = {p.findtext("UUID"): poly_points(p) for p in polygons(model)}
    bnds = [b for b in boundaries(model)
            if any(e.text in shift_set for e in b if e.tag.startswith("NeighborPolygonUUID"))]
    origin = model.find("GlazingOrigin")

    n_layers = len(gs.findall("Layers/Layer"))
    order = []
    for i in range(n_layers):
        order.append(("layer", i))
        if i < n_layers - 1:
            order.append(("gap", i))
    for kind, i in order:
        if (kind, i) not in thick:
            continue
        old, new = thick[(kind, i)]
        delta = new - old
        if abs(delta) < 1e-9:
            continue
        poly = (layers_p if kind == "layer" else gaps_p).get(i)
        if poly is None:
            raise InputError("%s %d has no polygon to move" % (kind, i))
        lo, hi = extent(poly, axis)
        if abs((hi - lo) - old) > tol:
            raise InputError("polygon %s (%s %d) is %.6f mm thick but the IGU stores %s mm; the glazing is "
                             "rotated or not rectangular, which patch does not handle"
                             % (poly.findtext("ID"), kind, i, hi - lo, old))
        cut = (lo + hi) / 2
        pts = [pt for p in moving for pt in p.findall("Points/Point")]
        for b in bnds:
            pts += [b.find(t) for t in ("StartPoint", "EndPoint") if b.find(t) is not None]
            o = b.find("Origin")
            if o is not None and (fnum(o.findtext("x")), fnum(o.findtext("y"))) != (0.0, 0.0):
                pts.append(o)
        if origin is not None:
            pts.append(origin)
        where = "%s %d" % (kind, i)
        n = shift_points(pts, cut, axis, sign, delta, where)
        geometry["steps"].append({"element": kind, "index": i, "delta_mm": round(delta, 6),
                                  "cut_%s_mm" % "xy"[axis]: round(cut, 6), "points_moved": n})
    geometry["axis"] = "xy"[axis]
    geometry["interior_direction"] = ("+" if sign > 0 else "-") + "xy"[axis]

    changed = [p for p in moving if poly_points(p) != before[p.findtext("UUID")]]
    hits = []
    for p in changed:
        A = poly_points(p)
        for f in fixed:
            if overlap(A, poly_points(f)):
                hits.append("%s %s (%s) would overlap fixed polygon %s %r"
                            % (p.findtext("Type"), p.findtext("ID"), p.findtext("MaterialName") or "glazing",
                               f.findtext("ID"), f.findtext("MaterialName")))
    if hits:
        raise InputError("refusing the move: %s. Add the frame polygons that must follow the glazing to "
                         "\"also_shift\" (IDs, or \"all\" to stretch the whole section at the cut), or change "
                         "the frame in the GUI" % "; ".join(hits))
    for f in fixed:
        F = poly_points(f)
        c0 = sum(contact(before[p.findtext("UUID")], F) for p in changed)
        c1 = sum(contact(poly_points(p), F) for p in changed)
        if c1 < c0 - 1e-3:
            warnings.append("fixed polygon %s %r lost %.3f mm of contact with the moved glazing; the move may "
                            "have opened a void that THERM will reject. Add it to also_shift if it should follow"
                            % (f.findtext("ID"), f.findtext("MaterialName"), c0 - c1))
    if any(p.find("CavityUUID") is not None and p.find("GlazingSystem") is None for p in changed):
        warnings.append("stretched frame cavity records keep their stored dimensions; THERM recomputes "
                        "cavity values at solve time (not yet verified in a THERM solve)")
    geometry["polygons_moved"] = [{"id": p.findtext("ID"), "type": p.findtext("Type"),
                                   "material": p.findtext("MaterialName") or None} for p in changed]
    geometry["boundaries_moved"] = len(bnds)
    return geometry


def sync_glazing_cavities(model, gs, ed):
    """Keep each gap's Cavity record and glazing-cavity boundaries in line with the IGU layers."""
    layers_x, gases_x = gs.findall("Layers/Layer"), gs.findall("Gases/Gas")
    layers_p, gaps_p = layer_poly_map(model, gs)
    try:
        axis, sign = layout(model, gs)
    except InputError:
        return
    cavs = {(c.findtext("UUID") or "").strip(): c for c in (model.find("Cavities") if model.find("Cavities") is not None else [])}
    for i, gp in gaps_p.items():
        if i >= len(gases_x) or i + 1 >= len(layers_x):
            continue
        e1 = fnum(layers_x[i].findtext("Surface2/Emissivity"))
        e2 = fnum(layers_x[i + 1].findtext("Surface1/Emissivity"))
        cav = cavs.get((gp.findtext("CavityUUID") or "").strip())
        if cav is not None:
            ed.set(cav, "Emissivity1", e1, "cavity %s.Emissivity1" % cav.findtext("UUID"))
            ed.set(cav, "Emissivity2", e2, "cavity %s.Emissivity2" % cav.findtext("UUID"))
            lo, hi = extent(gp, axis)
            dim = "MaxXDimension" if axis == 0 else "MaxYDimension"
            old = fnum(cav.findtext(dim))
            if old and abs(old - (hi - lo)) > 1e-6:
                ed.set(cav, dim, hi - lo, "cavity %s.%s" % (cav.findtext("UUID"), dim))
                area = fnum(cav.findtext("Area"))
                if area:
                    cav.find("Area").text = "%.6g" % (area * (hi - lo) / old)
        # boundary emissivities on the gap faces: exterior face = layer i surface 2, interior = layer i+1 surface 1
        g_lo, g_hi = extent(gp, axis)
        faces = {g_lo: e1, g_hi: e2} if sign > 0 else {g_hi: e1, g_lo: e2}
        uu = gp.findtext("UUID")
        for b in boundaries(model):
            if b.findtext("Type") != "Glazing Cavity" or uu not in [x.text for x in b if x.tag.startswith("Neighbor")]:
                continue
            s, t = fnum(b.findtext("StartPoint/" + "xy"[axis])), fnum(b.findtext("EndPoint/" + "xy"[axis]))
            for face, e in faces.items():
                if abs(s - face) < EPS * 10 and abs(t - face) < EPS * 10 and e is not None:
                    ed.set(b, "ThermalEmissionProperties/Emissivity", e,
                           "boundary %s.emissivity" % b.findtext("ID"))
    # outer glass faces: layer 0 surface 1 (exterior), last layer surface 2 (interior)
    for j, surf in ((0, "Surface1"), (len(layers_x) - 1, "Surface2")):
        p = layers_p.get(j)
        if p is None:
            continue
        e = fnum(layers_x[j].findtext(surf + "/Emissivity"))
        lo, hi = extent(p, axis)
        face = (lo if sign > 0 else hi) if surf == "Surface1" else (hi if sign > 0 else lo)
        for b in boundaries(model):
            if b.findtext("Type") == "Glazing Cavity" or p.findtext("UUID") not in \
                    [x.text for x in b if x.tag.startswith("Neighbor")]:
                continue
            s, t = fnum(b.findtext("StartPoint/" + "xy"[axis])), fnum(b.findtext("EndPoint/" + "xy"[axis]))
            if abs(s - face) < EPS * 10 and abs(t - face) < EPS * 10 and \
                    b.find("ThermalEmissionProperties/Emissivity") is not None:
                ed.set(b, "ThermalEmissionProperties/Emissivity", e, "boundary %s.emissivity" % b.findtext("ID"))


def sync_igu_bc(bcs, gs, ed, warnings):
    """The IGU-surface BC carries the Winter interior film: convection Hc for a comprehensive BC
    (sample-sill: 2.614016 = Hc), combined H for a simplified one. Also the gaps' CR BCTemperature."""
    P = [x for x in gs.findall("GlazingSystemProperties/Property") if x.findtext("EnvironmentalCondition") == "Winter"]
    if not P:
        return False
    inner = coeffs(P[0].find("Coefficients/Interior"))
    for g in gs.findall("Gases/Gas"):
        if g.find("CRProperties/BCTemperature") is not None:
            ed.set(g, "CRProperties/BCTemperature", inner["temperature"], "gas %s.cr_bc_temperature" % g.findtext("ID"))
    bc = find_igu_bc(bcs, gs)
    if bc is None:
        warnings.append("IGU interior BC %r not found in SteadyStateBC.xml; nothing to keep in step"
                        % interior_bc_name(gs))
        return False
    n = len(ed.log)
    label = "igu_bc %r" % bc.findtext("Name")
    if bc.find("Comprehensive") is not None:
        ed.set(bc, "Comprehensive/Convection/FilmCoefficient", inner["hc"], label + ".film_coefficient")
        ed.set(bc, "Comprehensive/Convection/Temperature", inner["temperature"], label + ".temperature")
        r = bc.find("Comprehensive/Radiation")
        if r is not None and len(r) and r[0].find("Temperature") is not None:
            ed.set(r[0], "Temperature", inner["temperature"], label + ".radiation_temperature")
    elif bc.find("Simplified") is not None:
        ed.set(bc, "Simplified/FilmCoefficient", inner["h"], label + ".film_coefficient")
        ed.set(bc, "Simplified/Temperature", inner["temperature"], label + ".temperature")
    return len(ed.log) > n


# ---------------------------------------------------------------- solid (option C)

def solid_material(name, k, e, color):
    m = ET.Element("Material")
    for tag, text in (("UUID", str(uuid.uuid5(UUID_NS, name))), ("Name", name), ("Protected", "false"),
                      ("Color", color)):
        ET.SubElement(m, tag).text = text
    s = ET.SubElement(m, "Solid")
    ET.SubElement(ET.SubElement(s, "HygroThermal"), "ThermalConductivityDry").text = fmt(k)
    ir = ET.SubElement(ET.SubElement(ET.SubElement(s, "Optical"), "Integrated"), "Infrared")
    ET.SubElement(ir, "Emissivity-Front").text = fmt(e)
    ET.SubElement(ir, "Emissivity-Back").text = fmt(e)
    return m


def mat_names(kind, k, e=None):
    if kind == "glass":
        return "Glass k=%s e=%s (therm-glazing)" % (fmt(k), fmt(e)), "0x80FFFF"
    if kind == "gap":
        return "Glazing gap keff=%s (therm-glazing)" % fmt(k), "0xC0FFC0"
    return "Calibration panel k=%s (therm-glazing)" % fmt(k), "0xFFFF80"


def solid_plan(ch, n_layers, stored_gap_k, where_gaps):
    """Per element material (name, k, e, color) for glass layers and gaps, or one panel."""
    glass = ch.get("glass") or {}
    check_keys(glass, ("conductivity_w_mk", "emissivity"), "glass")
    gk = number(glass.get("conductivity_w_mk", GLASS_K), "glass.conductivity_w_mk", lo=0, lo_open=True)
    ge = number(glass.get("emissivity", GLASS_E), "glass.emissivity", lo=0, hi=1, lo_open=True)
    gaps = {g["index"]: g for g in indexed(ch.get("gaps") or [], max(n_layers - 1, 0) or 1, "gaps")} \
        if ch.get("gaps") else {}
    plan, notes = {}, []
    for i in range(n_layers):
        plan[("layer", i)] = (gk, ge)
    for i in range(n_layers - 1):
        g = gaps.get(i, {})
        check_keys(g, ("index", "thickness_mm", "conductivity_w_mk"), "gaps[index=%d]" % i)
        if "conductivity_w_mk" in g:
            k = number(g["conductivity_w_mk"], "gaps[index=%d].conductivity_w_mk" % i, lo=0, lo_open=True)
        elif stored_gap_k and stored_gap_k[i] is not None:
            k = stored_gap_k[i]
            notes.append("gap %d keff %s W/mK taken from the IGU record (WINDOW's effective conductivity)"
                         % (i, fmt(k)))
        else:
            raise InputError("%s: gaps[index=%d].conductivity_w_mk is required (effective conductivity, e.g. "
                             "from pywincalc or EN 673)" % (where_gaps, i))
        plan[("gap", i)] = (k, 0.9)
    return plan, notes


def cmd_solid(args, warnings):
    ch = load_changes(args.changes)
    if is_thmx(args.input):  # THERM 7.8
        return cmd_solid_thmx(args, ch, warnings)
    if args.output and not args.thmz and args.output.lower().endswith(".thmz"):
        args.thmz = args.output
    if args.input.lower().endswith(".json"):
        return solid_spec(args, ch, warnings)
    check_keys(ch, ("glazing_system", "glass", "gaps", "panel", "interior_bc"), "change set")
    infos, data = read_thmz(args.input)
    out_path = target_path(args, ".thmz")
    model = parse(data["Model.xml"], "Model.xml")
    mats = parse(data["Materials.xml"], "Materials.xml") if "Materials.xml" in data else None
    bcs = parse(data["SteadyStateBC.xml"], "SteadyStateBC.xml") if "SteadyStateBC.xml" in data else None
    if mats is None or bcs is None:
        raise InputError("%s needs Materials.xml and SteadyStateBC.xml entries" % args.input)
    gs = select_system(model, ch.get("glazing_system"))
    tol = float_tol(model)
    before = describe(model, gs, bcs, tol)
    n_layers = len(before["layers"])
    polys = system_polys(model, gs)
    if not polys:
        raise InputError("glazing system %s has no polygons" % before["name"])
    notes = []
    if ch.get("panel") is not None:
        panel = ch["panel"]
        check_keys(panel, ("thickness_mm", "conductivity_w_mk"), "panel")
        k = number(panel.get("conductivity_w_mk", PANEL_K), "panel.conductivity_w_mk", lo=0, lo_open=True)
        if "thickness_mm" in panel:
            t = number(panel["thickness_mm"], "panel.thickness_mm", lo=0, lo_open=True)
            if abs(t - before["total_thickness_mm"]) > tol:
                raise InputError("panel.thickness_mm %s differs from the glazing's %s mm; in a .thmz the panel "
                                 "takes the glazing polygons' place, so the thickness must match (resize with "
                                 "patch first)" % (t, before["total_thickness_mm"]))
        name, color = mat_names("panel", k)
        assign = {id(p): (name, k, 0.9, color) for p in polys}
    else:
        if any(isinstance(g, dict) and "thickness_mm" in g for g in ch.get("gaps") or []):
            raise InputError("solid on a .thmz keeps the existing geometry; change thicknesses with patch first")
        plan, notes = solid_plan(ch, n_layers, [g["conductivity_w_mk"] for g in before["gaps"]], "solid")
        assign = {}
        for p in polys:
            kind, i = ("gap" if is_gap(p) else "layer"), poly_ref(p)[1]
            if (kind, i) not in plan:
                raise InputError("polygon %s refers to %s %d, which the IGU does not define"
                                 % (p.findtext("ID"), kind, i))
            k, e = plan[(kind, i)]
            name, color = mat_names("glass" if kind == "layer" else "gap", k, e)
            assign[id(p)] = (name, k, e, color)

    # interior BC that replaces the IGU film on the glass
    igu_bc = find_igu_bc(bcs, gs)
    igu_names = {interior_bc_name(gs), interior_bc_name(gs).split(":", 1)[-1]}
    if igu_bc is not None:
        igu_names.add(igu_bc.findtext("Name"))
    users = [b for b in boundaries(model) if b.findtext("Name") in igu_names]
    new_bc = None
    if users:
        want = ch.get("interior_bc")
        if not want:
            raise InputError("%d boundaries use the IGU film BC %r, which goes away with the glazing system; set "
                             "\"interior_bc\" to the BC for the glass surface (for ISO work, \"ISO Interior Rsi "
                             "0.13 normal (20 C)\" from therm-library presets iso, passed with --library)"
                             % (len(users), interior_bc_name(gs)))
        new_bc = next((b for b in bcs.findall("BoundaryCondition") if b.findtext("Name") == want), None)
        if new_bc is None:
            pool = library_elements(args.library)["BoundaryCondition"]
            src = next((b for b in pool if b.findtext("Name") == want), None)
            if src is None:
                raise InputError("interior_bc %r is not in the file's SteadyStateBC.xml or any --library" % want)
            new_bc = clean_el(src)
            append_indented(bcs, new_bc, 1)
        enclosure = new_bc.find("Comprehensive/Radiation/AutomaticEnclosure") is not None
        for b in users:
            b.find("Name").text = want
            b.find("Type").text = "Enclosure" if enclosure else "Boundary Condition"

    # polygons -> Material
    poly_uuids = {p.findtext("UUID") for p in polys}
    cav_uuids = set()
    existing = {(m.findtext("UUID") or "").strip(): m for m in mats.findall("Material")}
    added = []
    for p in polys:
        name, k, e, color = assign[id(p)]
        rec = solid_material(name, k, e, color)
        u = rec.findtext("UUID")
        if u not in existing:
            append_indented(mats, rec, 1)
            existing[u] = rec
            added.append({"name": name, "uuid": u, "conductivity_w_mk": k, "emissivity": e})
        p.find("MaterialUUID").text = u
        p.find("MaterialName").text = name
        p.remove(p.find("GlazingSystem"))
        cav = p.find("CavityUUID")
        if cav is not None:
            cav_uuids.add((cav.text or "").strip())
            p.remove(cav)
        p.find("Type").text = "Material"
    # glazing-cavity boundaries become internal edges between solids
    holder = model.find("Boundaries")
    removed_b = 0
    for b in list(holder):
        ns = [x.text for x in b if x.tag.startswith("NeighborPolygonUUID")]
        if b.findtext("Type") == "Glazing Cavity" and set(ns) & poly_uuids:
            holder.remove(b)
            removed_b += 1
    cavities = model.find("Cavities")
    if cavities is not None:
        for c in list(cavities):
            if (c.findtext("UUID") or "").strip() in cav_uuids:
                cavities.remove(c)
    holder = model.find("GlazingSystems")
    holder.remove(gs)
    if not len(holder):
        holder.text = None
    # drop the IGU BC record when nothing references it any more
    if igu_bc is not None and igu_bc is not new_bc:
        still = any(b.findtext("Name") == igu_bc.findtext("Name") for b in boundaries(model)) or \
            any(interior_bc_name(s).split(":", 1)[-1] == igu_bc.findtext("Name") for s in systems(model))
        if not still:
            bcs.remove(igu_bc)

    bad = unresolved_materials(model, mats)
    if bad:
        raise ThermError("internal check failed, unresolved polygon materials: %s" % bad)
    data["Model.xml"] = serialize(model)
    data["Materials.xml"] = serialize(mats)
    data["SteadyStateBC.xml"] = serialize(bcs)
    drop = [n for n in RESULT_ENTRIES if n in data]
    write_thmz(out_path, infos, data, drop)
    warnings.append("option C loses THERM's glass-plane projection and the automatic IGU film BC; report U "
                    "on the projection you need and check the interior BC on the glass")
    emit({"skill": SKILL, "version": VERSION, "command": "solid", "input": args.input, "output": out_path,
          "replaced_glazing_system": {"index": before["index"], "id": before["id"], "name": before["name"],
                                      "total_thickness_mm": before["total_thickness_mm"]},
          "mode": "panel" if ch.get("panel") is not None else "glass+gaps",
          "polygons": [{"id": p.findtext("ID"), "material": p.findtext("MaterialName")} for p in polys],
          "materials_added": added, "interior_bc": ch.get("interior_bc") if users else None,
          "boundaries_retargeted": len(users), "glazing_cavity_boundaries_removed": removed_b,
          "unresolved_materials": bad, "stripped_entries": drop, "notes": notes, "warnings": warnings}, args)


def solid_spec(args, ch, warnings):
    """Fill a rectangular polygon of a therm-model spec JSON with glass/gap solids or a panel."""
    check_keys(ch, ("region", "interior", "glass", "layers", "gaps", "panel"), "change set")
    try:
        with open(args.input, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as exc:
        raise InputError("cannot read spec %s: %s" % (args.input, exc))
    if not isinstance(spec, dict) or not isinstance(spec.get("polygons"), list):
        raise InputError("%s is not a therm-model spec (no polygons list)" % args.input)
    out_path = target_path(args, ".json")
    region = ch.get("region")
    idx = [i for i, p in enumerate(spec["polygons"]) if isinstance(p, dict) and p.get("id") == region]
    if not idx:
        raise InputError("region: no polygon with id %r in the spec" % region)
    i0 = idx[0]
    pts = [tuple(map(float, q)) for q in spec["polygons"][i0]["points"]]
    x0, x1, y0, y1 = bbox(pts)
    corners = {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}
    if len(set(pts)) != 4 or any(min(math.hypot(p[0] - c[0], p[1] - c[1]) for c in corners) > EPS for p in pts):
        raise InputError("region %r must be an axis-aligned rectangle" % region)
    interior = ch.get("interior")
    if interior not in ("+x", "-x", "+y", "-y"):
        raise InputError("interior must be +x, -x, +y or -y (the direction from exterior to interior)")
    axis, sign = "xy".index(interior[1]), (1 if interior[0] == "+" else -1)
    width = (x1 - x0) if axis == 0 else (y1 - y0)
    start = ((x0 if sign > 0 else x1), (y0 if sign > 0 else y1))[axis]
    elements, notes = [], []
    if ch.get("panel") is not None:
        panel = ch["panel"]
        check_keys(panel, ("thickness_mm", "conductivity_w_mk"), "panel")
        k = number(panel.get("conductivity_w_mk", PANEL_K), "panel.conductivity_w_mk", lo=0, lo_open=True)
        t = number(panel.get("thickness_mm", width), "panel.thickness_mm", lo=0, lo_open=True)
        name, color = mat_names("panel", k)
        elements.append(("%s-panel" % region, t, name, k, 0.9, color))
    else:
        layers = ch.get("layers")
        if not isinstance(layers, list) or not layers:
            raise InputError("spec mode needs \"layers\": [{thickness_mm}, ...] or \"panel\"")
        n = len(layers)
        plan, notes = solid_plan(dict(ch, gaps=[dict(g, index=j) if "index" not in g else g
                                                for j, g in enumerate(ch.get("gaps") or [])]),
                                 n, None, "spec mode")
        gaps = ch.get("gaps") or []
        if len(gaps) != n - 1:
            raise InputError("spec mode needs %d gap(s) for %d layer(s)" % (n - 1, n))
        for j in range(n):
            check_keys(layers[j], ("thickness_mm", "conductivity_w_mk", "emissivity"), "layers[%d]" % j)
            t = number(layers[j].get("thickness_mm"), "layers[%d].thickness_mm" % j, lo=0, lo_open=True)
            k = number(layers[j].get("conductivity_w_mk", plan[("layer", j)][0]), "layers[%d].conductivity_w_mk"
                       % j, lo=0, lo_open=True)
            e = number(layers[j].get("emissivity", plan[("layer", j)][1]), "layers[%d].emissivity" % j,
                       lo=0, hi=1, lo_open=True)
            name, color = mat_names("glass", k, e)
            elements.append(("%s-glass-%d" % (region, j), t, name, k, e, color))
            if j < n - 1:
                gt = number(gaps[j].get("thickness_mm"), "gaps[%d].thickness_mm" % j, lo=0, lo_open=True)
                gk = plan[("gap", j)][0]
                name, color = mat_names("gap", gk)
                elements.append(("%s-gap-%d" % (region, j), gt, name, gk, 0.9, color))
    total = sum(e[1] for e in elements)
    if abs(total - width) > 0.01:
        raise InputError("glazing is %s mm thick but region %r is %s mm along %s; make them equal so the "
                         "spec's boundaries still meet the faces" % (fmt(total), region, fmt(width), "xy"[axis]))
    mats = spec.setdefault("materials", [])
    by_name = {m.get("name"): m for m in mats if isinstance(m, dict)}
    new_polys, pos = [], start
    for pid, t, name, k, e, color in elements:
        a, b = pos, pos + sign * t
        lo, hi = min(a, b), max(a, b)
        if axis == 0:
            box = [[lo, y0], [hi, y0], [hi, y1], [lo, y1]]
        else:
            box = [[x0, lo], [x1, lo], [x1, hi], [x0, hi]]
        new_polys.append({"id": pid, "material": name, "points": [[float(fmt(v)) for v in q] for q in box]})
        if name not in by_name:
            rec = {"name": name, "uuid": str(uuid.uuid5(UUID_NS, name)), "conductivity": k, "emissivity": e,
                   "color": color}
            mats.append(rec)
            by_name[name] = rec
        pos = b
    spec["polygons"][i0:i0 + 1] = new_polys
    write_json(out_path, spec)
    warnings.append("option C loses THERM's glass-plane projection and the automatic IGU film BC")
    emit({"skill": SKILL, "version": VERSION, "command": "solid", "input": args.input, "output": out_path,
          "region": region, "interior": interior, "thickness_mm": fmt(total),
          "mode": "panel" if ch.get("panel") is not None else "glass+gaps", "polygons": new_polys,
          "notes": notes, "warnings": warnings}, args)


# ---------------------------------------------------------------- THERM 7.8 .thmx (equivalent solid only)
# A .thmx carries no IGU layer data and no gas field: THERM 7.8 turns an imported IGU into solids
# anyway, so on 7.8 this skill writes glass panes and gaps as plain solids (option C) and nothing else.
# Helpers are kept here (no import from therm-model), so the skill can be copied on its own.

THMX_NS = "http://windows.lbl.gov"
TQ = "{%s}" % THMX_NS
GLAZING_TYPES = {"11": "layer", "9": "layer", "5": "gap"}   # outer glass, inner glass, gap


def normalise_name(name):
    """Copied from research/library-record-78.md section 3 (the shared 7.8 name rule)."""
    name = re.sub(r"^(?:[^\s:]+:)+", "", name.strip())
    return re.sub(r"(?:_Cavity_\d+)+$", "", name).strip()


def is_thmx(path):
    return path.lower().endswith(".thmx")


def read_thmx(path):
    try:
        root = ET.parse(path).getroot()
    except OSError as exc:
        raise InputError("cannot read %s: %s" % (path, exc))
    except ET.ParseError as exc:
        raise ThermError("%s is not valid XML: %s" % (path, exc))
    if root.tag != TQ + "THERM-XML":
        raise InputError("%s is not a THERM 7.8 .thmx (root %s)" % (path, root.tag))
    return root


def write_thmx(path, root):
    stem = os.path.splitext(os.path.basename(path))[0]
    if "." in stem:
        raise InputError("output name %r has a period in the file stem; THERM 7.8 hangs on such names"
                         % os.path.basename(path))
    ET.register_namespace("", THMX_NS)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="ascii", errors="xmlcharrefreplace", newline="\n") as fh:
        fh.write('<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n")


def thmx_glazing(root):
    """[(polygon, kind, index)] for IGU polygons: Type 11/9/5, or <IGU>_Glass_<n> / <IGU>_Gap_<n> names."""
    out = []
    for p in root.iter(TQ + "Polygon"):
        name = normalise_name(p.get("Material", ""))
        m = re.search(r"_(Glass|Gap)_(\d+)$", name)
        kind = GLAZING_TYPES.get(p.get("Type")) or (m and ("layer" if m.group(1) == "Glass" else "gap"))
        if kind:
            out.append((p, kind, int(m.group(2)) if m else None))
    layers = sorted({i for _, k, i in out if k == "layer" and i is not None})
    gaps = sorted({i for _, k, i in out if k == "gap" and i is not None})
    for p, kind, i in out:
        if i is None:
            raise InputError("glazing polygon %s has no _Glass_<n>/_Gap_<n> material name to number it by"
                             % p.get("ID"))
    return out, layers, gaps


def thmx_record(root, tag, name):
    return next((e for e in root.iter(TQ + tag) if normalise_name(e.get("Name", "")) == normalise_name(name)),
                None)


def thmx_library_bc(paths, name):
    """A BC record with a "thmx" dict from a therm-library project JSON, or one inside another .thmx."""
    for path in paths or []:
        if is_thmx(path):
            el = thmx_record(read_thmx(path), "BoundaryCondition", name)
            if el is not None:
                return dict(el.attrib, Name=normalise_name(el.get("Name")))
        elif path.lower().endswith(".json"):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError) as exc:
                raise InputError("cannot read library JSON %s: %s" % (path, exc))
            for r in doc.get("boundary_conditions") or []:
                if isinstance(r, dict) and isinstance(r.get("thmx"), dict) and \
                        normalise_name(r.get("name") or r["thmx"].get("Name", "")) == normalise_name(name):
                    return dict({k: str(v) for k, v in r["thmx"].items()}, Name=normalise_name(r["thmx"]["Name"]))
    return None


def cmd_solid_thmx(args, ch, warnings):
    check_keys(ch, ("glass", "gaps", "interior_bc"), "change set (THERM 7.8 .thmx)")
    out = args.output or os.path.join(os.path.dirname(os.path.abspath(args.input)),
                                      os.path.splitext(os.path.basename(args.input))[0].replace(".", "_")
                                      + "_solid.thmx")
    if not is_thmx(out):
        raise InputError("a .thmx input gives a .thmx output (THERM 7.8); got %s" % out)
    if os.path.exists(out) and os.path.samefile(out, args.input) and not args.in_place:
        raise InputError("output is the input file; pass --in-place to overwrite it")
    root = read_thmx(args.input)
    polys, layers, gap_ids = thmx_glazing(root)
    if not polys:
        raise InputError("%s has no glazing polygons (Type 11/9/5 or <IGU>_Glass_<n>/_Gap_<n> materials)"
                         % args.input)
    glass = ch.get("glass") or {}
    check_keys(glass, ("conductivity_w_mk", "emissivity"), "glass")
    gk = number(glass.get("conductivity_w_mk", GLASS_K), "glass.conductivity_w_mk", lo=0, lo_open=True)
    ge = number(glass.get("emissivity", GLASS_E), "glass.emissivity", lo=0, hi=1, lo_open=True)
    given = {}
    for j, g in enumerate(ch.get("gaps") or []):
        where = "gaps[%d]" % j
        if not isinstance(g, dict):
            raise InputError("%s must be a JSON object" % where)
        idx = g.get("index", j)
        if "conductivity_w_mk" not in g:
            raise InputError("%s: conductivity_w_mk is required on THERM 7.8 (a .thmx has no gas field, so %s "
                             "alone cannot set the gap); give the gap's effective conductivity in W/mK"
                             % (where, "gas %r" % g["gas"] if "gas" in g else "the gap"))
        check_keys(g, ("index", "conductivity_w_mk", "gas"), where)
        if "gas" in g:
            warnings.append("%s: gas %r is a label only on THERM 7.8; conductivity_w_mk sets the gap" % (where, g["gas"]))
        given[idx] = number(g["conductivity_w_mk"], where + ".conductivity_w_mk", lo=0, lo_open=True)
    missing = [i for i in gap_ids if i not in given]
    if missing:
        raise InputError("gaps: conductivity_w_mk is required for every gap on THERM 7.8 (missing gap index %s; "
                         "the stored value is not reused)" % ", ".join(map(str, missing)))
    extra = sorted(set(given) - set(gap_ids))
    if extra:
        raise InputError("gaps: index %s not in the model (gaps are %s)" % (extra, gap_ids))

    holder = root.find(TQ + "Materials")
    old_names = {p.get("Material") for p, _, _ in polys}
    plan = {}
    for p, kind, i in polys:
        k, e = (gk, ge) if kind == "layer" else (given[i], 0.9)
        name, color = mat_names("glass" if kind == "layer" else "gap", k, e)
        plan[id(p)] = name
        if thmx_record(root, "Material", name) is None:
            el = ET.SubElement(holder, TQ + "Material", {
                "Name": name, "Index": str(max([int(m.get("Index", 0)) for m in holder] + [0]) + 1), "Type": "0",
                "Conductivity": "%.6f" % k, "Tir": "0.000000", "EmissivityFront": "%.6f" % e,
                "EmissivityBack": "%.6f" % e, "RGBColor": color})
            el.tail = "\n\t"
    ids = {p.get("ID") for p, _, _ in polys}
    for p, _, _ in polys:
        p.set("Material", plan[id(p)])
        p.set("Type", "1")
    used = {p.get("Material") for p in root.iter(TQ + "Polygon")}
    for m in list(holder):
        if m.get("Name") in old_names and m.get("Name") not in used:
            holder.remove(m)
    # glazing-cavity segments become internal edges between solids, as THERM's own import does
    bholder = root.find(TQ + "Boundaries")
    removed = 0
    for b in list(bholder):
        if normalise_name(b.get("BC", "")) == "Glazing Cavity Surface":
            bholder.remove(b)
            removed += 1
    gs = root.find(TQ + "GlazingSystems")
    if gs is not None:
        for g in list(gs):
            gs.remove(g)
    # inside film: keep the IGU film BC as a plain convection BC, or retarget it to interior_bc
    films = [b for b in bholder if b.get("PolygonID") in ids and "Inside Film" in b.get("BC", "")]
    film_names = sorted({b.get("BC") for b in films})
    want = ch.get("interior_bc")
    bcs = root.find(TQ + "BoundaryConditions")
    if want:
        rec = thmx_record(root, "BoundaryCondition", want)
        if rec is None:
            src = thmx_library_bc(args.library, want)
            if src is None:
                raise InputError("interior_bc %r is not in the file or any --library record with a \"thmx\" dict"
                                 % want)
            rec = ET.SubElement(bcs, TQ + "BoundaryCondition", src)
            rec.tail = "\n"
        targets = films or [b for b in bholder if b.get("PolygonID") in ids and b.get("EnclosureID", "0") == "0"
                            and normalise_name(b.get("BC", "")) != "Adiabatic" and b.get("UFactorTag")]
        if not targets:
            raise InputError("interior_bc: found no glass boundary to put it on (no 'Inside Film' BC on the IGU)")
        for b in targets:
            b.set("BC", rec.get("Name"))
        film = rec.get("Name")
    elif films:
        film = film_names[0]
        warnings.append("inside film kept: %s stays on the glass as an ordinary convection BC" % film_names)
    else:
        film = None
        warnings.append("no 'Inside Film' BC found on the glazing; check the interior BC on the glass")
    for b in list(bcs):
        n = normalise_name(b.get("Name", ""))
        if n == "Glazing Cavity Surface" or (b.get("Name") in film_names and b.get("Name") != film):
            if not any(x.get("BC") == b.get("Name") for x in bholder):
                bcs.remove(b)
    for tag in ("Mesh", "Results"):
        for e in root.findall(TQ + tag):
            root.remove(e)
    write_thmx(out, root)
    warnings.append("THERM 7.8: equivalent solid only; no Condensation Resistance case and no 'Projected in "
                    "glass plane' U-factor (read Projected Y on a sill)")
    emit({"skill": SKILL, "version": VERSION, "command": "solid", "format": "thmx", "therm_version": "7.8",
          "input": args.input, "output": out, "mode": "glass+gaps",
          "polygons": [{"id": p.get("ID"), "material": p.get("Material")} for p, _, _ in polys],
          "layers": len(layers), "gaps": [{"index": i, "conductivity_w_mk": given[i]} for i in gap_ids],
          "inside_film_bc": film, "glazing_cavity_boundaries_removed": removed, "warnings": warnings}, args)


def cmd_show_thmx(args, warnings):
    root = read_thmx(args.input)
    polys, layers, gaps = thmx_glazing(root)
    mats = {m.get("Name"): m for m in root.iter(TQ + "Material")}
    emit({"skill": SKILL, "version": VERSION, "command": "show", "format": "thmx", "input": args.input,
          "glazing_systems": [dict(g.attrib) for g in root.iter(TQ + "GlazingSystem")],
          "polygons": [{"id": p.get("ID"), "kind": k, "index": i, "material": p.get("Material"),
                        "conductivity_w_mk": float(mats[p.get("Material")].get("Conductivity"))
                        if p.get("Material") in mats else None} for p, k, i in polys],
          "layers": len(layers), "gaps": len(gaps),
          "results_present": root.find(TQ + "Results") is not None,
          "warnings": warnings + ["THERM 7.8 .thmx stores no IGU layer data; only equivalent solids can be edited"]},
         args)


# ---------------------------------------------------------------- show and CLI

def cmd_show(args, warnings):
    if is_thmx(args.input):
        return cmd_show_thmx(args, warnings)
    infos, data = read_thmz(args.input)
    model = parse(data["Model.xml"], "Model.xml")
    bcs = parse(data["SteadyStateBC.xml"], "SteadyStateBC.xml") if "SteadyStateBC.xml" in data else None
    mats = parse(data["Materials.xml"], "Materials.xml") if "Materials.xml" in data else None
    tol = float_tol(model)
    found = [describe(model, gs, bcs, tol) for gs in systems(model)]
    o = model.find("GlazingOrigin")
    emit({"skill": SKILL, "version": VERSION, "command": "show", "input": args.input,
          "calculation_ready": (model.findtext("CalculationReady") or "").strip() == "true",
          "results_entries": [n for n in RESULT_ENTRIES if n in data],
          "glazing_origin": None if o is None else [fnum(o.findtext("x")), fnum(o.findtext("y"))],
          "glazing_systems": found,
          "unresolved_materials": unresolved_materials(model, mats) if mats is not None else None,
          "warnings": warnings + [w for g in found for w in g["warnings"]]}, args)


def target_path(args, ext):
    if args.in_place:
        return args.input
    out = args.thmz if ext == ".thmz" else args.spec
    if not out:
        raise InputError("give %s OUTPUT (or --in-place)" % ("--thmz" if ext == ".thmz" else "--spec"))
    if os.path.exists(out) and os.path.samefile(out, args.input):
        raise InputError("output is the input file; pass --in-place to overwrite it")
    if not out.lower().endswith(ext):
        raise InputError("output must end with %s: %s" % (ext, out))
    return out


def write_json(path, doc):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2) + "\n")


def emit(doc, args):
    if getattr(args, "out", None):
        write_json(args.out, doc)
    else:
        print(json.dumps(doc, indent=2))


def build_parser():
    ap = argparse.ArgumentParser(prog="therm_glazing", description=__doc__.split("\n\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("show", help="list the glazing systems in a .thmz")
    p.add_argument("input", help=".thmz file")
    p.add_argument("--out", help="write the JSON report here instead of stdout")
    for name, helptext in (("patch", "edit a GUI-imported IGU and move its polygons (option A)"),
                           ("solid", "replace the IGU with Material polygons, or fill a spec region (option C)")):
        p = sp.add_parser(name, help=helptext)
        p.add_argument("input", help=".thmz file" + ("" if name == "patch" else ", or a therm-model spec .json"))
        p.add_argument("--changes", help="change set: a JSON file, '-' for stdin, or an inline JSON object")
        p.add_argument("--thmz", help="output .thmz (a new file)")
        p.add_argument("-o", "--output", "--thmx", dest="output",
                       help="output file; the extension picks the format (.thmx = THERM 7.8, .thmz = 8.1). "
                            "A .thmx input with no output writes <stem>_solid.thmx")
        if name == "solid":
            p.add_argument("--spec", help="output spec .json when the input is a spec")
        p.add_argument("--in-place", action="store_true", help="overwrite the input instead")
        p.add_argument("--library", action="append",
                       help="extra gases/BCs: therm-library project .json, .thmz, library .xml or lib folder")
        p.add_argument("--out", help="write the JSON report here instead of stdout")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    warnings = []
    try:
        {"show": cmd_show, "patch": cmd_patch, "solid": cmd_solid}[args.cmd](args, warnings)
    except InputError as exc:
        print("therm-glazing: error: %s" % exc, file=sys.stderr)
        return 1
    except ThermError as exc:
        print("therm-glazing: THERM file error: %s" % exc, file=sys.stderr)
        return 2
    for w in warnings:
        print("therm-glazing: warning: %s" % w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
