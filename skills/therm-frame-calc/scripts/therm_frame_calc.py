"""Frame calculations from therm-results JSON: ISO 10077-2 Uf and psi, or NFRC frame/edge values.

Pure arithmetic on the JSON that therm-results writes. It never runs THERM.
Standard library only.

  iso   Uf from a calibration-panel run and psi from a glazed run (ISO 10077-2).
  nfrc  Frame and Edge U-factors and lengths, as a table for WINDOW's frame library.

Exit codes: 0 success, 1 user/input error (missing file, case or tag, bad unit,
bad sign, result that cannot be physical).
"""
import argparse
import json
import math
import sys

SKILL = "therm-frame-calc"
VERSION = 1

ISO_DISCLAIMER = (
    "The ISO 10077-2 formulas used here come from the therm-skills research doc "
    "(research/headless-interface.md section 5), not from the standard text. Check them, "
    "the panel conductivity and the surface resistances against a licensed copy of "
    "ISO 10077-2 before using the numbers for certification or compliance work.")
NFRC_NOTE = (
    "NFRC certification currently uses THERM 7 / WINDOW 7 (NFRC simulation manual, "
    "Jan 2026 edition). Values from THERM 8.1 are for design checks until NFRC approves "
    "version 8.")
NFRC_NOTE_78 = (
    "NFRC certification uses THERM 7 / WINDOW 7 (NFRC simulation manual, Jan 2026 edition). "
    "These values come from a THERM 7.8 .thmx model, which has no glazing system, no Condensation "
    "Resistance case and no glass-plane projection. Check them against a full NFRC model made in "
    "the THERM GUI before certification.")
# Projection names as therm-results writes them.
PROJECTIONS = {
    "glass": "Projected in glass plane",
    "y": "Projected Y",
    "x": "Projected X",
    "total": "Total Length",
}
NFRC_EDGE_MM = 63.5  # NFRC 100 edge-of-glass zone height
# Relative gap allowed between |q|/dT and U*L of the displayed projection.
CONSISTENCY_TOL = 0.005


class UserError(Exception):
    pass


def load_results(path):
    """Load a therm-results JSON file and check that it is one."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise UserError("results file not found: %s" % path)
    except (OSError, ValueError) as exc:
        raise UserError("cannot read results JSON %s: %s" % (path, exc))
    if not isinstance(data, dict) or data.get("skill") != "therm-results":
        raise UserError("%s is not therm-results output (missing \"skill\": \"therm-results\")" % path)
    if data.get("version") != 1:
        raise UserError("%s: unsupported therm-results version %r (expected 1)" % (path, data.get("version")))
    length_unit = (data.get("units") or {}).get("length")
    if length_unit != "mm":
        raise UserError("%s: length unit is %r; this skill expects therm-results lengths in mm"
                        % (path, length_unit))
    return data


def find_case(data, case, path):
    for c in data.get("cases", []):
        if c.get("case") == case:
            return c
    have = [c.get("case") for c in data.get("cases", [])]
    raise UserError("%s: case %r not found (cases present: %s)" % (path, case, ", ".join(map(str, have)) or "none"))


def find_tag(case, name, path):
    for t in case.get("tags", []):
        if t.get("tag") == name:
            return t
    have = [t.get("tag") for t in case.get("tags", [])]
    raise UserError("%s: tag %r not found in case %r (tags present: %s)"
                    % (path, name, case.get("case"), ", ".join(map(str, have)) or "none"))


def positive(value, label):
    if value is None or not math.isfinite(value) or value <= 0:
        raise UserError("%s must be a positive number, got %r" % (label, value))
    return value


def l2d_from_tags(data, case_name, tags, path, warnings):
    """Sum |q|/dT over the tags. Returns (L2D in W/mK, per-tag rows)."""
    case = find_case(data, case_name, path)
    rows, signs = [], set()
    for name in tags:
        t = find_tag(case, name, path)
        q, dt = t.get("heat_flux_w_m2"), t.get("delta_t")
        if q is None or dt is None:
            raise UserError("%s: tag %r has no heat flux or DeltaT" % (path, name))
        positive(dt, "%s: DeltaT of tag %r" % (path, name))
        if q == 0:
            raise UserError("%s: tag %r has zero heat flow; it is not a heat-flow boundary" % (path, name))
        signs.add(q > 0)
        l2d = abs(q) / dt
        row = {"tag": name, "heat_flow_w_m": q, "delta_t": dt, "l2d_w_mk": l2d}
        shown = t.get("displayed")
        if shown and not shown.get("degenerate"):
            check = shown["u_factor"] * shown["length_mm"] / 1000.0
            row["check_u_times_length_w_mk"] = check
            row["check_projection"] = shown["type"]
            if abs(check - l2d) > CONSISTENCY_TOL * l2d:
                warnings.append("%s: tag %r |q|/dT=%.6g but U*L (%s) = %.6g; heat flux may not be "
                                "W per metre of depth" % (path, name, l2d, shown["type"], check))
        rows.append(row)
    if len(signs) > 1:
        raise UserError("%s: tags %s have heat flows of opposite sign. Sum tags on one side of the "
                        "section only (all interior or all exterior), or the flows cancel"
                        % (path, ", ".join(tags)))
    return sum(r["l2d_w_mk"] for r in rows), rows


def compute_iso(args):
    warnings = []
    bf = positive(args.bf, "--bf (projected frame width, mm)") / 1000.0
    bp = positive(args.bp, "--bp (visible panel width, mm)") / 1000.0
    bg_mm = args.bg if args.bg is not None else args.bp
    bg = positive(bg_mm, "--bg (visible glazing width, mm)") / 1000.0
    ug = positive(args.ug, "--ug (W/m2K)")
    if (args.up is None) == (args.panel_thickness is None):
        raise UserError("give exactly one of --up or --panel-thickness")
    panel_tags = args.tag
    glazed_tags = args.glazed_tag or args.tag

    assumptions = [
        "L2D = sum over the chosen tags of |HeatFlux| / DeltaT. therm-results labels HeatFlux W/m2, "
        "but THERM stores the heat flow per metre of section depth (W/m); U*L of each tag's "
        "displayed projection is checked against it.",
        "Sign convention: THERM's sign depends on the boundary direction (interior tags are "
        "negative in LBNL samples). Magnitudes are used; tags of mixed sign are refused.",
        "The chosen tags must carry the whole heat flow through the section: panel run tags %s, "
        "glazed run tags %s. Tags on one side only (all interior or all exterior)."
        % (", ".join(panel_tags), ", ".join(glazed_tags)),
        "Widths are entered in mm and converted to m for the formulas.",
    ]
    if args.bg is None:
        assumptions.append("bg was not given, so bg = bp (glazing replaces the panel at the same width).")

    if args.up is not None:
        up = positive(args.up, "--up (W/m2K)")
        up_source = {"method": "given", "up_w_m2k": up}
    else:
        d = positive(args.panel_thickness, "--panel-thickness (mm)") / 1000.0
        lam = positive(args.panel_conductivity, "--panel-conductivity (W/mK)")
        rsi = positive(args.rsi, "--rsi (m2K/W)")
        rse = positive(args.rse, "--rse (m2K/W)")
        up = 1.0 / (rsi + d / lam + rse)
        up_source = {"method": "Up = 1 / (Rsi + dp/lambda_p + Rse)", "rsi_m2k_w": rsi,
                     "dp_m": d, "lambda_p_w_mk": lam, "rse_m2k_w": rse, "up_w_m2k": up}
        assumptions.append("Up uses Rsi=%g and Rse=%g m2K/W (normal, not reduced-radiation, interior "
                           "film); check these against the BCs used in the panel run." % (rsi, rse))
        if lam != 0.035:
            warnings.append("panel conductivity %g W/mK differs from the ISO 10077-2 calibration "
                            "panel value 0.035 W/mK" % lam)

    panel = load_results(args.panel)
    glazed = load_results(args.glazed)
    if is_78(panel) or is_78(glazed):
        assumptions.append("THERM 7.8 .thmx results store no heat flux: therm-results derives the heat "
                           "flow as U x length x DeltaT (W/m, unsigned), so the sign check cannot catch "
                           "tags from opposite sides.")
    l2d_p, panel_rows = l2d_from_tags(panel, args.case, panel_tags, args.panel, warnings)
    l2d_g, glazed_rows = l2d_from_tags(glazed, args.case, glazed_tags, args.glazed, warnings)

    uf_num = l2d_p - up * bp
    if uf_num <= 0:
        raise UserError("L2D_panel (%.6g W/mK) <= Up*bp (%.6g W/mK), so Uf would be <= 0. Check bp, Up "
                        "and that the tags cover the whole section" % (l2d_p, up * bp))
    uf = uf_num / bf
    psi = l2d_g - uf * bf - ug * bg
    if psi < 0:
        warnings.append("psi is negative (%.6g W/mK); check the tags, bg and Ug" % psi)

    steps = [
        {"name": "L2D_panel", "formula": "sum(|q_tag| / dT_tag) over panel-run tags",
         "values": {"tags": panel_rows}, "result": l2d_p, "unit": "W/mK"},
        {"name": "Up", "formula": up_source["method"], "values": up_source, "result": up, "unit": "W/m2K"},
        {"name": "Uf", "formula": "Uf = (L2D_panel - Up*bp) / bf",
         "values": {"L2D_panel": l2d_p, "Up": up, "bp_m": bp, "bf_m": bf, "Up*bp": up * bp},
         "result": uf, "unit": "W/m2K"},
        {"name": "L2D_glazed", "formula": "sum(|q_tag| / dT_tag) over glazed-run tags",
         "values": {"tags": glazed_rows}, "result": l2d_g, "unit": "W/mK"},
        {"name": "psi", "formula": "psi = L2D_glazed - Uf*bf - Ug*bg",
         "values": {"L2D_glazed": l2d_g, "Uf": uf, "bf_m": bf, "Ug": ug, "bg_m": bg,
                    "Uf*bf": uf * bf, "Ug*bg": ug * bg},
         "result": psi, "unit": "W/mK"},
    ]
    return {
        "skill": SKILL, "version": VERSION, "mode": "iso", "standard": "ISO 10077-2",
        "disclaimer": ISO_DISCLAIMER,
        "inputs": {"panel_results": args.panel, "glazed_results": args.glazed, "case": args.case,
                   "panel_tags": panel_tags, "glazed_tags": glazed_tags,
                   "bf_mm": args.bf, "bp_mm": args.bp, "bg_mm": bg_mm, "up_w_m2k": up, "ug_w_m2k": ug},
        "steps": steps,
        "results": {"l2d_panel_w_mk": l2d_p, "uf_w_m2k": uf, "l2d_glazed_w_mk": l2d_g, "psi_w_mk": psi},
        "assumptions": assumptions,
        "warnings": warnings,
    }


def pick_projection(tag, which, path):
    if which == "displayed":
        p = tag.get("displayed")
        if not p or p.get("degenerate"):
            raise UserError("%s: tag %r has no usable displayed U-factor" % (path, tag.get("tag")))
        return p
    want = PROJECTIONS[which]
    for p in tag.get("projections", []):
        if (p.get("type") or "").lower() == want.lower() and not p.get("degenerate"):
            return p
    raise UserError("%s: tag %r has no usable %r projection; try --projection displayed"
                    % (path, tag.get("tag"), want))


def is_78(data):
    return data.get("therm_format") == "thmx-7.8"


def resolve_projection_78(data, projection, path, warnings):
    """THERM 7.8 .thmx: the glass-plane value falls back to Projected X after import, so it is
    never used. Default to Projected Y on a Sill; other sections need an explicit --projection."""
    section = data.get("cross_section") or "unknown"
    if projection == "glass":
        raise UserError("%s: THERM 7.8 .thmx results have no reliable glass-plane projection (it falls "
                        "back to Projected X after a .thmx import). Use --projection y for a sill, or "
                        "choose y, x or total for other sections." % path)
    if projection is None:
        if section.lower() != "sill":
            raise UserError("%s: cross-section is %r. On THERM 7.8 only a Sill has a checked default "
                            "(Projected Y = glass plane). Pass --projection y, x, total or displayed."
                            % (path, section))
        return "y"
    if section.lower() != "sill":
        warnings.append("%s: cross-section %r on THERM 7.8: which projection equals the glass plane was "
                        "not checked; this table uses --projection %s" % (path, section, projection))
    return projection


def compute_nfrc(args):
    warnings = []
    data = load_results(args.results)
    if is_78(data):
        args.projection = resolve_projection_78(data, args.projection, args.results, warnings)
    elif args.projection is None:
        args.projection = "glass"
    case = find_case(data, args.case, args.results)
    rows = []
    for role, name in (("Frame", args.frame_tag), ("Edge", args.edge_tag)):
        t = find_tag(case, name, args.results)
        p = pick_projection(t, args.projection, args.results)
        rows.append({"role": role, "tag": name, "u_factor_w_m2k": p["u_factor"],
                     "length_mm": p["length_mm"], "projection": p["type"],
                     "delta_t": t.get("delta_t"), "heat_flow_w_m": t.get("heat_flux_w_m2")})
        if role == "Edge" and abs(p["length_mm"] - NFRC_EDGE_MM) > 0.5:
            warnings.append("Edge length is %.3f mm; NFRC 100 uses a %.1f mm edge-of-glass zone"
                            % (p["length_mm"], NFRC_EDGE_MM))
    notes = None
    if is_78(data):
        if args.projection == "y" and (data.get("cross_section") or "").lower() == "sill":
            notes = "THERM 7.8 sill: Projected Y is used in place of the glass plane (equal on a sill)."
        else:
            notes = None
    elif args.projection != "glass":
        warnings.append("NFRC 100 reports frame and edge U-factors projected in the glass plane; "
                        "this table uses %s" % rows[0]["projection"])
    return {
        "skill": SKILL, "version": VERSION, "mode": "nfrc", "standard": "NFRC 100",
        "note": NFRC_NOTE_78 if is_78(data) else NFRC_NOTE,
        "inputs": {"results": args.results, "case": args.case, "frame_tag": args.frame_tag,
                   "edge_tag": args.edge_tag, "projection": args.projection},
        "table": rows,
        "simulation_error_pct": case.get("simulation_error_pct"),
        "assumptions": ([notes] if is_78(data) and notes else []) + ([
            "THERM 7.8 .thmx: the error energy norm is not available (simulation_error_pct is null)."]
            if is_78(data) else []) + [
            "Frame length is the projected frame width for WINDOW; Edge length should be the 63.5 mm "
            "edge-of-glass zone.",
            "WINDOW normally imports the THERM file directly; use this table to enter or check values by hand.",
        ],
        "warnings": warnings,
    }


def fmt(v):
    return "%.7g" % v if isinstance(v, float) else str(v)


def to_markdown(sheet):
    out = []
    if sheet["mode"] == "iso":
        r, i = sheet["results"], sheet["inputs"]
        out += ["# ISO 10077-2 frame calculation", "", "> " + sheet["disclaimer"], "", "## Inputs", "",
                "| Input | Value |", "|---|---|"]
        out += ["| %s | %s |" % (k, ", ".join(v) if isinstance(v, list) else fmt(v)) for k, v in i.items()]
        out += ["", "## Steps", ""]
        for s in sheet["steps"]:
            out.append("**%s** = %s %s  " % (s["name"], fmt(s["result"]), s["unit"]))
            out.append("`%s`" % s["formula"])
            for k, v in s["values"].items():
                if k == "tags":
                    for t in v:
                        out.append("- tag %s: q = %s W/m, dT = %s K, |q|/dT = %s W/mK"
                                   % (t["tag"], fmt(t["heat_flow_w_m"]), fmt(t["delta_t"]), fmt(t["l2d_w_mk"])))
                elif k != "method":
                    out.append("- %s = %s" % (k, fmt(v)))
            out.append("")
        out += ["## Results", "", "| Quantity | Value |", "|---|---|",
                "| L2D panel | %s W/mK |" % fmt(r["l2d_panel_w_mk"]), "| Uf | %s W/m2K |" % fmt(r["uf_w_m2k"]),
                "| L2D glazed | %s W/mK |" % fmt(r["l2d_glazed_w_mk"]), "| psi | %s W/mK |" % fmt(r["psi_w_mk"])]
    else:
        out += ["# NFRC frame and edge values for WINDOW", "", "> " + sheet["note"], "",
                "| Role | Tag | U-factor (W/m2K) | Length (mm) | Projection |", "|---|---|---|---|---|"]
        out += ["| %s | %s | %s | %s | %s |" % (r["role"], r["tag"], fmt(r["u_factor_w_m2k"]),
                                                fmt(r["length_mm"]), r["projection"]) for r in sheet["table"]]
    out += ["", "## Assumptions", ""] + ["- " + a for a in sheet["assumptions"]]
    if sheet["warnings"]:
        out += ["", "## Warnings", ""] + ["- " + w for w in sheet["warnings"]]
    return "\n".join(out) + "\n"


def build_parser():
    ap = argparse.ArgumentParser(prog=SKILL, description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)

    iso = sub.add_parser("iso", help="ISO 10077-2 Uf and psi from a panel run and a glazed run")
    iso.add_argument("--panel", required=True, help="therm-results JSON of the calibration-panel run")
    iso.add_argument("--glazed", required=True, help="therm-results JSON of the glazed run")
    iso.add_argument("--tag", action="append", required=True,
                     help="U-factor tag to sum in the panel run (repeat for several)")
    iso.add_argument("--glazed-tag", action="append",
                     help="tag to sum in the glazed run (repeatable; default: same as --tag)")
    iso.add_argument("--bf", type=float, required=True, help="projected frame width bf, mm")
    iso.add_argument("--bp", type=float, required=True, help="visible panel width bp, mm")
    iso.add_argument("--bg", type=float, help="visible glazing width bg, mm (default: bp)")
    iso.add_argument("--ug", type=float, required=True, help="centre-of-glass Ug, W/m2K")
    iso.add_argument("--up", type=float, help="panel Up, W/m2K (or give --panel-thickness)")
    iso.add_argument("--panel-thickness", type=float, help="panel thickness dp, mm, to compute Up")
    iso.add_argument("--panel-conductivity", type=float, default=0.035,
                     help="panel conductivity, W/mK (default 0.035)")
    iso.add_argument("--rsi", type=float, default=0.13, help="interior surface resistance for Up, m2K/W (default 0.13)")
    iso.add_argument("--rse", type=float, default=0.04, help="exterior surface resistance for Up, m2K/W (default 0.04)")

    nfrc = sub.add_parser("nfrc", help="Frame and Edge U-factors and lengths for WINDOW's frame library")
    nfrc.add_argument("results", help="therm-results JSON of the NFRC run")
    nfrc.add_argument("--frame-tag", default="Frame", help="frame tag name (default Frame)")
    nfrc.add_argument("--edge-tag", default="Edge", help="edge-of-glass tag name (default Edge)")
    nfrc.add_argument("--projection", choices=["glass", "displayed", "y", "x", "total"], default=None,
                      help="which projection to report (default: glass plane for 8.1 .thmz results; "
                           "Projected Y for a 7.8 .thmx sill, other 7.8 sections must name one)")

    for p in (iso, nfrc):
        p.add_argument("--case", default="U-factor", help="results case (default U-factor)")
        p.add_argument("--out", help="write JSON here instead of stdout")
        p.add_argument("--markdown", help="also write a Markdown calc sheet here")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        sheet = compute_iso(args) if args.mode == "iso" else compute_nfrc(args)
    except UserError as exc:
        print("%s: error: %s" % (SKILL, exc), file=sys.stderr)
        return 1
    for w in sheet["warnings"]:
        print("%s: warning: %s" % (SKILL, w), file=sys.stderr)
    text = json.dumps(sheet, indent=2)
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        else:
            print(text)
        if args.markdown:
            with open(args.markdown, "w", encoding="utf-8") as fh:
                fh.write(to_markdown(sheet))
    except OSError as exc:
        print("%s: error: cannot write output: %s" % (SKILL, exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
