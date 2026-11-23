"""Look up, export and add LBNL THERM 8.1 materials, gases and boundary conditions.

Reads the installed THERM lib folder, the copies embedded in a .thmz, single
library XML files, or a project library JSON written by this script. Writes
project library JSON only; it never writes into the THERM lib folder.

Standard library only. Exit codes: 0 success, 1 user/input error, 2 a THERM
library file exists but cannot be parsed.
"""
import argparse
import copy
import json
import math
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

SKILL = "therm-library"
VERSION = 1
FORMAT = "therm-library-project"
DEFAULT_LIB = r"C:\Users\Public\LBNL\THERM8.1\lib"
KINDS = ("material", "pure_gas", "gas", "bc")
GROUPS = ("materials", "boundary_conditions", "gases")  # project JSON lists, as therm-model reads them
# THERM 8.1.30 ships BoundaryConditionsSteadyState.xml; newer builds use BoundaryConditions.xml.
LIB_FILES = ("Materials.xml", "Gases.xml", "BoundaryConditionsSteadyState.xml", "BoundaryConditions.xml")
THMZ_FILES = ("Materials.xml", "Gases.xml", "SteadyStateBC.xml")
UNIFIED_BC_TAGS = ("NoExchange", "SurfaceExchange", "PrescribedState")

# NFRC 100-2010 set, selected by UUID from the shipped library (8.1.30 names have the
# "Inerior" typo; LibraryFEMTherm's unified file fixes the names but keeps these UUIDs).
NFRC_UUIDS = [
    "1810f37a-de4d-4e2d-95d5-09fe01157c34",  # NFRC 100-2010 Exterior
    "61d7bd1c-22c6-4ea0-8720-0696e8c194ad",  # Adiabatic
    "51ecd33a-79d8-4288-b581-47b7365abd9f",  # Interior Aluminum Frame (convection only)
    "4efda3be-62bf-4fbe-b596-4e41b564fafb",  # Interior Thermally Broken Frame
    "e78f7996-e426-4d3f-9461-05d9dc7b6457",  # Interior Thermally Improved Frame
    "2459e81c-1a1b-4733-bab8-412771ac7bff",  # Interior Wood/Vinyl Frame
    "4c179ded-3f34-4102-b8d6-46d8e6d1c6d9",  # Interior (20 tilt) Aluminum Frame
    "4a87d7c7-7aa7-4775-96a0-487e1b43fd40",  # Interior (20 tilt) Thermally Broken Frame
    "e3afebb0-66ea-4a1d-a799-fb0b0709b846",  # Interior (20 tilt) Thermally Improved Frame
    "87afa340-f28d-4f6f-ae9b-2dc0e03f4911",  # Interior (20 tilt) Wood/Vinyl Frame
]
ADIABATIC_UUID = "61d7bd1c-22c6-4ea0-8720-0696e8c194ad"
# Fixed namespace so ISO preset UUIDs are the same on every run (re-running merges cleanly).
PRESET_NS = uuid.UUID("5b0c8f0e-2a64-4c8e-9a43-7f1d2e6b9c10")

SRC_ISO6946 = ("ISO 6946:2017 Table 5 (official preview, cdn.standards.iteh.ai sample of "
               "ISO 6946:2017): Rse = 0,04 m2K/W; Rsi range 0,1 to 0,2 m2K/W, values in 6.8")
SRC_ISO10077 = ("ISO 10077-2:2017 Annex E 'Surface resistances' (normative; content paywalled, "
                "only the table of contents was read)")
SRC_LIB = "THERM 8.1 shipped library (BoundaryConditionsSteadyState.xml)"


class UserError(Exception):
    pass


class LibError(Exception):
    pass


# ---------------------------------------------------------------- parsing

def num(el, path=None):
    """Number from an element: plain text, or a unified-format <Constant> child."""
    if el is not None and path:
        el = el.find(path)
    if el is None:
        return None
    if el.find("Constant") is not None:
        el = el.find("Constant")
    elif el.find("FromTimeSeries") is not None:
        return "series:" + (el.findtext("FromTimeSeries") or "").strip()
    text = (el.text or "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def boolean(text):
    return None if text is None else text.strip().lower() == "true"


def clean(d):
    return {k: v for k, v in d.items() if v is not None}


def xml_text(el):
    el = copy.deepcopy(el)
    for e in el.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        e.tail = None
    ET.indent(el, space="\t")
    return ET.tostring(el, encoding="unicode")


def material_record(el):
    props, mtype = {}, None
    solid, cavity, enclosure = el.find("Solid"), el.find("Cavity"), el.find("RadiationEnclosure")
    if solid is not None:
        mtype = "solid"
        ir = solid.find("Optical/Integrated/Infrared")
        props = clean({
            "conductivity_w_mk": num(solid, "HygroThermal/ThermalConductivityDry"),
            "emissivity_front": num(ir, "Emissivity-Front"),
            "emissivity_back": num(ir, "Emissivity-Back"),
            "density_kg_m3": num(solid, "HygroThermal/BulkDensity"),
            "specific_heat_j_kgk": num(solid, "HygroThermal/SpecificHeatCapacityDry"),
        })
    elif cavity is not None:
        mtype = "cavity"
        props = clean({
            "cavity_standard": cavity.findtext("CavityStandard"),
            "gas": cavity.findtext("Gas"),
            "emissivity_side1": num(cavity, "EmissivitySide1"),
            "emissivity_side2": num(cavity, "EmissivitySide2"),
        })
    elif enclosure is not None:
        mtype = "radiation_enclosure"
        props = clean({"emissivity_default": num(enclosure, "EmissivityDefault")})
    return "material", mtype, props


def abc(el):
    return None if el is None else [num(el, k) for k in ("A", "B", "C")]


def gas_record(el):
    if el.tag == "PureGas":
        p = el.find("Properties")
        return "pure_gas", "pure", clean({
            "molecular_weight": num(p, "MolecularWeight"),
            "specific_heat_ratio": num(p, "SpecificHeatRatio"),
            "conductivity_abc": abc(p.find("Conductivity")) if p is not None else None,
            "viscosity_abc": abc(p.find("Viscosity")) if p is not None else None,
            "specific_heat_abc": abc(p.find("SpecificHeat")) if p is not None else None,
        })
    comps = [{"fraction": num(c, "Fraction"), "pure_gas": c.findtext("PureGas")}
             for c in el.findall("Components/Component")]
    return "gas", "mixture", {"components": comps}


RADIATION_MODELS = {"AutomaticEnclosure": "automatic_enclosure", "BlackBodyRadiation": "black_body",
                    "BlackBody": "black_body", "ManualEnclosure": "manual_enclosure",
                    "FixedCoefficient": "fixed_coefficient"}


def radiation_props(parent):
    for tag, model in RADIATION_MODELS.items():
        r = parent.find(tag) if parent is not None else None
        if r is not None:
            return clean({"radiation_model": model, "radiation_temperature_c": num(r, "Temperature"),
                          "emissivity": num(r, "Emissivity"), "view_factor": num(r, "ViewFactor"),
                          "radiation_coefficient_w_m2k": num(r, "Coefficient")})
    return {"radiation_model": None}


def bc_record(el):
    simp, comp = el.find("Simplified"), el.find("Comprehensive")
    rs, se = el.find("RadiationSurface"), el.find("SurfaceExchange")
    if simp is not None:
        h = num(simp, "FilmCoefficient")
        return "bc", "simplified", clean({
            "temperature_c": num(simp, "Temperature"), "film_coefficient_w_m2k": h,
            "relative_humidity": num(simp, "RelativeHumidity"), "adiabatic": h == 0 or None})
    if comp is not None:
        return "bc", "comprehensive", clean(dict({
            "relative_humidity": num(comp, "RelativeHumidity"),
            "convection_temperature_c": num(comp, "Convection/Temperature"),
            "film_coefficient_w_m2k": num(comp, "Convection/FilmCoefficient"),
            "flux_w_m2": num(comp, "ConstantFlux/Flux")}, **radiation_props(comp.find("Radiation"))))
    if rs is not None:
        return "bc", "radiation_surface", clean({
            "temperature_c": num(rs, "Temperature"), "emissivity": num(rs, "Emissivity"),
            "default": boolean(rs.findtext("Default"))})
    if el.find("NoExchange") is not None:
        return "bc", "adiabatic", {"adiabatic": True}
    if se is not None:
        return "bc", "comprehensive", clean(dict({
            "relative_humidity": num(se, "RelativeHumidity"),
            "convection_model": se.findtext("Convection/Model"),
            "convection_temperature_c": num(se, "Convection/AirTemperature"),
            "film_coefficient_w_m2k": num(se, "Convection/FilmCoefficient"),
            "flux_w_m2": num(se, "Flux")}, **radiation_props(se)))
    ps = el.find("PrescribedState")
    if ps is not None:
        return "bc", "prescribed", clean({"temperature_c": num(ps, "Temperature"),
                                          "relative_humidity": num(ps, "RelativeHumidity")})
    return "bc", None, {}


def parse_xml_records(data, path, entry):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LibError("cannot parse %s (%s): %s" % (entry, path, exc))
    if root.tag == "Materials":
        elems, parser = root.findall("Material"), material_record
    elif root.tag == "Gases":
        elems, parser = [e for e in root if e.tag in ("PureGas", "Gas")], gas_record
    elif root.tag == "BoundaryConditions":
        elems, parser = root.findall("BoundaryCondition"), bc_record
    else:
        raise UserError("%s in %s is not a THERM Materials, Gases or BoundaryConditions file "
                        "(root element <%s>)" % (entry, path, root.tag))
    fmt = "therm-8.1"
    if any(e.find(t) is not None for e in elems for t in UNIFIED_BC_TAGS):
        fmt = "libraryfemtherm-unified"
    out = []
    for el in elems:
        kind, rtype, props = parser(el)
        rec = clean({
            "kind": kind, "uuid": (el.findtext("UUID") or "").strip(), "name": el.findtext("Name") or "",
            "type": rtype, "protected": boolean(el.findtext("Protected")), "color": el.findtext("Color"),
            "project_name": el.findtext("ProjectName") or el.findtext("ProjectNameTag") or None,
        })
        if kind == "bc":
            rec["igu_surface"] = boolean(el.findtext("IGUSurface")) or False
        rec.update({"properties": props, "source": {"path": path, "entry": entry},
                    "xml_format": fmt, "xml": xml_text(el)})
        out.append(rec)
    return out


def load_source(path, warnings):
    if os.path.isdir(path):
        found = [f for f in LIB_FILES if os.path.isfile(os.path.join(path, f))]
        if not found:
            raise UserError("no THERM library files (%s) in %s" % (", ".join(LIB_FILES), path))
        if "BoundaryConditionsSteadyState.xml" in found and "BoundaryConditions.xml" in found:
            warnings.append("%s has both BC files; reading both, duplicate UUIDs keep the first" % path)
        if not any(f.startswith("BoundaryConditions") for f in found):
            warnings.append("%s has no BoundaryConditionsSteadyState.xml or BoundaryConditions.xml" % path)
        recs = []
        for f in found:
            with open(os.path.join(path, f), "rb") as fh:
                recs += parse_xml_records(fh.read(), path, f)
        return recs
    if not os.path.isfile(path):
        raise UserError("source not found: %s" % path)
    low = path.lower()
    if low.endswith(".thmz"):
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise UserError("not a readable .thmz (zip) file: %s (%s)" % (path, exc))
        with zf:
            names = set(zf.namelist())
            recs = []
            for f in THMZ_FILES:
                if f in names:
                    recs += parse_xml_records(zf.read(f), path, f)
                else:
                    # shortcut: .json entry variants (LibraryFEMTherm) are not read; add if needed.
                    warnings.append("%s has no %s entry" % (path, f))
            return recs
    if low.endswith(".json"):
        return read_project(path, "8.1", warnings)["records"]
    if low.endswith(".xml"):
        with open(path, "rb") as fh:
            return parse_xml_records(fh.read(), path, os.path.basename(path))
    raise UserError("unsupported source %s: give a lib folder, .thmz, .xml or project .json" % path)


def read_project(path, version="8.1", warnings=None):
    """Read a project JSON: lists under materials / boundary_conditions / gases (the shape
    therm-model loads). A record with "xml" but no "properties" is parsed from its xml.
    With version 8.1 and a warnings list, a 7.8-only record (thmx, no xml) is skipped with a warning."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise UserError("cannot read project JSON %s: %s" % (path, exc))
    if not isinstance(doc, dict) or not any(isinstance(doc.get(g), list) for g in GROUPS):
        raise UserError("%s is not a project library file (no %s list)" % (path, " / ".join(GROUPS)))
    records = []
    for group in GROUPS:
        for i, r in enumerate(doc.get(group) or []):
            where = "%s %s[%d]" % (path, group, i)
            if not isinstance(r, dict) or not r.get("name"):
                raise UserError("%s needs at least a name" % where)
            r = dict(r)
            if version == "8.1" and warnings is not None and "thmx" in r and not r.get("xml"):
                warnings.append("%s %r is a THERM 7.8-only record (thmx, no xml); skipped" % (where, r["name"]))
                continue
            if "properties" not in r and r.get("xml"):
                root = {"materials": "Materials", "gases": "Gases"}.get(group, "BoundaryConditions")
                parsed = parse_xml_records(("<%s>%s</%s>" % (root, r["xml"], root)).encode("utf-8"), path, where)
                if parsed:
                    r = dict(parsed[0], **{k: v for k, v in r.items() if k != "source"})
            if r.get("kind") not in KINDS or group_of(r["kind"]) != group:
                raise UserError("%s: kind must be one of %s and match the list it is in"
                                % (where, "/".join(k for k in KINDS if group_of(k) == group)))
            r.setdefault("uuid", "")
            records.append(r)
    doc["records"] = records
    return doc


def load_sources(args, warnings):
    paths = args.source or [args.lib or DEFAULT_LIB]
    recs, seen = [], set()
    for p in paths:
        for r in load_source(p, warnings):
            key = (r["kind"], r["uuid"])
            if key not in seen:
                seen.add(key)
                recs.append(r)
    return paths, recs


# ---------------------------------------------------------------- output

def counts(records):
    c = {k: 0 for k in KINDS}
    for r in records:
        c[r["kind"]] += 1
    return {"materials": c["material"], "pure_gases": c["pure_gas"], "gases": c["gas"], "bcs": c["bc"]}


def group_of(kind):
    return {"material": "materials", "bc": "boundary_conditions"}.get(kind, "gases")


def document(records, **extra):
    doc = {"skill": SKILL, "version": VERSION, "format": FORMAT}
    doc.update(extra)
    doc["counts"] = counts(records)
    for g in GROUPS:
        doc[g] = [r for r in records if group_of(r["kind"]) == g]
        if any("thmx" in r for r in doc[g]):  # 7.8 records carry no uuid key (contract shape)
            doc[g] = [{k: v for k, v in r.items() if not (k == "uuid" and v == "" and "thmx" in r)}
                      for r in doc[g]]
    return doc


def merge(existing, new):
    """Merge by (kind, uuid). A different record with the same kind and name is refused,
    because THERM resolves BCs (and gas components) by name."""
    out = list(existing)
    for r in new:
        same = [i for i, e in enumerate(out) if e["kind"] == r["kind"] and e["uuid"] == r["uuid"]
                and (r["uuid"] or e["name"] == r["name"])]  # records without a UUID match by name
        clash = [e for e in out if e["kind"] == r["kind"] and e["name"] == r["name"] and e["uuid"] != r["uuid"]]
        if clash:
            raise UserError("%s %r already exists with UUID %s; rename it or remove the old record"
                            % (r["kind"], r["name"], clash[0]["uuid"]))
        if same:
            out[same[0]] = r
        else:
            out.append(r)
    return out


def check_out_path(out, args):
    if not out.lower().endswith(".json"):
        raise UserError("--out must be a .json project file: %s" % out)
    target = os.path.dirname(os.path.abspath(out))
    libs = [args.lib or DEFAULT_LIB] + [p for p in (args.source or []) if os.path.isdir(p)]
    is_lib = any(os.path.normcase(os.path.abspath(lib)) == os.path.normcase(target) for lib in libs)
    if getattr(args, "ini", None) or getattr(args, "therm_version", None) == "7.8":
        is_lib = is_lib or (os.path.isdir(target) and any(f.lower().endswith(".lib") for f in os.listdir(target)))
    if is_lib or any(os.path.isfile(os.path.join(target, f)) for f in LIB_FILES):
        raise UserError("refusing to write into a THERM library folder: %s" % target)


def emit(records, args, merge_out, **extra):
    if extra.get("therm_version") == "7.8":
        records = [dict(r, uuid=r.get("uuid", "")) for r in records]  # merge keys on (kind, uuid, name)
    if args.out:
        check_out_path(args.out, args)
        if merge_out and os.path.exists(args.out):
            records = merge(read_project(args.out)["records"], records)
    doc = document(records, **extra)
    if args.out:
        text = json.dumps(doc, indent=2)
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as exc:
            raise UserError("cannot write output: %s" % exc)
    else:
        print(json.dumps(doc, indent=2))


# ---------------------------------------------------------------- commands

def cmd_find(args, warnings):
    paths, recs = load_sources(args, warnings)
    q = (getattr(args, "query", None) or "").lower()
    hits = [r for r in recs if (not args.kind or r["kind"] == args.kind)
            and (q in r["name"].lower() or q in r["uuid"].lower())]
    if not args.full:
        hits = [{k: v for k, v in r.items() if k != "xml"} for r in hits]
    emit(hits, args, False, sources=paths, query=getattr(args, "query", None), kind=args.kind,
         total=counts(recs), warnings=warnings)


def with_dependencies(selected, pool, warnings):
    """Add the gas mixture a cavity names and the pure gases a mixture uses (both by name)."""
    out, keys = list(selected), {(r["kind"], r["uuid"]) for r in selected}

    def add(kind, name, why):
        found = [r for r in pool if r["kind"] == kind and r["name"] == name]
        if not found:
            warnings.append("%s needs %s %r, which is not in the source" % (why, kind, name))
        for r in found:
            if (r["kind"], r["uuid"]) not in keys:
                keys.add((r["kind"], r["uuid"]))
                out.append(r)
    for r in list(out):
        if r["kind"] == "material" and r.get("type") == "cavity" and r["properties"].get("gas"):
            add("gas", r["properties"]["gas"], "cavity %r" % r["name"])
    for r in list(out):
        if r["kind"] == "gas":
            for c in r["properties"].get("components", []):
                add("pure_gas", c["pure_gas"], "gas %r" % r["name"])
    return out


def cmd_export(args, warnings):
    if not (args.uuid or args.name):
        raise UserError("export needs at least one --uuid or --name")
    paths, recs = load_sources(args, warnings)
    pool = [r for r in recs if not args.kind or r["kind"] == args.kind]
    selected = []
    for field, wanted in (("uuid", args.uuid or []), ("name", args.name or [])):
        for w in wanted:
            hits = [r for r in pool if r[field] == w]
            if not hits:
                near = [r["name"] for r in pool if w.lower() in r[field].lower()][:5]
                raise UserError("no record with %s %r%s" % (field, w, "; similar: %s" % near if near else ""))
            selected += [r for r in hits if r not in selected]
    emit(with_dependencies(selected, recs, warnings), args, True, sources=paths, warnings=warnings)


def require(cond, msg):
    if not cond:
        raise UserError(msg)


def finite(name, v):
    require(v is not None and math.isfinite(v), "%s must be a finite number" % name)
    return v


def fmt(v):
    return repr(round(v, 9))


def sub(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def bc_element(uid, name, color, body):
    el = ET.Element("BoundaryCondition")
    for tag, text in (("UUID", uid), ("Name", name), ("ProjectNameTag", ""), ("Protected", "false"),
                      ("Color", color), ("IGUSurface", "false")):
        sub(el, tag, text)
    el.append(body)
    return el


def simplified_bc(uid, name, color, temp, h, rh):
    body = ET.Element("Simplified")
    sub(body, "Temperature", fmt(temp))
    sub(body, "FilmCoefficient", fmt(h))
    sub(body, "RelativeHumidity", fmt(rh))
    return bc_element(uid, name, color, body)


def new_record(el, origin):
    root = ET.Element("Materials" if el.tag == "Material" else "BoundaryConditions")
    root.append(el)
    rec = parse_xml_records(ET.tostring(root), origin, origin)[0]
    rec["source"] = {"created_by": SKILL, "command": origin}
    return rec


def validate_common(a):
    require(a.name and a.name.strip(), "--name must not be empty")
    require(a.color.startswith("0x") and len(a.color) == 8, "--color must look like 0xRRGGBB")


def cmd_add(args, warnings):
    validate_common(args)
    uid = str(uuid.uuid4())
    if args.what == "material":
        k = finite("--conductivity", args.conductivity)
        require(k > 0, "--conductivity must be > 0 W/mK (got %s)" % k)
        e_back = args.emissivity if args.emissivity_back is None else args.emissivity_back
        for flag, e in (("--emissivity", args.emissivity), ("--emissivity-back", e_back)):
            require(0 <= finite(flag, e) <= 1, "%s must be between 0 and 1 (got %s)" % (flag, e))
        el = ET.Element("Material")
        for tag, text in (("UUID", uid), ("Name", args.name), ("Protected", "false"), ("Color", args.color)):
            sub(el, tag, text)
        solid = sub(el, "Solid")
        sub(sub(solid, "HygroThermal"), "ThermalConductivityDry", fmt(k))
        ir = sub(sub(sub(solid, "Optical"), "Integrated"), "Infrared")
        sub(ir, "Emissivity-Front", fmt(args.emissivity))
        sub(ir, "Emissivity-Back", fmt(e_back))
    else:
        t, h, rh = finite("--temperature", args.temperature), finite("--film-coefficient", args.film_coefficient), args.rh
        require(t > -273.15, "--temperature must be above -273.15 C")
        require(h >= 0, "--film-coefficient must be >= 0 W/m2K (0 means adiabatic)")
        require(0 <= finite("--rh", rh) <= 1, "--rh must be between 0 and 1")
        if not args.comprehensive:
            el = simplified_bc(uid, args.name, args.color, t, h, rh)
        else:
            tr = t if args.rad_temperature is None else finite("--rad-temperature", args.rad_temperature)
            require(tr > -273.15, "--rad-temperature must be above -273.15 C")
            require(0 <= finite("--emissivity", args.emissivity) <= 1, "--emissivity must be between 0 and 1")
            require(0 <= finite("--view-factor", args.view_factor) <= 1, "--view-factor must be between 0 and 1")
            body = ET.Element("Comprehensive")
            sub(body, "RelativeHumidity", fmt(rh))
            conv = sub(body, "Convection")
            sub(conv, "Temperature", fmt(t))
            sub(conv, "FilmCoefficient", fmt(h))
            sub(sub(body, "ConstantFlux"), "Flux", fmt(finite("--flux", args.flux)))
            rad = sub(body, "Radiation")
            model = sub(rad, "AutomaticEnclosure" if args.radiation == "automatic" else "BlackBodyRadiation")
            sub(model, "Temperature", fmt(tr))
            sub(model, "Emissivity", fmt(args.emissivity))
            if args.radiation == "blackbody":
                sub(model, "ViewFactor", fmt(args.view_factor))
            el = bc_element(uid, args.name, args.color, body)
    emit([new_record(el, "add " + args.what)], args, True, warnings=warnings)


def iso_presets(t_ext, t_int):
    def check(field, value, source, confirmed):
        return {"field": field, "value": value, "source": source, "confirmed": confirmed}
    temp_note = ("ISO 10077-2 U and psi do not depend on the temperatures in a linear steady-state "
                 "model; 20/0 C is a common convention, not read from the standard")
    rows = [
        ("ISO Exterior Rse 0.04 (%g C)" % t_ext, "0x0080C0", t_ext, 0.04, [
            check("surface_resistance_m2k_w", 0.04, SRC_ISO6946, True),
            check("surface_resistance_m2k_w (frames)", 0.04, SRC_ISO10077, False),
            check("temperature_c", t_ext, temp_note, False)]),
        ("ISO Interior Rsi 0.13 normal (%g C)" % t_int, "0xFF8040", t_int, 0.13, [
            check("surface_resistance_m2k_w", 0.13, SRC_ISO6946 + "; 0.13 itself is in 6.8, not in the "
                  "preview (only the 0,1 to 0,2 range is)", False),
            check("surface_resistance_m2k_w (frames)", 0.13, SRC_ISO10077, False),
            check("temperature_c", t_int, temp_note, False)]),
        ("ISO Interior Rsi 0.20 reduced radiation (%g C)" % t_int, "0xFFC080", t_int, 0.20, [
            check("surface_resistance_m2k_w", 0.20, SRC_ISO10077 + "; reduced radiation/convection zones "
                  "(edges, corners) per common practice", False),
            check("temperature_c", t_int, temp_note, False)]),
    ]
    recs = []
    for name, color, temp, r_s, checks in rows:
        el = simplified_bc(str(uuid.uuid5(PRESET_NS, name)), name, color, temp, round(1 / r_s, 6), 0.5)
        rec = new_record(el, "presets iso")
        rec["properties"]["surface_resistance_m2k_w"] = r_s
        checks.append(check("relative_humidity", 0.5, "THERM default; does not affect U-factor", True))
        rec["preset"] = {"set": "iso-10077-2", "checks": checks,
                         "needs_engineer_check": not all(c["confirmed"] for c in checks)}
        recs.append(rec)
    adi_el = simplified_bc(ADIABATIC_UUID, "Adiabatic", "0x000000", 0, 0, 0.5)
    adi_el.find("Protected").text = "true"  # identical to the shipped record
    adi = new_record(adi_el, "presets iso")
    adi["preset"] = {"set": "iso-10077-2", "needs_engineer_check": False,
                     "checks": [check("record", "Adiabatic", SRC_LIB + ", same UUID and values", True)]}
    return recs + [adi]


def cmd_presets(args, warnings):
    if args.set == "iso":
        recs = iso_presets(finite("--exterior-temp", args.exterior_temp),
                           finite("--interior-temp", args.interior_temp))
        emit(recs, args, True, preset="iso-10077-2", warnings=warnings)
        return
    paths, pool = load_sources(args, warnings)
    by_uuid = {r["uuid"]: r for r in pool if r["kind"] == "bc"}
    if NFRC_UUIDS[0] not in by_uuid:
        raise UserError("NFRC 100-2010 Exterior (UUID %s) not found in %s; point --lib at the THERM 8.1 lib"
                        % (NFRC_UUIDS[0], ", ".join(paths)))
    recs = []
    for u in NFRC_UUIDS:
        if u not in by_uuid:
            warnings.append("NFRC preset BC %s not in the source; skipped" % u)
            continue
        rec = dict(by_uuid[u], preset={"set": "nfrc-100-2010", "needs_engineer_check": False,
                                       "checks": [{"field": "record", "source": SRC_LIB + " (copied as-is)",
                                                   "confirmed": True}]})
        recs.append(rec)
    emit(recs, args, True, preset="nfrc-100-2010", sources=paths, warnings=warnings)


# ---------------------------------------------------------------- THERM 7.8
# 7.8 libraries are binary MFC CArchive files that cannot be edited, so 7.8 records travel as
# JSON "thmx" dicts (research/library-record-78.md) and are written inline into each .thmx.

DEFAULT_INI_78 = r"C:\Users\Public\LBNL\Settings\therm7.8.ini"
EXE_78 = [r"C:\Program Files (x86)\lbnl\THERM7.8\THERM7.exe", r"C:\Program Files\lbnl\THERM7.8\THERM7.exe"]
INI_LIB_KEYS = {"MaterialLibName": "material", "BCLibName": "bc", "GasLibName": "gas"}
ASSET_78 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "records-78.json")
MAT_KEYS = ("Name", "Index", "Type", "Conductivity", "Tir", "EmissivityFront", "EmissivityBack", "RGBColor")
CAVITY_KEYS = ("Name", "Index", "Type", "Conductivity", "Tir", "Absorptivity", "EmissivityFront",
               "EmissivityBack", "RGBColor", "CavityModel")
BC_KEYS = ("Name", "Type", "H", "HeatFLux", "Temperature", "Tr", "Hr", "Ei", "Viewfactor", "RadiationModel",
           "ConvectionFlag", "FluxFlag", "RadiationFlag", "ConstantTemperatureFlag", "EmisModifier", "RGBColor")
CLASS_KIND = {"CMaterial": "material", "CBoundaryCondition": "bc", "CCavityGas": "gas"}


def normalise_name(name):
    """Copied verbatim from research/library-record-78.md section 3 (no cross-skill imports)."""
    name = re.sub(r"^(?:[^\s:]+:)+", "", name.strip())
    return re.sub(r"(?:_Cavity_\d+)+$", "", name).strip()


def installed_78(exes=None, ini=None):
    return any(os.path.isfile(p) for p in (exes or EXE_78)) or os.path.isfile(ini or DEFAULT_INI_78)


def version_for(args, installed=None):
    """--therm-version wins; else the source file type (.thmx/.lib/.ini = 7.8, .thmz/.xml = 8.1);
    else 7.8 when it is installed, else 8.1."""
    if getattr(args, "therm_version", None):
        return args.therm_version
    for p in (getattr(args, "source", None) or []) + [getattr(args, "lib", None) or ""]:
        low = p.lower()
        if low.endswith((".thmx", ".lib", ".ini")):
            return "7.8"
        if low.endswith((".thmz", ".xml")):
            return "8.1"
        if low.endswith(".json") and os.path.isfile(p):
            v = json_version(p)
            if v:
                return v
        if p and os.path.isdir(p):
            files = [f.lower() for f in os.listdir(p)]
            if any(f.endswith(".lib") for f in files):
                return "7.8"
            if any(f.endswith(".xml") for f in files):
                return "8.1"
    if getattr(args, "ini", None):
        return "7.8"
    out = getattr(args, "out", None)
    if out and os.path.isfile(out) and json_version(out):  # merging keeps the project's version
        return json_version(out)
    return "7.8" if (installed_78() if installed is None else installed) else "8.1"


def json_version(path):
    """7.8 when a project JSON holds only 7.8 records (thmx, no xml), 8.1 when any has xml."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        recs = [r for g in GROUPS for r in (doc.get(g) or []) if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return None
    if any(r.get("xml") for r in recs):
        return "8.1"
    return "7.8" if any("thmx" in r for r in recs) else None


class _Reader:
    def __init__(self, data):
        self.d, self.i = data, 0

    def u8(self):
        self.i += 1
        return self.d[self.i - 1]

    def u16(self):
        self.i += 2
        return int.from_bytes(self.d[self.i - 2:self.i], "little")

    def cstring(self):
        """MFC CString: <len8>, or FF <len16>, or FF FE FF <len> then UTF-16LE (Unicode build)."""
        unicode, n = False, self.u8()
        if n == 0xFF:
            n = self.u16()
            if n == 0xFFFE:
                unicode, n = True, self.u8()
                if n == 0xFF:
                    n = self.u16()
        if unicode:
            raw = self.d[self.i:self.i + 2 * n]
            self.i += 2 * n
            if len(raw) != 2 * n:
                raise ValueError("string runs past end of file")
            return raw.decode("utf-16-le")
        raw = self.d[self.i:self.i + n]
        self.i += n
        if len(raw) != n:
            raise ValueError("string runs past end of file")
        return raw.decode("cp1252", errors="replace")


def lib_names(path):
    """Names in a THERM 7.8 binary .lib. Returns (class or None, header count, [names]).
    Object libraries (material, BC, gas): an optional 01BB version word, an optional default-name
    string, a u16 count, the class header FF FF <schema> <len> <class>, a 2-byte record tag, then each
    record's name; later records start with the class reference 01 80 and the same tag. String-list
    libraries (UFACTOR, attributes) are a u16 count followed by that many strings."""
    with open(path, "rb") as fh:
        data = fh.read()
    m = re.search(rb"\xff\xff..(..)(C[A-Za-z]+)", data, re.S)
    if m and int.from_bytes(m.group(1), "little") == len(m.group(2)):
        cls = m.group(2).decode("ascii")
        count = int.from_bytes(data[m.start() - 2:m.start()], "little")
        tag = data[m.end():m.end() + 2]
        starts = [m.end() + 2] + [k.end() for k in re.finditer(re.escape(b"\x01\x80" + tag), data)]
        names = []
        for s in starts:
            r = _Reader(data)
            r.i = s
            try:
                names.append(r.cstring())
            except (ValueError, IndexError):
                continue
        if len(names) != count:
            raise LibError("%s: header says %d %s records, parser found %d" % (path, count, cls, len(names)))
        return cls, count, names
    r = _Reader(data)
    count = r.u16()
    try:
        names = [r.cstring() for _ in range(count)]
    except (ValueError, IndexError) as exc:
        raise LibError("%s: not a THERM 7.8 library (%s)" % (path, exc))
    if r.i != len(data):
        raise LibError("%s: %d unread bytes after %d strings" % (path, len(data) - r.i, count))
    return None, count, names


def read_ini_libs(ini):
    """{kind: path} for the ini's MaterialLibName, BCLibName and GasLibName keys ([THERM] section)."""
    try:
        with open(ini, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise UserError("cannot read THERM 7.8 ini %s: %s" % (ini, exc))
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("cp1252", "replace")
    out, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]").upper()
        elif section == "THERM" and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in INI_LIB_KEYS and v.strip():
                out[INI_LIB_KEYS[k.strip()]] = v.strip()
    return out


def resolve_path(p, base):
    """Windows paths are case-insensitive; on other systems match the file name ignoring case."""
    if os.path.isfile(p):
        return p
    folder, name = os.path.split(p.replace("\\", os.sep))
    folder = folder if os.path.isdir(folder) else base
    if folder and os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.lower() == name.lower():
                return os.path.join(folder, f)
    return None


def lib_records(ini, warnings):
    """Name-only records from the libraries the ini names. They carry no values: THERM fills them
    from its library only when a .thmx names them without an inline record."""
    recs = []
    for kind, p in sorted(read_ini_libs(ini).items()):
        path = resolve_path(p, os.path.dirname(ini))
        if not path:
            warnings.append("%s names %s, which does not exist" % (ini, p))
            continue
        cls, _, names = lib_names(path)
        if cls and CLASS_KIND.get(cls) != kind:
            warnings.append("%s holds %s records, expected %s" % (path, cls, kind))
        for n in names:
            recs.append({"kind": kind, "name": n, "library_only": True,
                         "source": {"path": path, "ini": ini}})
    return recs


def thmx_records(path):
    """Inline Material and BoundaryCondition elements of a .thmx, as contract records."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise UserError("cannot read .thmx %s: %s" % (path, exc))
    ns = "{http://windows.lbl.gov}"
    out = []
    for tag, kind in (("Materials/%sMaterial", "material"), ("BoundaryConditions/%sBoundaryCondition", "bc")):
        for el in root.findall(ns + tag % ns):
            out.append(thmx_record(kind, dict(el.attrib), {"path": path}))
    return out


def thmx_record(kind, attrs, source):
    """A contract record: name normalised, thmx attributes in contract order."""
    name = normalise_name(attrs.get("Name", ""))
    attrs["Name"] = name
    keys = BC_KEYS if kind == "bc" else (CAVITY_KEYS if attrs.get("Type") == "1" else MAT_KEYS)
    missing = [k for k in keys if k not in attrs]
    if missing:
        raise UserError("%s %r in %s lacks attributes %s" % (kind, name, source.get("path"), missing))
    return {"kind": kind, "name": name, "thmx": {k: attrs[k] for k in keys}, "source": source}


def load_78(args, warnings):
    """7.8 pool: bundled records + .thmx / project .json / .lib sources; with no --source, the ini's
    libraries (names only) plus the bundled records."""
    recs, paths = [], []
    sources = args.source or []
    if not sources:
        ini = getattr(args, "ini", None) or DEFAULT_INI_78
        paths.append(ini)
        if os.path.isfile(ini):
            recs += lib_records(ini, warnings)
        else:
            warnings.append("THERM 7.8 ini not found at %s; listing bundled records only" % ini)
        sources = [ASSET_78]
    for p in sources:
        paths.append(p)
        low = p.lower()
        if not os.path.isfile(p):
            raise UserError("source not found: %s" % p)
        if low.endswith(".thmx"):
            recs += thmx_records(p)
        elif low.endswith(".lib"):
            cls, _, names = lib_names(p)
            if cls not in CLASS_KIND:
                raise UserError("%s is a list of %d tag or attribute names, not material, BC or gas "
                                "records" % (p, len(names)))
            recs += [{"kind": CLASS_KIND[cls], "name": n, "library_only": True,
                      "source": {"path": p}} for n in names]
        elif low.endswith(".ini"):
            recs += lib_records(p, warnings)
        elif low.endswith(".json"):
            recs += [r for r in read_project(p, "7.8")["records"]]
        else:
            raise UserError("unsupported 7.8 source %s: give a .thmx, .lib, .ini or project .json" % p)
    out, seen = [], set()
    for r in recs:
        key = (r["kind"], r["name"], "thmx" in r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return paths, out


def fmt6(v):
    return "%.6f" % v


def bc_thmx(name, temp, h, color, adiabatic=False):
    """A combined-film (or adiabatic) BC in the shape THERM 7.8 writes; probe 4.1 hand-made wall."""
    return {"Name": name, "Type": "0" if adiabatic else "1", "H": fmt6(0 if adiabatic else h),
            "HeatFLux": fmt6(0), "Temperature": fmt6(0 if adiabatic else temp),
            "Tr": fmt6(0 if adiabatic else temp), "Hr": fmt6(0), "Ei": fmt6(1), "Viewfactor": fmt6(1),
            "RadiationModel": "0", "ConvectionFlag": "0" if adiabatic else "1",
            "FluxFlag": "1" if adiabatic else "0", "RadiationFlag": "0", "ConstantTemperatureFlag": "0",
            "EmisModifier": fmt6(1), "RGBColor": color}


def bundled_78(names=None):
    with open(ASSET_78, encoding="utf-8") as fh:
        doc = json.load(fh)
    recs = [dict(r, source={"path": "assets/records-78.json", "copied_from": r.get("copied_from")})
            for g in GROUPS for r in doc.get(g, [])]
    for r in recs:
        r.pop("copied_from", None)
    return recs if names is None else [r for r in recs if r["name"] in names]


def strip_78(records):
    """Drop in-memory helper keys before writing 7.8 records."""
    return [{k: v for k, v in r.items() if not (k == "uuid" and v == "")} for r in records]


def cmd_find_78(args, warnings):
    paths, recs = load_78(args, warnings)
    q = (getattr(args, "query", None) or "").lower()
    hits = [r for r in recs if (not args.kind or r["kind"] == args.kind) and q in r["name"].lower()]
    emit(strip_78(hits), args, False, therm_version="7.8", sources=paths, query=getattr(args, "query", None),
         kind=args.kind, total=counts(recs), warnings=warnings)


def cmd_export_78(args, warnings):
    if args.uuid:
        raise UserError("THERM 7.8 records have no UUID; use --name")
    if not args.name:
        raise UserError("export needs at least one --name")
    paths, recs = load_78(args, warnings)
    pool = [r for r in recs if not args.kind or r["kind"] == args.kind]
    selected = []
    for w in args.name:
        want = normalise_name(w)
        hits = [r for r in pool if r["name"] == want and "thmx" in r]
        if not hits:
            if any(r["name"] == want for r in pool):
                raise UserError("%r is only in a binary THERM 7.8 library, whose values cannot be read. "
                                "Name it in the model without an inline record (THERM fills it from its "
                                "library), or create a project record with 'add'" % want)
            near = [r["name"] for r in pool if want.lower() in r["name"].lower()][:5]
            raise UserError("no record named %r%s" % (want, "; similar: %s" % near if near else ""))
        selected += [h for h in hits[:1] if h not in selected]
    emit(strip_78(selected), args, True, therm_version="7.8", sources=paths, warnings=warnings)


def cmd_add_78(args, warnings):
    validate_common(args)
    name = normalise_name(args.name)
    require(name == args.name.strip(), "--name must not have a '<stem>:' prefix or '_Cavity_<n>' suffix")
    if args.what == "material":
        k = finite("--conductivity", args.conductivity)
        require(k > 0, "--conductivity must be > 0 W/mK (got %s)" % k)
        e_back = args.emissivity if args.emissivity_back is None else args.emissivity_back
        for flag, e in (("--emissivity", args.emissivity), ("--emissivity-back", e_back)):
            require(0 <= finite(flag, e) <= 1, "%s must be between 0 and 1 (got %s)" % (flag, e))
        thmx = {"Name": name, "Index": "1", "Type": "0", "Conductivity": fmt6(k), "Tir": fmt6(0),
                "EmissivityFront": fmt6(args.emissivity), "EmissivityBack": fmt6(e_back), "RGBColor": args.color}
        rec = {"kind": "material", "name": name, "thmx": thmx}
    else:
        require(not args.comprehensive, "THERM 7.8: add bc makes combined-film BCs only; --comprehensive "
                "is 8.1 only. Export a THERM-written BC from a .thmx instead")
        t, h = finite("--temperature", args.temperature), finite("--film-coefficient", args.film_coefficient)
        require(t > -273.15, "--temperature must be above -273.15 C")
        require(h >= 0, "--film-coefficient must be >= 0 W/m2K (0 means adiabatic)")
        rec = {"kind": "bc", "name": name, "thmx": bc_thmx(name, t, h, args.color, adiabatic=h == 0)}
    rec["source"] = {"created_by": SKILL, "command": "add " + args.what}
    emit([rec], args, True, therm_version="7.8", warnings=warnings)


NFRC_78 = ["NFRC 100-2010 Exterior", "Adiabatic", "Interior Aluminum Frame (convection only)",
           "Interior Thermally Broken Frame (convection only)", "Interior Thermally Improved Frame (convection only)",
           "Interior Wood/Vinyl Frame (convection only)", "Interior (20 tilt) Aluminum Frame (convection only)",
           "Interior (20 tilt) Thermally Broken Frame (convection only)",
           "Interior (20 tilt) Thermally Improved Frame (convection only)",
           "Interior (20 tilt) Interior Wood/Vinyl Frame (convection only)"]  # 7.8 BC.lib spelling
SRC_LIB_78 = "THERM 7.8.80 BC.lib record, as THERM wrote it into a solved .thmx (assets/records-78.json)"


def cmd_presets_78(args, warnings):
    if args.set == "nfrc":
        recs = bundled_78(set(NFRC_78))
        have = {r["name"] for r in recs}
        for n in NFRC_78:
            if n not in have:
                warnings.append("NFRC preset BC %r is not in the bundled 7.8 records; skipped" % n)
        for r in recs:
            r["preset"] = {"set": "nfrc-100-2010", "needs_engineer_check": False,
                           "checks": [{"field": "record", "source": SRC_LIB_78, "confirmed": True}]}
        recs.sort(key=lambda r: NFRC_78.index(r["name"]))
        emit(recs, args, True, therm_version="7.8", preset="nfrc-100-2010", warnings=warnings)
        return
    t_ext = finite("--exterior-temp", args.exterior_temp)
    t_int = finite("--interior-temp", args.interior_temp)
    recs = []
    for r81 in iso_presets(t_ext, t_int):  # same names, values and engineer-check flags as 8.1
        p = r81["properties"]
        adi = r81["name"] == "Adiabatic"
        thmx = bc_thmx(r81["name"], p.get("temperature_c", 0), p.get("film_coefficient_w_m2k", 0),
                       r81.get("color", "0x000000"), adiabatic=adi)
        recs.append({"kind": "bc", "name": r81["name"], "thmx": thmx,
                     "properties": p, "preset": r81["preset"],
                     "source": {"created_by": SKILL, "command": "presets iso"}})
    emit(recs, args, True, therm_version="7.8", preset="iso-10077-2", warnings=warnings)


# ---------------------------------------------------------------- CLI

def version_args(p):
    p.add_argument("--therm-version", choices=("7.8", "8.1"),
                   help="default: from the source file (.thmx/.lib/.ini = 7.8, .thmz/.xml = 8.1), "
                        "else 7.8 when installed, else 8.1")
    p.add_argument("--ini", help="THERM 7.8 settings ini whose MaterialLibName, BCLibName and GasLibName "
                                 "are listed (default %s)" % DEFAULT_INI_78)


def build_parser():
    ap = argparse.ArgumentParser(prog="therm_library",
                                 description="Find, export and add THERM 8.1 or 7.8 materials, gases and BCs.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def source_args(p):
        p.add_argument("--lib", help="THERM lib folder (default %s)" % DEFAULT_LIB)
        p.add_argument("--source", action="append",
                       help="lib folder, .thmz, single library .xml or project .json; repeatable; "
                            "overrides --lib")
        p.add_argument("--kind", choices=KINDS, help="only this record kind")
        version_args(p)

    def out_arg(p, merging):
        p.add_argument("--out", help="write JSON here instead of stdout%s"
                       % (" (merged into the file if it exists)" if merging else ""))

    for name, helptext in (("list", "list every record"), ("find", "search by name or UUID substring")):
        p = sp.add_parser(name, help=helptext)
        if name == "find":
            p.add_argument("query", help="case-insensitive substring of the name or UUID")
        source_args(p)
        p.add_argument("--full", action="store_true", help="include each record's raw XML")
        out_arg(p, False)

    p = sp.add_parser("export", help="write chosen records (plus gas dependencies) to a project JSON")
    source_args(p)
    p.add_argument("--uuid", action="append", help="exact UUID; repeatable")
    p.add_argument("--name", action="append", help="exact, case-sensitive name; repeatable")
    out_arg(p, True)

    p = sp.add_parser("add", help="create a new project record with a fresh UUID")
    p.add_argument("what", choices=("material", "bc"))
    p.add_argument("--name", required=True)
    p.add_argument("--color", default="0x808080", help="0xRRGGBB (default 0x808080)")
    p.add_argument("--conductivity", type=float, help="material: W/mK, > 0")
    p.add_argument("--emissivity", type=float, default=0.9,
                   help="material front emissivity, or comprehensive BC radiation emissivity (0..1, default 0.9)")
    p.add_argument("--emissivity-back", type=float, help="material back emissivity (default = front)")
    p.add_argument("--temperature", type=float, help="bc: air temperature, C")
    p.add_argument("--film-coefficient", type=float, help="bc: W/m2K (simplified = combined; 0 = adiabatic)")
    p.add_argument("--rh", type=float, default=0.5, help="bc relative humidity 0..1 (default 0.5)")
    p.add_argument("--comprehensive", action="store_true", help="bc: convection + separate radiation")
    p.add_argument("--radiation", choices=("automatic", "blackbody"), default="automatic",
                   help="comprehensive bc radiation model (default automatic enclosure)")
    p.add_argument("--rad-temperature", type=float, help="radiation temperature, C (default = --temperature)")
    p.add_argument("--view-factor", type=float, default=1.0, help="blackbody view factor 0..1 (default 1)")
    p.add_argument("--flux", type=float, default=0.0, help="comprehensive bc constant flux W/m2 (default 0)")
    p.add_argument("--lib", help=argparse.SUPPRESS)
    p.add_argument("--source", action="append", help=argparse.SUPPRESS)
    version_args(p)
    out_arg(p, True)

    p = sp.add_parser("presets", help="emit a ready-made BC set as project records")
    p.add_argument("set", choices=("nfrc", "iso"))
    p.add_argument("--lib", help="THERM lib folder for nfrc (default %s)" % DEFAULT_LIB)
    p.add_argument("--source", action="append", help="other source for nfrc; overrides --lib")
    p.add_argument("--kind", help=argparse.SUPPRESS)
    p.add_argument("--exterior-temp", type=float, default=0.0, help="iso exterior C (default 0)")
    p.add_argument("--interior-temp", type=float, default=20.0, help="iso interior C (default 20)")
    version_args(p)
    out_arg(p, True)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    warnings = []
    try:
        args.therm_version = version_for(args)
        if args.therm_version == "7.8":
            handler = {"list": cmd_find_78, "find": cmd_find_78, "export": cmd_export_78,
                       "add": cmd_add_78, "presets": cmd_presets_78}[args.cmd]
        else:
            handler = {"list": cmd_find, "find": cmd_find, "export": cmd_export,
                       "add": cmd_add, "presets": cmd_presets}[args.cmd]
        if args.cmd == "add" and args.what == "material":
            require(args.conductivity is not None, "add material needs --conductivity")
        if args.cmd == "add" and args.what == "bc":
            require(args.temperature is not None and args.film_coefficient is not None,
                    "add bc needs --temperature and --film-coefficient")
        handler(args, warnings)
    except UserError as exc:
        print("therm-library: error: %s" % exc, file=sys.stderr)
        return 1
    except LibError as exc:
        print("therm-library: library error: %s" % exc, file=sys.stderr)
        return 2
    for w in warnings:
        print("therm-library: warning: %s" % w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
