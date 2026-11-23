#!/usr/bin/env python3
"""therm-model: build, inspect or patch an LBNL THERM cross-section (7.8 .thmx or 8.1 .thmz).

  build   SPEC.json [-o OUT.thmx|OUT.thmz] [--library FILE ...]   write a model from a section spec
  inspect FILE.thmx|FILE.thmz [--detail]                          summarise a model as JSON
  patch   IN [-o OUT] [edits ...]                                 apply small edits, keep everything else

The output file's extension picks the format: .thmx = THERM 7.8, .thmz = THERM 8.1.
With no output given, build and patch write a .thmx.

JSON result goes to stdout (or --out). Errors go to stderr. Exit 0 ok, 1 input error, 2 internal error.
Standard library only; Python 3.10+.
"""
import argparse
import copy
import datetime
import json
import os
import sys
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thmz_core as core  # noqa: E402
import thmx_core as thmx  # noqa: E402
from thmz_core import InputError, sub, xy, fmt  # noqa: E402

SKILL, VERSION = 'therm-model', 1
THERM_VERSION = 'Version 8.1.30.0'
WINDOW_SECTIONS = ('Sill', 'Jamb', 'Head', 'Horizontal Divider', 'Vertical Divider', 'Horizontal Meeting Rail',
                   'Vertical Meeting Rail', 'Common Frame', 'Spacer')
OPAQUE_SECTIONS = ('Sill Plate', 'Header', 'End Section', 'Middle Section', 'Thermal Bridge',
                   'Window Framing - Sill', 'Rough Opening - Header', 'Rough Opening - Jamb')
OTHER_SECTIONS = ('Common Thermal Bridge', 'General Cross Section')
PURPOSES = {'Window/Transparent Facade': ('WindowCrossSection', WINDOW_SECTIONS),
            'Opaque Facade': ('OpaqueCrossSection', OPAQUE_SECTIONS),
            'Other': ('OtherCrossSection', OTHER_SECTIONS)}
SECTION_ALIASES = {'Mullion': 'Vertical Divider', 'Transom': 'Horizontal Divider'}
GRAVITY = ('Down', 'Up', 'Left', 'Right', 'Into Screen', 'Out Of Screen')
MESHERS = {'simmetrix': 'Simmetrix Version 2022', 'quadtree': 'QuadTree Mesher'}
INTERNAL_BCS = ('Frame Cavity Surface', 'Glazing Cavity Surface')

# Model.xml preamble in the element order THERM 8.1.30 saves (fairyfly test_no_result.thmz,
# LBNL sample-sill.thmz). Values are THERM defaults; build fills in the marked fields.
PREAMBLE = """<ThermModel><Version>1</Version><CalculationReady>true</CalculationReady><Preferences><Settings>
<Origin><x>0</x><y>0</y></Origin><Scale>1</Scale><ConductivityUnits>Btu/hr-ft-F</ConductivityUnits>
<UValueBasis>Y-projected</UValueBasis><CondensationResistanceModel>false</CondensationResistanceModel></Settings>
<Underlay><Name></Name><IsUnderlayOn>false</IsUnderlayOn><UnitSystem>SI</UnitSystem><Origin><x>0</x><y>0</y></Origin>
<UnderlayType>None</UnderlayType><HorizontalScale>1</HorizontalScale><VerticalScale>1</VerticalScale></Underlay><Snap>
<SnapToGrid>false</SnapToGrid><SmartSnap>false</SmartSnap><SnapToUnderlay>false</SnapToUnderlay>
<SnapToVertical>true</SnapToVertical><SnapToHorizontal>true</SnapToHorizontal><SnapToAngle>false</SnapToAngle>
<VerticalAndHorizontalLock>false</VerticalAndHorizontalLock><DrawGrid>false</DrawGrid><SnapAngle>10</SnapAngle>
<GridWidth>5</GridWidth><GridHeight>5</GridHeight><Origin><x>0</x><y>0</y></Origin></Snap></Preferences><Properties>
<General><CalculationVersion></CalculationVersion><FileName></FileName><Directory></Directory><CreationDate></CreationDate>
<LastModified></LastModified><CreationVersion></CreationVersion><LastModifiedVersion></LastModifiedVersion><Title></Title>
<CreatedBy></CreatedBy><Company></Company><Client></Client><Notes></Notes></General><CalculationOptions>
<SimulationEngine>Conrad</SimulationEngine><CalculationMode>Steady-State</CalculationMode>
<SimulateMoisture>true</SimulateMoisture><SimulateThermal>true</SimulateThermal><ModelingOptions>
<ExcludeWaterLiquidTransportation>true</ExcludeWaterLiquidTransportation><ExcludeHeatOfEvaporation>true</ExcludeHeatOfEvaporation>
<ExcludeCapillaryConduction>true</ExcludeCapillaryConduction><ExcludeVaporDiffusionConduction>true</ExcludeVaporDiffusionConduction>
<ExcludeLatentHeatOfFusion>true</ExcludeLatentHeatOfFusion>
<ExcludeThermalConductivityMoistureDependent>true</ExcludeThermalConductivityMoistureDependent>
<ExcludeThermalConductivityTemperatureDependent>true</ExcludeThermalConductivityTemperatureDependent></ModelingOptions>
<TransientCalculationMethodology>Constant everywhere</TransientCalculationMethodology>
<SteadyStateCalculationMethodology>No time variable</SteadyStateCalculationMethodology>
<ConstantInitialConditionsTransient><Temperature>21</Temperature><Humidity>0.5</Humidity></ConstantInitialConditionsTransient>
<ConstantInitialConditionsSteadyState><Temperature>21</Temperature><Humidity>0.5</Humidity></ConstantInitialConditionsSteadyState>
<EngineParameters><ConvergenceTolerance>1e-05</ConvergenceTolerance><RelaxationParameter>1</RelaxationParameter>
<TimeStep>3600</TimeStep><NumberOfTimesteps>8670</NumberOfTimesteps></EngineParameters><MeshControl><MeshType></MeshType>
<MeshParameter></MeshParameter><RunErrorEstimator></RunErrorEstimator><ErrorEnergyNorm></ErrorEnergyNorm>
<MaximumIterations></MaximumIterations></MeshControl><HeatTransferModelingOptions>
<AutomaticallyAdjustRelaxationParameter>true</AutomaticallyAdjustRelaxationParameter><AdjustmentStep>-0.01</AdjustmentStep>
<MaximumIterations>25</MaximumIterations><ViewFactorSmoothing>true</ViewFactorSmoothing></HeatTransferModelingOptions>
<MiscellaneousProperties><RadianceMode>false</RadianceMode><UseCRForGlazingSystem>true</UseCRForGlazingSystem>
<CheckForCorrectBCOnGlazingSystem>false</CheckForCorrectBCOnGlazingSystem></MiscellaneousProperties></CalculationOptions>
<ModelExposure><ModelOrientation>0</ModelOrientation><GravityOrientation></GravityOrientation><Exposure></Exposure>
</ModelExposure><Miscellaneous><FloatTolerance>0.03</FloatTolerance><CheckingTolerance>0.1</CheckingTolerance>
</Miscellaneous></Properties><GlazingOrigin><x>0</x><y>0</y></GlazingOrigin></ThermModel>"""


# ================================================================ build

def is_thmx(path):
    return path.lower().endswith('.thmx')


def _pt(v, where):
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(c, (int, float)) for c in v)):
        raise InputError('%s: expected a point [x, y] in mm, got %r' % (where, v))
    return v


def _resolve(lib, kind, ref, where):
    """ref is a name, a {name, uuid} reference, or a full inline record."""
    if isinstance(ref, str):
        rec = lib.find(kind, name=ref)
        label = ref
    elif isinstance(ref, dict):
        inline = 'xml' in ref or (kind == 'material' and ('conductivity' in ref or ref.get('type') == 'cavity')) \
            or (kind == 'bc' and 'temperature' in ref)
        try:
            if inline:
                new = lib.record_from_json(kind, ref, where)
                lib.add(new, 'spec ' + where)
                rec = lib.find(kind, name=core.record_name(new), uid=core.record_uuid(new))
            else:
                rec = lib.find(kind, name=ref.get('name'), uid=ref.get('uuid'))
        except InputError as exc:
            raise InputError('%s: %s' % (where, exc))
        label = ref.get('name') or ref.get('uuid')
    else:
        raise InputError('%s: expected a name or a record object' % where)
    if rec is None:
        raise InputError('%s: %s "%s" not found in the spec or any --library file'
                         % (where, 'material' if kind == 'material' else 'boundary condition', label))
    return rec


def _geometry(spec, resolve, is_cav, name_of, seed, fixes):
    """Shared checks for both formats: polygons, boundary segments, overlaps, holes, cavities."""
    # ---- polygons: materials, closure, orientation, self-intersection
    snap = core.Snapper()
    polys = []
    for i, p in enumerate(spec['polygons']):
        where = 'polygons[%d]%s' % (i, " '%s'" % p['id'] if p.get('id') else '')
        mat = resolve('material', p.get('material'), where)
        raw = [snap(_pt(v, where)) for v in p.get('points', [])]
        pts = []
        for q in raw:
            if not pts or q != pts[-1]:
                pts.append(q)
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        if len(pts) != len(raw):
            fixes.append('%s: removed %d repeated/closing point(s)' % (where, len(raw) - len(pts)))
        if len(pts) < 3 or abs(core.signed_area(pts)) < 1e-6:
            raise InputError('%s: not a closed polygon with area (needs >= 3 distinct points)' % where)
        if core.signed_area(pts) < 0:
            pts.reverse()
            fixes.append('%s: points were clockwise, reversed to counter-clockwise' % where)
        n = len(pts)
        for a in range(n):
            for b in range(a + 2, n):
                if a == 0 and b == n - 1:
                    continue
                if core.segments_cross(pts[a], pts[(a + 1) % n], pts[b], pts[(b + 1) % n]):
                    raise InputError('%s: self-intersecting (edge %d crosses edge %d)' % (where, a, b))
        cav = is_cav(mat)
        polys.append({'where': where, 'label': p.get('id') or str(i + 1), 'mat': mat, 'pts': pts, 'cavity': cav,
                      'uuid': p.get('uuid') or str(uuid.uuid5(seed, 'polygon/%d' % i))})

    # ---- boundary segments
    segs = []
    for i, b in enumerate(spec.get('boundaries', [])):
        where = 'boundaries[%d]' % i
        line = b['points'] if 'points' in b else [b.get('start'), b.get('end')]
        line = [snap(_pt(v, where)) for v in line]
        bc = resolve('bc', b.get('bc'), where)
        for k in range(len(line) - 1):
            if line[k] == line[k + 1]:
                raise InputError('%s: zero-length segment' % where)
            segs.append({'where': where, 'a': line[k], 'b': line[k + 1], 'bc': bc, 'tag': b.get('tag') or '',
                         'edges': []})

    # ---- make neighbouring polygons share vertices (T-junctions), then check overlaps
    cands = list(snap.points)
    for p in polys:
        p['pts'], n = core.insert_vertices(p['pts'], cands)
        if n:
            fixes.append('%s: inserted %d vertex(es) where neighbours or boundary ends meet its edges'
                         % (p['where'], n))
    edges = {}
    for pi, p in enumerate(polys):
        n = len(p['pts'])
        for k in range(n):
            e = (p['pts'][k], p['pts'][(k + 1) % n])
            if e in edges:
                raise InputError('%s overlaps %s (shared edge %s-%s runs the same way)'
                                 % (p['where'], polys[edges[e]]['where'], list(e[0]), list(e[1])))
            edges[e] = pi
    for i, p in enumerate(polys):
        for j, q in enumerate(polys):
            if i >= j:
                continue
            pe = [(p['pts'][k], p['pts'][(k + 1) % len(p['pts'])]) for k in range(len(p['pts']))]
            qe = [(q['pts'][k], q['pts'][(k + 1) % len(q['pts'])]) for k in range(len(q['pts']))]
            bad = any(core.segments_cross(a, b, c, d) for a, b in pe for c, d in qe)
            bad = bad or any(core.point_in_polygon(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), q['pts']) == 1
                             for a, b in pe)
            bad = bad or any(core.point_in_polygon(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), p['pts']) == 1
                             for a, b in qe)
            if bad:
                raise InputError('%s overlaps %s' % (p['where'], q['where']))
    outer = [e for e in edges if (e[1], e[0]) not in edges]
    for loop in core.outer_loops(outer):
        if core.signed_area(loop) < 0:
            raise InputError('polygons leave an unfilled hole near %s (area %s mm2); fill it with a polygon'
                             % (list(loop[0]), fmt(-core.signed_area(loop))))
    if sum(1 for lp in core.outer_loops(outer) if core.signed_area(lp) > 0) > 1:
        raise InputError('polygons form more than one separate region; THERM needs one connected section')

    # ---- frame cavities must be enclosed by solids
    for e, pi in edges.items():
        if polys[pi]['cavity']:
            other = edges.get((e[1], e[0]))
            if other is None:
                raise InputError('%s: frame cavity edge %s-%s is on the outside; cavities must be enclosed by solids'
                                 % (polys[pi]['where'], list(e[0]), list(e[1])))
            if polys[other]['cavity']:
                raise InputError('%s touches cavity %s; merge touching cavities into one polygon'
                                 % (polys[pi]['where'], polys[other]['where']))

    # ---- assign outer edges to boundary segments; the rest is adiabatic
    adiabatic = []
    for e in outer:
        hit = [s for s in segs if core.on_segment(e[0], s['a'], s['b']) and core.on_segment(e[1], s['a'], s['b'])]
        if len(hit) > 1:
            raise InputError('%s and %s overlap on edge %s-%s' % (hit[0]['where'], hit[1]['where'],
                                                                  list(e[0]), list(e[1])))
        if hit:
            hit[0]['edges'].append(e)
        else:
            adiabatic.append(e)
    for s in segs:
        covered = sum(core.dist(*e) for e in s['edges'])
        if covered < core.dist(s['a'], s['b']) - core.TOL:
            raise InputError('%s: segment %s-%s lies on the outer edge of the model for only %s of %s mm'
                             % (s['where'], list(s['a']), list(s['b']), fmt(covered), fmt(core.dist(s['a'], s['b']))))
    if len({name_of(s['bc']) for s in segs if name_of(s['bc']) != 'Adiabatic'}) < 2:
        raise InputError('boundaries: THERM needs at least two different non-adiabatic boundary conditions')
    return {'polys': polys, 'segs': segs, 'edges': edges, 'outer': outer, 'adiabatic': adiabatic}


def build(spec, libraries, thmz_path):
    fixes, warnings = [], []
    if not isinstance(spec, dict) or not spec.get('polygons'):
        raise InputError('spec: needs a non-empty "polygons" list')
    if spec.get('units', 'mm') != 'mm':
        raise InputError('spec: units must be "mm"')
    seed = uuid.uuid5(uuid.NAMESPACE_URL, 'therm-model/' + json.dumps(spec, sort_keys=True))
    if is_thmx(thmz_path):  # THERM 7.8: records from "thmx" dicts, written inline
        recs, resolve = thmx.resolver(spec, libraries)
        g = _geometry(spec, resolve, thmx.is_cavity, lambda r: r['Name'], seed, fixes)
        warnings += recs.warnings
        res = thmx.build(spec, g, thmz_path, warnings)
        return dict({'skill': SKILL, 'version': VERSION, 'command': 'build'}, **res,
                    fixes=fixes, warnings=warnings)
    lib = core.Library()
    for key, kind in (('materials', 'material'), ('boundary_conditions', 'bc')):
        for i, d in enumerate(spec.get(key, [])):
            lib.add(lib.record_from_json(kind, d, '%s[%d]' % (key, i)), 'spec %s[%d]' % (key, i))
    for g in spec.get('gases', []):
        lib.add(lib.record_from_json('gas', g, 'gases'), 'spec gases')
    for path in libraries:
        lib.load(path)
    builtin = core.builtin_library()
    for kind in builtin.by_name:
        for rec, _ in builtin.by_name[kind].values():
            lib.add(rec, 'built-in')

    g = _geometry(spec, lambda kind, ref, where: _resolve(lib, kind, ref, where),
                  lambda mat: core.is_cavity(mat) and mat.findtext('Cavity/CavityStandard') != 'CEN',
                  core.record_name, seed, fixes)
    polys, segs, edges, outer, adiabatic = g['polys'], g['segs'], g['edges'], g['outer'], g['adiabatic']

    root, entries = _model_xml(spec, polys, segs, adiabatic, edges, seed, thmz_path)
    used_mats, used_bcs = [], [lib.find('bc', name='Adiabatic')]
    for p in polys:
        if p['mat'] not in used_mats:
            used_mats.append(p['mat'])
    for s in segs:
        if s['bc'] not in used_bcs:
            used_bcs.append(s['bc'])
    for kind, recs in (('material', used_mats), ('boundary condition', used_bcs)):
        names = [core.record_name(r) for r in recs]
        dup = next((n for n in names if names.count(n) > 1), None)
        if dup:
            raise InputError('%s "%s" refers to two different records (UUIDs %s); THERM matches by name'
                             % (kind, dup, ', '.join(core.record_uuid(r) for r in recs if core.record_name(r) == dup)))
    pure, mix = [], []
    for m in used_mats:
        gname = m.findtext('Cavity/Gas')
        if gname is None:
            continue
        g = lib.find('gas', name=gname)
        if g is None:
            raise InputError('material "%s": gas "%s" not found in the spec or any --library file'
                             % (core.record_name(m), gname))
        if g not in mix:
            mix.append(g)
        for comp in g.iter('PureGas'):
            pg = lib.find('puregas', name=(comp.text or '').strip())
            if pg is None:
                raise InputError('gas "%s": pure gas "%s" not found' % (gname, comp.text))
            if pg not in pure:
                pure.append(pg)
    files = [('Model.xml', root)]
    for tag, recs, name in (('Materials', used_mats, 'Materials.xml'), ('Gases', pure + mix, 'Gases.xml'),
                            ('BoundaryConditions', used_bcs, 'SteadyStateBC.xml')):
        r = ET.Element(tag)
        sub(r, 'Version', '1')
        for rec in recs:
            r.append(copy.deepcopy(rec))
        files.append((name, r))
    core.write_thmz(thmz_path, [(n, core.to_bytes(r)) for n, r in files])

    tags = {}
    for s in segs:
        if s['tag']:
            t = tags.setdefault(s['tag'], {'segments': 0, 'length_mm': 0.0})
            t['segments'] += len(s['edges'])
            t['length_mm'] = round(t['length_mm'] + sum(core.dist(*e) for e in s['edges']), 6)
    return {
        'skill': SKILL, 'version': VERSION, 'command': 'build', 'thmz': os.path.abspath(thmz_path),
        'entries': [n for n, _ in files], 'polygons': {p['label']: i + 1 for i, p in enumerate(polys)},
        'counts': {'polygons': len(polys), 'boundaries': entries, 'frame_cavities': sum(p['cavity'] for p in polys)},
        'materials': [core.record_name(m) for m in used_mats], 'boundary_conditions': [core.record_name(b) for b in used_bcs],
        'gases': [{'kind': g.tag, 'name': core.record_name(g)} for g in pure + mix], 'tags': tags,
        'adiabatic_edges': [{'polygon': polys[edges[e]]['label'], 'start': list(e[0]), 'end': list(e[1])}
                            for e in adiabatic],
        'fixes': fixes, 'warnings': warnings,
    }


def _model_xml(spec, polys, segs, adiabatic, edges, seed, thmz_path):
    root = core._clean(PREAMBLE)
    go = spec.get('glazing_origin', [0, 0])
    _pt(go, 'glazing_origin')
    for path in ('Preferences/Settings/Origin', 'Preferences/Underlay/Origin', 'GlazingOrigin'):
        root.find(path + '/x').text, root.find(path + '/y').text = fmt(go[0]), fmt(go[1])
    allpts = [q for p in polys for q in p['pts']]
    span = max(max(q[0] for q in allpts) - min(q[0] for q in allpts), max(q[1] for q in allpts) - min(q[1] for q in allpts))
    root.find('Preferences/Settings/Scale').text = fmt(1.0 if span < 100 else 100.0 / span)
    root.find('Preferences/Settings/UValueBasis').text = spec.get('u_value_basis', 'Y-projected')
    now = spec.get('created') or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    gen = root.find('Properties/General')
    for tag, val in (('CalculationVersion', THERM_VERSION), ('CreationVersion', THERM_VERSION),
                     ('LastModifiedVersion', THERM_VERSION), ('CreationDate', now), ('LastModified', now),
                     ('FileName', os.path.splitext(os.path.basename(thmz_path))[0]),
                     ('Directory', os.path.dirname(os.path.abspath(thmz_path))),
                     ('Title', spec.get('title', '')), ('Notes', spec.get('notes', 'Built by therm-model v1'))):
        gen.find(tag).text = val
    mesh = dict({'type': 'simmetrix', 'parameter': 6, 'error_estimator': True, 'error_norm': 10,
                 'max_iterations': 5}, **spec.get('mesh', {}))
    mtype = MESHERS.get(str(mesh['type']).lower().split()[0], mesh['type'])
    if mtype not in MESHERS.values():
        raise InputError('mesh.type must be one of %s' % ', '.join(MESHERS))
    if not (isinstance(mesh['parameter'], int) and 1 <= mesh['parameter'] <= 20):
        raise InputError('mesh.parameter must be an integer 1..20 (THERM default 6)')
    if not (isinstance(mesh['max_iterations'], int) and mesh['max_iterations'] >= 1):
        raise InputError('mesh.max_iterations must be an integer >= 1')
    if not 0 < float(mesh['error_norm']) <= 100:
        raise InputError('mesh.error_norm must be a percentage in (0, 100]')
    mc = root.find('Properties/CalculationOptions/MeshControl')
    for tag, val in (('MeshType', mtype), ('MeshParameter', str(mesh['parameter'])),
                     ('RunErrorEstimator', 'true' if mesh['error_estimator'] else 'false'),
                     ('ErrorEnergyNorm', fmt(mesh['error_norm'])), ('MaximumIterations', str(mesh['max_iterations']))):
        mc.find(tag).text = val
    purpose = spec.get('model_purpose', 'Window/Transparent Facade')
    if purpose not in PURPOSES:
        raise InputError('model_purpose must be one of: %s' % ', '.join(PURPOSES))
    tag, allowed = PURPOSES[purpose]
    section = SECTION_ALIASES.get(spec.get('cross_section', allowed[0]), spec.get('cross_section', allowed[0]))
    if section not in allowed:
        raise InputError('cross_section "%s" is not valid for %s; use one of: %s (or Mullion, Transom)'
                         % (section, purpose, ', '.join(allowed)))
    gravity = spec.get('gravity', 'Down')
    if gravity not in GRAVITY:
        raise InputError('gravity must be one of: %s' % ', '.join(GRAVITY))
    exp = root.find('Properties/ModelExposure')
    exp.find('GravityOrientation').text = gravity
    sub(exp.find('Exposure'), 'ModelPurpose', purpose)
    sub(exp.find('Exposure'), tag, section)
    if purpose == 'Opaque Facade':
        sub(exp.find('Exposure'), 'AssemblyType', spec.get('assembly_type', 'Wall'))

    cav_uuid = {}
    cavities = [p for p in polys if p['cavity']]
    if cavities:
        cs = sub(root, 'Cavities')
        for n, p in enumerate(cavities):
            cav_uuid[id(p)] = str(uuid.uuid5(seed, 'cavity/%d' % n))
            c = sub(cs, 'Cavity')
            xs, ys = [q[0] for q in p['pts']], [q[1] for q in p['pts']]
            for t, v in (('UUID', cav_uuid[id(p)]), ('HeatFlowDirection', 'Unknown'),
                         ('Emissivity1', fmt(p['mat'].findtext('Cavity/EmissivitySide1') or 0.9)),
                         ('Emissivity2', fmt(p['mat'].findtext('Cavity/EmissivitySide2') or 0.9)),
                         ('Temperature1', '7'), ('Temperature2', '-4'), ('MaxXDimension', fmt(max(xs) - min(xs))),
                         ('MaxYDimension', fmt(max(ys) - min(ys))), ('ActualHeight', '1000'),
                         ('Area', fmt(core.signed_area(p['pts']) * 1e-6)), ('LocalEmissivities', 'true'),
                         ('Pressure', '1.013e+05')):
                sub(c, t, v)
            xy(c, 'WarmLocator', (0, 0))
            xy(c, 'ColdLocator', (0, 0))
    pe = sub(root, 'Polygons')
    for n, p in enumerate(polys):
        e = sub(pe, 'Polygon')
        sub(e, 'UUID', p['uuid'])
        sub(e, 'ID', str(n + 1))
        sub(e, 'MaterialUUID', core.record_uuid(p['mat']))
        sub(e, 'MaterialName', core.record_name(p['mat']))
        xy(e, 'Origin', (0, 0))
        pts = sub(e, 'Points')
        for q in p['pts']:
            xy(pts, 'Point', q)
        if id(p) in cav_uuid:
            sub(e, 'CavityUUID', cav_uuid[id(p)])
        sub(e, 'Type', 'Material')

    be = sub(root, 'Boundaries')
    count = [len(polys)]

    def boundary(edge, name, tag, color, poly, poly2=None, enclosure=None):
        count[0] += 1
        b = sub(be, 'Boundary')
        sub(b, 'ID', str(count[0]))
        sub(b, 'UUID', str(uuid.uuid5(seed, 'boundary/%d' % count[0])))
        sub(b, 'Name', name)
        sub(b, 'FluxTag', tag)
        sub(b, 'IsBlocking', 'true')
        sub(b, 'NeighborPolygonUUID', poly['uuid'])
        if poly2 is not None:
            sub(b, 'NeighborPolygonUUID2', poly2['uuid'])
        xy(b, 'Origin', (0, 0))
        xy(b, 'StartPoint', edge[0])
        xy(b, 'EndPoint', edge[1])
        sub(b, 'Side', '0')
        t = sub(b, 'ThermalEmissionProperties')
        sub(t, 'Emissivity', fmt(core.material_emissivity((poly2 or poly)['mat'])))
        sub(t, 'Temperature', '0')
        sub(t, 'UseGlobalEmissivity', 'true')
        sub(b, 'IsIlluminated', 'false')
        sub(b, 'EdgeID', '0')
        if enclosure is not None:
            sub(b, 'EnclosureNumber', str(enclosure))
        sub(b, 'Type', 'Frame Cavity' if enclosure is not None else 'Boundary Condition')
        sub(b, 'Color', color)
        sub(b, 'Status', '64' if enclosure is not None else '0')

    for s in segs:
        for e in sorted(s['edges'], key=lambda e: core.seg_param(e[0], s['a'], s['b'])[0]):
            boundary(e, core.record_name(s['bc']), s['tag'], s['bc'].findtext('Color') or '0x000000',
                     polys[edges[e]])
    for e in adiabatic:
        boundary(e, 'Adiabatic', '', '0x000000', polys[edges[e]])
    for n, p in enumerate(cavities):
        k = len(p['pts'])
        for j in range(k):
            a, b = p['pts'][j], p['pts'][(j + 1) % k]
            solid = polys[edges[(b, a)]]
            boundary((b, a), 'Frame Cavity Surface', '', '0xFF0000', p, solid, enclosure=n + 1)
    return root, count[0] - len(polys)


# ================================================================ inspect

def inspect(path, detail=False):
    if is_thmx(path):
        return dict({'skill': SKILL, 'version': VERSION, 'command': 'inspect'}, **thmx.inspect(path, detail))
    infos, entries = core.read_thmz(path)
    model_name, model = core.get_entry(entries, 'Model')
    if model is None:
        raise InputError('%s: no Model.xml entry; not a THERM 8 .thmz' % path)
    _, mats = core.get_entry(entries, 'Materials')
    _, gases = core.get_entry(entries, 'Gases')
    bc_root = None
    for base in ('SteadyStateBC', 'BoundaryConditions'):
        _, bc_root = core.get_entry(entries, base)
        if bc_root is not None:
            break
    issues = []
    mat_recs = list(mats) if mats is not None else []
    bc_recs = list(bc_root) if bc_root is not None else []
    by_uuid = {core.record_uuid(m): m for m in mat_recs if m.tag == 'Material'}
    by_name = {core.record_name(m): m for m in mat_recs if m.tag == 'Material'}
    gs_names = {g.findtext('Name') for g in model.iter('GlazingSystem') if g.find('Name') is not None}
    polys, used = [], {}
    for p in model.iter('Polygon'):
        pts = [(float(q.findtext('x')), float(q.findtext('y'))) for q in p.iter('Point')]
        area = core.signed_area(pts) if len(pts) >= 3 else 0.0
        mname, muid = p.findtext('MaterialName') or '', p.findtext('MaterialUUID') or ''
        rec = next((r for r in (by_uuid.get(muid), by_name.get(mname), by_name.get(core.strip_project(mname)))
                    if r is not None), None)
        if rec is None and mname not in gs_names and p.findtext('Type') == 'Material':
            issues.append('polygon %s: material "%s" (%s) not in Materials' % (p.findtext('ID'), mname, muid))
        elif rec is not None and core.strip_project(mname) != core.record_name(rec):
            issues.append('polygon %s: MaterialName "%s" does not match record "%s"'
                          % (p.findtext('ID'), mname, core.record_name(rec)))
        if area < 0:
            issues.append('polygon %s is clockwise' % p.findtext('ID'))
        if rec is not None:
            used[core.record_name(rec)] = used.get(core.record_name(rec), 0) + 1
        d = {'id': p.findtext('ID'), 'uuid': p.findtext('UUID'), 'type': p.findtext('Type'), 'material': mname,
             'points': len(pts), 'area_mm2': round(abs(area), 3)}
        for opt, key in (('CavityUUID', 'cavity_uuid'), ('GlazingSystem/ID', 'glazing_system')):
            if p.findtext(opt):
                d[key] = p.findtext(opt)
        if detail:
            d['coords'] = [list(q) for q in pts]
        polys.append(d)
    bc_names = {core.record_name(b) for b in bc_recs}
    groups, tags, bounds, bc_used = {}, {}, [], {}
    for b in model.iter('Boundary'):
        s = (float(b.findtext('StartPoint/x')), float(b.findtext('StartPoint/y')))
        e = (float(b.findtext('EndPoint/x')), float(b.findtext('EndPoint/y')))
        L = core.dist(s, e)
        name, tag, typ = b.findtext('Name') or '', b.findtext('FluxTag') or '', b.findtext('Type') or ''
        if name not in INTERNAL_BCS and name not in bc_names and core.strip_project(name) not in bc_names:
            issues.append('boundary %s: BC "%s" not in the BC entry' % (b.findtext('ID'), name))
        bc_used[core.strip_project(name)] = bc_used.get(core.strip_project(name), 0) + 1
        g = groups.setdefault((typ, name, tag), {'type': typ, 'bc': name, 'tag': tag, 'segments': 0, 'length_mm': 0})
        g['segments'] += 1
        g['length_mm'] = round(g['length_mm'] + L, 3)
        if tag:
            t = tags.setdefault(tag, {'segments': 0, 'length_mm': 0, 'bcs': []})
            t['segments'] += 1
            t['length_mm'] = round(t['length_mm'] + L, 3)
            if name not in t['bcs']:
                t['bcs'].append(name)
        if detail:
            bounds.append({'id': b.findtext('ID'), 'bc': name, 'tag': tag, 'type': typ, 'start': list(s),
                           'end': list(e)})
    materials = []
    for m in mat_recs:
        if m.tag != 'Material':
            continue
        d = {'name': core.record_name(m), 'uuid': core.record_uuid(m), 'used_by': used.get(core.record_name(m), 0)}
        if core.is_cavity(m):
            d.update(kind='cavity', cavity_standard=m.findtext('Cavity/CavityStandard'), gas=m.findtext('Cavity/Gas'))
        else:
            k = m.findtext('Solid/HygroThermal/ThermalConductivityDry')
            d.update(kind='solid', conductivity=float(k) if k else None,
                     emissivity=core.material_emissivity(m) if m.find('Solid') is not None else None)
        materials.append(d)
    bcs = []
    for b in bc_recs:
        if b.tag != 'BoundaryCondition':
            continue
        model_el = next((c for c in b if c.tag in ('Simplified', 'Comprehensive', 'RadiationSurface', 'NoExchange')),
                        None)
        d = {'name': core.record_name(b), 'uuid': core.record_uuid(b), 'used_by': bc_used.get(core.record_name(b), 0),
             'model': model_el.tag if model_el is not None else None}
        t = b.findtext('Simplified/Temperature') or b.findtext('Comprehensive/Convection/Temperature')
        h = b.findtext('Simplified/FilmCoefficient') or b.findtext('Comprehensive/Convection/FilmCoefficient')
        if t:
            d['temperature'] = float(t)
        if h:
            d['film_coefficient'] = float(h)
        bcs.append(d)
    exposure = model.find('Properties/ModelExposure/Exposure')
    section = None
    if exposure is not None:
        section = next((c.text for c in exposure if c.tag.endswith('CrossSection')), None)
    mc = model.find('Properties/CalculationOptions/MeshControl')
    names = [i.filename for i in infos]
    return {
        'skill': SKILL, 'version': VERSION, 'command': 'inspect', 'input': os.path.abspath(path), 'entries': names,
        'format': 'json' if model_name.endswith('.json') else 'xml', 'model_version': model.findtext('Version'),
        'solved': any(n.startswith('SteadyStateResults') for n in names),
        'has_mesh': any(n.startswith('Mesh.') for n in names),
        'transient': any(n.startswith('transient results/') for n in names),
        'title': model.findtext('Properties/General/Title'),
        'therm_version': model.findtext('Properties/General/CalculationVersion'),
        'model_purpose': model.findtext('Properties/ModelExposure/Exposure/ModelPurpose'),
        'cross_section': section, 'gravity': model.findtext('Properties/ModelExposure/GravityOrientation'),
        'u_value_basis': model.findtext('Preferences/Settings/UValueBasis'),
        'mesh': {c.tag: c.text for c in mc} if mc is not None else None,
        'glazing_origin': [float(model.findtext('GlazingOrigin/x') or 0), float(model.findtext('GlazingOrigin/y') or 0)],
        'counts': {'polygons': len(polys), 'boundaries': sum(g['segments'] for g in groups.values()),
                   'frame_cavities': len(model.findall('Cavities/Cavity')) +
                   sum(1 for p in polys if p['type'] == 'Frame Cavity')},
        'polygons': polys, 'materials': materials,
        'gases': [{'kind': g.tag, 'name': core.record_name(g)} for g in (gases if gases is not None else [])
                  if g.tag in ('PureGas', 'Gas')],
        'boundary_conditions': bcs, 'boundary_groups': list(groups.values()), 'tags': tags,
        'glazing_systems': [{'id': g.findtext('ID'), 'name': g.findtext('Name'), 'layers': len(g.findall('Layers/Layer')),
                             'gaps': len(g.findall('Gases/Gas')), 'source': g.findtext('Source')}
                            for g in model.findall('GlazingSystems/GlazingSystem')],
        **({'boundaries': bounds} if detail else {}),
        'issues': issues,
    }


# ================================================================ patch

def _select(model, selector):
    """Boundaries (not cavity surfaces) matching 'id:N', 'tag:T' or a BC name."""
    out = []
    for b in model.iter('Boundary'):
        if b.findtext('Type') in ('Frame Cavity', 'Glazing Cavity'):
            continue
        if selector.startswith('id:'):
            ok = b.findtext('ID') == selector[3:]
        elif selector.startswith('tag:'):
            ok = (b.findtext('FluxTag') or '') == selector[4:]
        else:
            name = selector[5:] if selector.startswith('name:') else selector
            ok = b.findtext('Name') in (name,) or core.strip_project(b.findtext('Name') or '') == name
        if ok:
            out.append(b)
    if not out:
        raise InputError('no boundary matches selector "%s" (use a BC name, tag:<tag> or id:<boundary ID>)'
                         % selector)
    return out


def _transform(model, f, what):
    """Apply f to every geometric (x, y) pair: points, boundary ends, origins, glazing origin."""
    pairs = list(model.iter('Point')) + list(model.iter('StartPoint')) + list(model.iter('EndPoint'))
    pairs += [p.find('Origin') for p in list(model.iter('Polygon')) + list(model.iter('Boundary'))
              if p.find('Origin') is not None]
    pairs += [model.find('GlazingOrigin')]
    for c in model.iter('Cavity'):
        pairs += [c.find(t) for t in ('WarmLocator', 'ColdLocator') if c.find(t) is not None]
    n = 0
    for e in pairs:
        if e is None or e.find('x') is None:
            continue
        x, y = float(e.findtext('x')), float(e.findtext('y'))
        if e.tag in ('Origin', 'WarmLocator', 'ColdLocator') and x == 0 and y == 0:
            continue
        nx, ny = f(x, y)
        if (nx, ny) != (x, y):  # leave unchanged values exactly as written
            e.find('x').text, e.find('y').text = fmt(nx), fmt(ny)
            n += 1
    return '%s: moved %d coordinate pairs' % (what, n)


def patch(path, out_path, args, libraries):
    if is_thmx(path) != is_thmx(out_path):
        raise InputError('patch keeps the format: %s and %s must both be .thmx (7.8) or both .thmz (8.1)'
                         % (os.path.basename(path), os.path.basename(out_path)))
    if is_thmx(path):
        return dict({'skill': SKILL, 'version': VERSION, 'command': 'patch'}, **thmx.patch(path, out_path, args, libraries))
    if getattr(args, 'set_record', None):
        raise InputError('--set-record is for THERM 7.8 .thmx files; on a .thmz use --swap-material or --set-bc')
    infos, entries = core.read_thmz(path)
    if 'Model.xml' not in entries:
        raise InputError('%s: patch needs an XML Model.xml entry (JSON-variant .thmz files are not patched)' % path)
    trees = {'Model.xml': core.parse_entry(entries['Model.xml'], 'Model.xml')}
    bc_entry = next((n for n in core.BC_ENTRIES if n in entries), None)
    for n in ('Materials.xml', bc_entry, 'Gases.xml'):
        if n and n in entries:
            trees[n] = core.parse_entry(entries[n], n)
    model, changed, notes = trees['Model.xml'], set(), []
    lib = core.Library()
    for n in ('Materials.xml', bc_entry):
        if n in trees:
            lib.add_root(trees[n], 'file')
    for p in libraries:
        lib.load(p)

    def swap_record(entry, old_names, rec, still_used):
        """Put rec into entry; an old record no longer used is replaced in place (else removed)."""
        root = trees[entry]
        present = any(core.record_uuid(r) == core.record_uuid(rec) for r in root)
        for r in list(root):
            name = core.record_name(r)
            if name not in old_names or still_used(name) or name == 'Adiabatic' or r is rec:
                continue
            if not present:
                new = copy.deepcopy(rec)
                ET.indent(new, '\t', level=1)
                new.tail = r.tail
                root.insert(list(root).index(r), new)
                present = True
                notes.append('%s: replaced unused record "%s" with "%s"' % (entry, name, core.record_name(rec)))
            else:
                notes.append('%s: removed now-unused record "%s"' % (entry, name))
            root.remove(r)
            changed.add(entry)
        if not present:
            new = copy.deepcopy(rec)
            ET.indent(new, '\t', level=1)
            if len(root):
                new.tail, root[-1].tail = root[-1].tail, '\n\t'
            root.append(new)
            changed.add(entry)
            notes.append('%s: added record "%s"' % (entry, core.record_name(rec)))

    for old, new in args.swap_material or []:
        if 'Materials.xml' not in trees:
            raise InputError('--swap-material: the file has no Materials.xml entry')
        hits = [p for p in model.iter('Polygon') if core.strip_project(p.findtext('MaterialName') or '') == old]
        if not hits:
            raise InputError('--swap-material: no polygon uses material "%s"' % old)
        rec = lib.find('material', name=new)
        if rec is None:
            raise InputError('--swap-material: material "%s" not in the file or any --library file' % new)
        oldrec = lib.find('material', uid=hits[0].findtext('MaterialUUID') or None)
        if oldrec is None:
            oldrec = lib.find('material', name=old)
        if core.is_cavity(rec) or core.is_cavity(oldrec) or any(p.findtext('CavityUUID') for p in hits):
            raise InputError('--swap-material: frame-cavity materials cannot be swapped by patch; rebuild instead')
        ids = {p.findtext('UUID') for p in hits}
        for p in hits:
            p.find('MaterialUUID').text, p.find('MaterialName').text = core.record_uuid(rec), core.record_name(rec)
        emis, n_b = fmt(core.material_emissivity(rec)), 0
        for b in model.iter('Boundary'):
            key = 'NeighborPolygonUUID2' if b.findtext('Type') == 'Frame Cavity' else 'NeighborPolygonUUID'
            if b.findtext(key) in ids and b.findtext('ThermalEmissionProperties/UseGlobalEmissivity') == 'true':
                b.find('ThermalEmissionProperties/Emissivity').text = emis
                n_b += 1
        swap_record('Materials.xml', {old}, rec, lambda n: any(
            core.strip_project(p.findtext('MaterialName') or '') == n for p in model.iter('Polygon')))
        changed.add('Model.xml')
        notes.append('swap-material: %d polygon(s) %s -> %s; emissivity updated on %d boundary(ies)'
                     % (len(hits), old, new, n_b))
    for sel, new in args.set_bc or []:
        if bc_entry is None:
            raise InputError('--set-bc: the file has no boundary-condition entry')
        rec = lib.find('bc', name=new)
        if rec is None and new == 'Adiabatic':
            rec = core.builtin_library().find('bc', name=new)
        if rec is None:
            raise InputError('--set-bc: BC "%s" not in the file or any --library file' % new)
        hits = _select(model, sel)
        olds = {core.strip_project(b.findtext('Name') or '') for b in hits}
        for b in hits:
            b.find('Name').text = core.record_name(rec)
            if b.find('Color') is not None and rec.findtext('Color'):
                b.find('Color').text = rec.findtext('Color')
        swap_record(bc_entry, olds, rec, lambda n: any(
            core.strip_project(b.findtext('Name') or '') == n for b in model.iter('Boundary')))
        changed.add('Model.xml')
        notes.append('set-bc: %d boundary(ies) %s -> %s' % (len(hits), sorted(olds), new))
    for sel, tag in args.retag or []:
        hits = _select(model, sel)
        for b in hits:
            b.find('FluxTag').text = tag
        changed.add('Model.xml')
        notes.append('retag: %d boundary(ies) -> "%s"' % (len(hits), tag))
    if args.scale is not None:
        s = args.scale
        if s <= 0:
            raise InputError('--scale must be > 0')
        if model.find('GlazingSystems/GlazingSystem') is not None:
            raise InputError('--scale: model has a glazing system whose layer thicknesses would not scale')
        notes.append(_transform(model, lambda x, y: (x * s, y * s), 'scale %s' % fmt(s)))
        for c in model.iter('Cavity'):
            for t, power in (('Area', 2), ('MaxXDimension', 1), ('MaxYDimension', 1)):
                e = c.find(t)
                if e is not None and e.text and float(e.text) > 0:
                    e.text = fmt(float(e.text) * s ** power)
        changed.add('Model.xml')
    if args.offset is not None:
        dx, dy = args.offset
        notes.append(_transform(model, lambda x, y: (x + dx, y + dy), 'offset %s,%s' % (fmt(dx), fmt(dy))))
        changed.add('Model.xml')
    if not changed:
        raise InputError('patch: no edit given (use --swap-material, --set-bc, --retag, --scale or --offset)')
    removed = [n for n in core.RESULT_ENTRIES if n in entries and not args.keep_results]
    def raw(name):
        if name not in changed:
            return entries[name]
        data = core.to_bytes_keep_layout(trees[name])
        return data + b'\n' if entries[name].endswith(b'\n') and not data.endswith(b'\n') else data

    items = [(i.filename, raw(i.filename)) for i in infos if i.filename not in removed]
    core.write_thmz(out_path, items, {i.filename: i for i in infos})
    return {'skill': SKILL, 'version': VERSION, 'command': 'patch', 'input': os.path.abspath(path),
            'thmz': os.path.abspath(out_path), 'changed_entries': sorted(changed), 'removed_entries': removed,
            'changes': notes}


# ================================================================ CLI

def _pair(s):
    try:
        x, y = (float(v) for v in s.split(','))
        return x, y
    except ValueError:
        raise argparse.ArgumentTypeError('expected DX,DY in mm, e.g. 10,-5')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='therm-model', description=__doc__.split('\n\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    b = sp.add_parser('build', help='write a .thmx (7.8) or .thmz (8.1) from a section spec JSON')
    b.add_argument('spec', help='section spec JSON (see SKILL.md)')
    i = sp.add_parser('inspect', help='summarise a .thmx or .thmz as JSON')
    i.add_argument('input', help='.thmx or .thmz file')
    i.add_argument('--detail', action='store_true', help='also list polygon coordinates and every boundary')
    p = sp.add_parser('patch', help='apply small edits to a .thmx or .thmz')
    p.add_argument('input', help='.thmx or .thmz file to patch')
    p.add_argument('--set-record', nargs=3, action='append', metavar=('NAME', 'ATTR', 'VALUE'),
                   help='7.8 only: set one attribute of an inline material or BC record, e.g. '
                        '"Wood (Pine, Spruce, Fir)" Conductivity 0.28')
    p.add_argument('--swap-material', nargs=2, action='append', metavar=('OLD', 'NEW'),
                   help='replace material OLD with NEW on every polygon (solid materials only)')
    p.add_argument('--set-bc', nargs=2, action='append', metavar=('SELECTOR', 'BC'),
                   help='set BC on boundaries chosen by a BC name, tag:<tag> or id:<boundary ID>')
    p.add_argument('--retag', nargs=2, action='append', metavar=('SELECTOR', 'TAG'),
                   help='set the U-factor tag ("" clears it) on selected boundaries')
    p.add_argument('--scale', type=float, help='multiply all geometry by this factor (about 0,0)')
    p.add_argument('--offset', type=_pair, metavar='DX,DY',
                   help='move all geometry by DX,DY mm (write --offset=-10,5 when DX is negative)')
    p.add_argument('--keep-results', action='store_true', help='keep Mesh/Results entries (default: drop them)')
    for s in (b, p):
        s.add_argument('-o', '--output', '--thmx', '--thmz', dest='output',
                       help='output file; .thmx = THERM 7.8, .thmz = THERM 8.1 (default: a .thmx beside the input)')
        s.add_argument('--library', action='append', default=[],
                       help='project JSON (records with "xml" for 8.1, "thmx" for 7.8), THERM lib XML, .thmz or .thmx (repeatable)')
    for s in (b, i, p):
        s.add_argument('--out', help='write the JSON result here instead of stdout')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'build':
            try:
                with open(a.spec, encoding='utf-8') as f:
                    spec = json.load(f)
            except OSError as exc:
                raise InputError('cannot read spec: %s' % exc)
            except ValueError as exc:
                raise InputError('spec %s is not valid JSON: %s' % (a.spec, exc))
            res = build(spec, a.library, a.output or thmx.default_out(a.spec))
        elif a.cmd == 'inspect':
            res = inspect(a.input, a.detail)
        else:
            if not a.output and not is_thmx(a.input):
                raise InputError('patch: give --thmz OUT.thmz for a .thmz input')
            res = patch(a.input, a.output or thmx.default_out(a.input, '_patched'), a, a.library)
    except InputError as exc:
        print('therm-model: error: %s' % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # unexpected: report, do not hide
        print('therm-model: internal error: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    text = json.dumps(res, indent=2)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
    else:
        print(text)
    for f in res.get('fixes', []):
        print('therm-model: fixed: %s' % f, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
