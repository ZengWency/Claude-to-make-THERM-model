"""THERM 7.8 .thmx helpers for therm-model: records, writer, inspect and patch.

A .thmx is one XML file in namespace http://windows.lbl.gov, root THERM-XML, geometry in mm.
Materials and boundary conditions are written inline from records' "thmx" dicts (the frozen
project-JSON contract), so the binary .lib files are never needed. Polygon and BCPolygon IDs
share one namespace. Standard library only.
"""
import datetime
import json
import os
import re
import xml.etree.ElementTree as ET

import thmz_core as geo  # same skill: geometry helpers only (seg_param, dist)

NS = 'http://windows.lbl.gov'
Q = '{%s}' % NS
ET.register_namespace('', NS)
THERM_VERSION = 'Version 7.8.80.0'

MATERIAL_ATTRS = ('Name', 'Index', 'Type', 'Conductivity', 'Tir', 'Absorptivity', 'EmissivityFront',
                  'EmissivityBack', 'RGBColor', 'CavityModel')
BC_ATTRS = ('Name', 'Type', 'H', 'HeatFLux', 'Temperature', 'Tr', 'Hr', 'Ei', 'Viewfactor', 'RadiationModel',
            'ConvectionFlag', 'FluxFlag', 'RadiationFlag', 'ConstantTemperatureFlag', 'EmisModifier', 'RGBColor')
TEXT_ATTRS = ('Name', 'RGBColor')
INT_ATTRS = ('Index', 'Type', 'CavityModel', 'RadiationModel', 'ConvectionFlag', 'FluxFlag', 'RadiationFlag',
             'ConstantTemperatureFlag')
SECTIONS = ('Sill', 'Jamb', 'Head', 'Horizontal Divider', 'Vertical Divider', 'Horizontal Meeting Rail',
            'Vertical Meeting Rail', 'Common Frame', 'Spacer')
INTERNAL_BCS = ('Frame Cavity Surface', 'Glazing Cavity Surface')


from thmz_core import InputError  # noqa: E402  same skill: one error type for the CLI


def normalise_name(name):
    """Copied from research/library-record-78.md section 3 (do not change here alone)."""
    name = re.sub(r"^(?:[^\s:]+:)+", "", name.strip())
    return re.sub(r"(?:_Cavity_\d+)+$", "", name).strip()


def f6(v):
    return '%.6f' % float(v)


def _bc(name, typ, rgb, flux_flag='0'):
    return {'Name': name, 'Type': typ, 'H': f6(0), 'HeatFLux': f6(0), 'Temperature': f6(0), 'Tr': f6(0),
            'Hr': f6(0), 'Ei': f6(1), 'Viewfactor': f6(1), 'RadiationModel': '0', 'ConvectionFlag': '0',
            'FluxFlag': flux_flag, 'RadiationFlag': '0', 'ConstantTemperatureFlag': '0', 'EmisModifier': f6(1),
            'RGBColor': rgb}


# Copied from THERM-written files (research/samples/therm78/shipped/sample-sill_7.8.55.thmx).
BUILTIN_BCS = {'Adiabatic': _bc('Adiabatic', '0', '0x000000', flux_flag='1'),
               'Frame Cavity Surface': _bc('Frame Cavity Surface', '7', '0x0000FF')}


def check_out_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    if '.' in stem:
        raise InputError('output name "%s" has a period in the file stem; THERM 7.8 hangs on such names '
                         '(use "_" instead)' % os.path.basename(path))


def default_out(src, suffix=''):
    stem = os.path.splitext(os.path.basename(src))[0].replace('.', '_') + suffix
    return os.path.join(os.path.dirname(os.path.abspath(src)), stem + '.thmx')


def _ordered(rec, attrs):
    out = {k: rec[k] for k in attrs if k in rec}
    out.update({k: v for k, v in rec.items() if k not in out})
    return out


def _check_cavity(rec, where):
    if rec.get('Type') == '1' and rec.get('CavityModel', '4') != '4':
        raise InputError('%s: frame cavity "%s" has CavityModel %s; only 4 (ISO 15099) is tested on THERM 7.8'
                         % (where, rec.get('Name'), rec.get('CavityModel')))


def record_from_json(kind, d, where):
    """A thmx dict from a contract record ({name, thmx}) or the spec's inline fields."""
    if not isinstance(d, dict) or not d.get('name') and not (d.get('thmx') or {}).get('Name'):
        raise InputError('%s: record needs a "name"' % where)
    name = normalise_name(d.get('name') or d['thmx']['Name'])
    if isinstance(d.get('thmx'), dict):
        rec = {k: str(v) for k, v in d['thmx'].items()}
        bad = set(rec) - set(MATERIAL_ATTRS if kind == 'material' else BC_ATTRS)
        if bad:
            raise InputError('%s: unknown thmx attribute(s) %s' % (where, sorted(bad)))
        rec['Name'] = name
        if kind == 'material':
            if rec.get('Type') == '1':
                rec.setdefault('CavityModel', '4')
            _check_cavity(rec, where)
        return _ordered(rec, MATERIAL_ATTRS if kind == 'material' else BC_ATTRS)
    if kind == 'material':
        e = f6(d.get('emissivity', 0.9))
        if d.get('type') == 'cavity':
            if d.get('cavity_standard', 'ISO15099') != 'ISO15099':
                raise InputError('%s: THERM 7.8 cavities are written as ISO 15099 (CavityModel 4) only, got %s'
                                 % (where, d.get('cavity_standard')))
            if d.get('gas', 'Air') != 'Air':
                raise InputError('%s: a .thmx frame cavity has no gas field (THERM uses Air); gas %r is not '
                                 'supported on 7.8' % (where, d.get('gas')))
            return {'Name': name, 'Index': '0', 'Type': '1', 'Conductivity': f6(d.get('conductivity', 0.025)),
                    'Tir': f6(0), 'Absorptivity': f6(0.5), 'EmissivityFront': e, 'EmissivityBack': e,
                    'RGBColor': d.get('color', '0x00FF00'), 'CavityModel': '4'}
        if 'conductivity' not in d:
            raise InputError('%s: material needs "conductivity" (W/mK) or a "thmx" dict' % where)
        return {'Name': name, 'Index': '0', 'Type': '0', 'Conductivity': f6(d['conductivity']), 'Tir': f6(0),
                'EmissivityFront': e, 'EmissivityBack': f6(d.get('emissivity_back', d.get('emissivity', 0.9))),
                'RGBColor': d.get('color', '0x808080')}
    typ, rad = d.get('type', 'comprehensive'), d.get('radiation', 'automatic')
    if not (typ == 'simplified' or (typ == 'comprehensive' and rad == 'none')):
        raise InputError('%s: inline BC "%s": only simplified films (or radiation "none") are written for '
                         'THERM 7.8; pass a therm-library record with a "thmx" dict for other BCs' % (where, name))
    if 'temperature' not in d or 'film_coefficient' not in d:
        raise InputError('%s: BC needs "temperature" and "film_coefficient"' % where)
    rec = _bc(name, '1', d.get('color', '0x0080C0'))
    rec.update(H=f6(d['film_coefficient']), Temperature=f6(d['temperature']), Tr=f6(d['temperature']),
               ConvectionFlag='1', FluxFlag='0')
    return rec


class Records:
    """Material and BC thmx dicts keyed by normalised name; the first source added wins."""

    def __init__(self):
        self.by = {'material': {}, 'bc': {}}
        self.warnings = []

    def add(self, kind, rec):
        self.by[kind].setdefault(normalise_name(rec['Name']), rec)

    def find(self, kind, name):
        return self.by[kind].get(normalise_name(name)) if name else None

    def load(self, path):
        if path.lower().endswith('.thmx'):
            root = read(path)
            for kind, tag in (('material', 'Material'), ('bc', 'BoundaryCondition')):
                for el in root.iter(Q + tag):
                    rec = dict(el.attrib)
                    rec['Name'] = normalise_name(rec.get('Name', ''))
                    self.add(kind, rec)
            return
        try:
            with open(path, encoding='utf-8') as f:
                doc = json.load(f)
        except OSError as exc:
            raise InputError('cannot read library %s: %s' % (path, exc))
        except ValueError as exc:
            raise InputError('library %s is not valid JSON: %s' % (path, exc))
        for key, kind in (('materials', 'material'), ('boundary_conditions', 'bc')):
            for i, r in enumerate(doc.get(key) or []):
                if not isinstance(r, dict) or not isinstance(r.get('thmx'), dict):
                    self.warnings.append('%s %s[%d] "%s" has no "thmx" dict; skipped for THERM 7.8'
                                         % (path, key, i, r.get('name') if isinstance(r, dict) else r))
                    continue
                self.add(kind, record_from_json(kind, r, '%s %s[%d]' % (path, key, i)))


def read(path):
    try:
        root = ET.parse(path).getroot()
    except OSError as exc:
        raise InputError('cannot read %s: %s' % (path, exc))
    except ET.ParseError as exc:
        raise InputError('%s is not valid XML: %s' % (path, exc))
    if root.tag != Q + 'THERM-XML':
        raise InputError('%s: root is %s, not THERM-XML; not a THERM 7.8 .thmx' % (path, root.tag))
    return root


def write(path, root):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='ascii', errors='xmlcharrefreplace', newline='\n') as f:
        f.write('<?xml version="1.0"?>\n' + ET.tostring(root, encoding='unicode') + '\n')


def _el(parent, tag, text=None, attrib=None):
    e = ET.SubElement(parent, Q + tag, attrib or {})
    if text is not None:
        e.text = text
    return e


def _points(parent, pts):
    for i, (x, y) in enumerate(pts):
        _el(parent, 'Point', attrib={'index': str(i), 'x': f6(x), 'y': f6(y)})


def check_ids(root):
    ids = [e.get('ID') for e in list(root.iter(Q + 'Polygon')) + list(root.iter(Q + 'BCPolygon'))]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    return ['Polygon/BCPolygon ID %s used twice; THERM 7.8 crashes on shared IDs' % i for i in dup]


# ================================================================ build

def resolver(spec, libraries):
    """(records, resolve(kind, ref, where)) for therm_model's shared geometry checks."""
    recs = Records()
    for key, kind in (('materials', 'material'), ('boundary_conditions', 'bc')):
        for i, d in enumerate(spec.get(key, [])):
            recs.add(kind, record_from_json(kind, d, '%s[%d]' % (key, i)))
    for path in libraries:
        recs.load(path)
    for rec in BUILTIN_BCS.values():
        recs.add('bc', dict(rec))

    def resolve(kind, ref, where):
        if isinstance(ref, str):
            rec, label = recs.find(kind, ref), ref
        elif isinstance(ref, dict):
            label = ref.get('name')
            if 'thmx' in ref or 'conductivity' in ref or ref.get('type') == 'cavity' or 'temperature' in ref:
                new = record_from_json(kind, ref, where)
                recs.add(kind, new)
                rec = recs.find(kind, new['Name'])
            else:
                rec = recs.find(kind, label)
        else:
            raise InputError('%s: expected a name or a record object' % where)
        if rec is None:
            raise InputError('%s: %s "%s" not found in the spec or any --library file (7.8 needs records with '
                             'a "thmx" dict)' % (where, 'material' if kind == 'material' else 'boundary condition',
                                                 label))
        return rec
    return recs, resolve


def is_cavity(rec):
    return rec.get('Type') == '1'


def build(spec, g, out_path, warnings):
    """Write the .thmx for geometry g from therm_model._geometry."""
    check_out_name(out_path)
    polys, segs, edges, adiabatic = g['polys'], g['segs'], g['edges'], g['adiabatic']
    section = spec.get('cross_section', 'Sill')
    section = {'Mullion': 'Vertical Divider', 'Transom': 'Horizontal Divider'}.get(section, section)
    if section not in SECTIONS:
        raise InputError('cross_section "%s" is not a THERM 7.8 window section; use one of: %s'
                         % (section, ', '.join(SECTIONS)))
    mesh = dict({'parameter': 6, 'error_estimator': True, 'error_norm': 10, 'max_iterations': 5},
                **spec.get('mesh', {}))
    ignored = ['mesh.type simmetrix'] if str(mesh.get('type', 'quadtree')).lower().startswith('simmetrix') else []
    ignored += [k for k in ('gravity', 'model_purpose', 'u_value_basis', 'glazing_origin') if k in spec]
    if ignored:
        warnings.append('8.1-only settings ignored on THERM 7.8 (QuadTree mesher, nothing stored): %s'
                        % ', '.join(ignored))
    if not (isinstance(mesh['parameter'], int) and 1 <= mesh['parameter'] <= 20):
        raise InputError('mesh.parameter must be an integer 1..20 (THERM default 6)')
    if not (isinstance(mesh['max_iterations'], int) and mesh['max_iterations'] >= 1):
        raise InputError('mesh.max_iterations must be an integer >= 1')
    if not 0 < float(mesh['error_norm']) <= 100:
        raise InputError('mesh.error_norm must be a percentage in (0, 100]')

    root = ET.Element(Q + 'THERM-XML')
    now = spec.get('created') or datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    for tag, val in (('ThermVersion', THERM_VERSION), ('FileVersion', '1'), ('SaveDate', now),
                     ('Title', spec.get('title', '')), ('CreatedBy', ''), ('Company', ''), ('Client', ''),
                     ('CrossSectionType', section), ('Notes', spec.get('notes', 'Built by therm-model v1')),
                     ('Units', 'SI')):
        _el(root, tag, val)
    _el(root, 'MeshControl', attrib={'MeshLevel': str(mesh['parameter']),
                                     'ErrorCheckFlag': '1' if mesh['error_estimator'] else '0',
                                     'ErrorLimit': f6(mesh['error_norm']), 'MaxIterations': str(mesh['max_iterations']),
                                     'CMAflag': '0'})
    mats, bcs = [], [BUILTIN_BCS['Adiabatic']]
    for p in polys:
        if p['mat'] not in mats:
            mats.append(p['mat'])
    for s in segs:
        if s['bc'] not in bcs:
            bcs.append(s['bc'])
    cavities = [p for p in polys if p['cavity']]
    if cavities:
        bcs.append(BUILTIN_BCS['Frame Cavity Surface'])
    me = _el(root, 'Materials')
    for i, m in enumerate(mats):
        _check_cavity(m, 'material')
        _el(me, 'Material', attrib=_ordered(dict(m, Index=str(i + 1)), MATERIAL_ATTRS))
    be = _el(root, 'BoundaryConditions')
    for b in bcs:
        _el(be, 'BoundaryCondition', attrib=_ordered(dict(b), BC_ATTRS))
    pe = _el(root, 'Polygons')
    for i, p in enumerate(polys):
        e = _el(pe, 'Polygon', attrib={'ID': str(i + 1), 'Material': p['mat']['Name'], 'NSides': str(len(p['pts'])),
                                       'Type': '1', 'units': 'mm'})
        _points(e, p['pts'])
    bd = _el(root, 'Boundaries')
    count = [len(polys)]  # BCPolygon IDs continue after the polygon IDs: one namespace

    def seg(edge, bc, tag, poly_idx, enclosure, emis):
        count[0] += 1
        e = _el(bd, 'BCPolygon', attrib={'ID': str(count[0]), 'BC': bc, 'units': 'mm', 'MaterialName': '',
                                         'PolygonID': str(poly_idx + 1), 'EnclosureID': str(enclosure),
                                         'UFactorTag': tag, 'Emissivity': f6(emis), 'MaterialSide': 'Front',
                                         'IlluminatedSurface': 'FALSE'})
        _points(e, edge)

    def emis(pi):
        return float(polys[pi]['mat'].get('EmissivityFront', 0.9))

    for s in segs:
        for e in sorted(s['edges'], key=lambda e: geo.seg_param(e[0], s['a'], s['b'])[0]):
            seg(e, s['bc']['Name'], s['tag'], edges[e], 0, emis(edges[e]))
    for e in adiabatic:
        seg(e, 'Adiabatic', '', edges[e], 0, emis(edges[e]))
    for n, p in enumerate(cavities):
        pi = polys.index(p)
        k = len(p['pts'])
        for j in range(k):
            a, b = p['pts'][j], p['pts'][(j + 1) % k]
            seg((a, b), 'Frame Cavity Surface', '', pi, n + 1, emis(edges[(b, a)]))
    ET.indent(root, '\t')
    issues = check_ids(root)
    if issues:
        raise InputError('internal check failed: %s' % issues)
    write(out_path, root)
    tags = {}
    for s in segs:
        if s['tag']:
            t = tags.setdefault(s['tag'], {'segments': 0, 'length_mm': 0.0})
            t['segments'] += len(s['edges'])
            t['length_mm'] = round(t['length_mm'] + sum(geo.dist(*e) for e in s['edges']), 6)
    return {'format': 'thmx', 'therm_version': '7.8', 'thmx': os.path.abspath(out_path),
            'polygons': {p['label']: i + 1 for i, p in enumerate(polys)},
            'counts': {'polygons': len(polys), 'boundaries': count[0] - len(polys), 'frame_cavities': len(cavities)},
            'materials': [m['Name'] for m in mats], 'boundary_conditions': [b['Name'] for b in bcs], 'tags': tags,
            'adiabatic_edges': [{'polygon': polys[edges[e]]['label'], 'start': list(e[0]), 'end': list(e[1])}
                                for e in adiabatic]}


# ================================================================ inspect

def _pts(el):
    return [(float(q.get('x')), float(q.get('y'))) for q in el.findall(Q + 'Point')]


def _area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2.0


def inspect(path, detail=False):
    root = read(path)
    issues = check_ids(root)
    if '.' in os.path.splitext(os.path.basename(path))[0]:
        issues.append('file name has a period in the stem; THERM 7.8 hangs on it (rename before solving)')
    mat_list = list(root.iter(Q + 'Material'))
    mats = {normalise_name(m.get('Name', '')): m for m in mat_list}
    bcs = {normalise_name(b.get('Name', '')): b for b in root.iter(Q + 'BoundaryCondition')}
    used, polys = {}, []
    for p in root.iter(Q + 'Polygon'):
        pts = _pts(p)
        name = normalise_name(p.get('Material', ''))
        if name not in mats:
            issues.append('polygon %s: material "%s" is not inline; THERM looks it up in its installed '
                          'material.lib and fails if it is missing' % (p.get('ID'), p.get('Material')))
        used[name] = used.get(name, 0) + 1
        d = {'id': p.get('ID'), 'type': p.get('Type'), 'material': p.get('Material'), 'points': len(pts),
             'area_mm2': round(abs(_area(pts)), 3) if len(pts) > 2 else 0.0}
        if detail:
            d['coords'] = [list(q) for q in pts]
        polys.append(d)
    groups, tags, bounds, bc_used = {}, {}, [], {}
    for b in root.iter(Q + 'BCPolygon'):
        pts = _pts(b)
        L = sum(((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5
                for i in range(len(pts) - 1))
        name, tag = b.get('BC', ''), b.get('UFactorTag', '')
        if normalise_name(name) not in bcs:
            issues.append('boundary %s: BC "%s" is not inline; THERM looks it up in its installed BC.lib'
                          % (b.get('ID'), name))
        bc_used[normalise_name(name)] = bc_used.get(normalise_name(name), 0) + 1
        g = groups.setdefault((name, tag), {'bc': name, 'tag': tag, 'segments': 0, 'length_mm': 0})
        g['segments'] += 1
        g['length_mm'] = round(g['length_mm'] + L, 3)
        if tag:
            t = tags.setdefault(tag, {'segments': 0, 'length_mm': 0, 'bcs': []})
            t['segments'] += 1
            t['length_mm'] = round(t['length_mm'] + L, 3)
            if name not in t['bcs']:
                t['bcs'].append(name)
        if detail:
            bounds.append({'id': b.get('ID'), 'bc': name, 'tag': tag, 'polygon': b.get('PolygonID'),
                           'enclosure': b.get('EnclosureID'), 'points': [list(q) for q in pts]})
    for m in mats.values():
        if m.get('Type') == '1' and m.get('CavityModel') not in (None, '4'):
            issues.append('material "%s": CavityModel %s is untested on THERM 7.8 (use 4)'
                          % (m.get('Name'), m.get('CavityModel')))
    mc = root.find(Q + 'MeshControl')
    results = root.find(Q + 'Results')
    return {
        'format': 'thmx', 'therm_version': root.findtext(Q + 'ThermVersion'), 'input': os.path.abspath(path),
        'solved': results is not None, 'has_mesh': root.find(Q + 'Mesh') is not None,
        'cases': [c.findtext(Q + 'ModelType') for c in results.findall(Q + 'Case')] if results is not None else [],
        'title': root.findtext(Q + 'Title'), 'cross_section': root.findtext(Q + 'CrossSectionType'),
        'mesh': dict(mc.attrib) if mc is not None else None,
        'counts': {'polygons': len(polys), 'boundaries': sum(g['segments'] for g in groups.values()),
                   'frame_cavities': sum(1 for p in polys if (mats.get(normalise_name(p['material'])) is not None
                                                               and mats[normalise_name(p['material'])].get('Type') == '1'))},
        'polygons': polys,
        'materials': [{'name': m.get('Name'), 'normalised': n, 'kind': 'cavity' if m.get('Type') == '1' else 'solid',
                       'conductivity': float(m.get('Conductivity')) if m.get('Conductivity') else None,
                       'used_by': sum(1 for p in root.iter(Q + 'Polygon') if p.get('Material') == m.get('Name'))}
                      for m, n in ((m, normalise_name(m.get('Name', ''))) for m in mat_list)],
        'boundary_conditions': [{'name': b.get('Name'), 'normalised': n, 'type': b.get('Type'),
                                 'film_coefficient': float(b.get('H')) if b.get('H') else None,
                                 'temperature': float(b.get('Temperature')) if b.get('Temperature') else None,
                                 'used_by': bc_used.get(n, 0)} for n, b in bcs.items()],
        'boundary_groups': list(groups.values()), 'tags': tags,
        'glazing_systems': [dict(g.attrib) for g in root.iter(Q + 'GlazingSystem')],
        **({'boundaries': bounds} if detail else {}),
        'issues': issues,
    }


# ================================================================ patch

def _record_el(root, kind, name):
    tag = 'Material' if kind == 'material' else 'BoundaryCondition'
    return next((e for e in root.iter(Q + tag) if normalise_name(e.get('Name', '')) == normalise_name(name)), None)


def _add_record(root, kind, rec, notes):
    if _record_el(root, kind, rec['Name']) is not None:
        return
    holder = root.find(Q + ('Materials' if kind == 'material' else 'BoundaryConditions'))
    rec = dict(rec)
    if kind == 'material':
        rec['Index'] = str(max([int(e.get('Index', 0)) for e in holder] + [0]) + 1)
    e = ET.Element(Q + ('Material' if kind == 'material' else 'BoundaryCondition'),
                   _ordered(rec, MATERIAL_ATTRS if kind == 'material' else BC_ATTRS))
    e.tail = holder[-1].tail if len(holder) else '\n'
    if len(holder):
        holder[-1].tail = holder.text or '\n\t'
    holder.append(e)
    notes.append('added record "%s"' % rec['Name'])


def _drop_unused(root, kind, names, notes):
    holder = root.find(Q + ('Materials' if kind == 'material' else 'BoundaryConditions'))
    refs = ({normalise_name(p.get('Material', '')) for p in root.iter(Q + 'Polygon')} if kind == 'material'
            else {normalise_name(b.get('BC', '')) for b in root.iter(Q + 'BCPolygon')})
    for e in list(holder):
        n = normalise_name(e.get('Name', ''))
        if n in names and n not in refs and n not in BUILTIN_BCS:
            holder.remove(e)
            notes.append('removed now-unused record "%s"' % e.get('Name'))


def _select(root, selector):
    out = []
    for b in root.iter(Q + 'BCPolygon'):
        if normalise_name(b.get('BC', '')) in INTERNAL_BCS or b.get('EnclosureID', '0') != '0':
            continue
        if selector.startswith('id:'):
            ok = b.get('ID') == selector[3:]
        elif selector.startswith('tag:'):
            ok = b.get('UFactorTag', '') == selector[4:]
        else:
            name = selector[5:] if selector.startswith('name:') else selector
            ok = normalise_name(b.get('BC', '')) == normalise_name(name)
        if ok:
            out.append(b)
    if not out:
        raise InputError('no boundary matches selector "%s" (use a BC name, tag:<tag> or id:<BCPolygon ID>)'
                         % selector)
    return out


def set_attr(el, attr, value, where):
    kind_attrs = MATERIAL_ATTRS if el.tag == Q + 'Material' else BC_ATTRS
    if attr not in kind_attrs or attr in ('Name', 'Index'):
        raise InputError('%s: attribute "%s" cannot be set; allowed: %s'
                         % (where, attr, ', '.join(a for a in kind_attrs if a not in ('Name', 'Index'))))
    if attr not in TEXT_ATTRS:
        try:
            v = float(value)
        except ValueError:
            raise InputError('%s: %s must be a number, got %r' % (where, attr, value))
        value = str(int(v)) if attr in INT_ATTRS else f6(v)
    if el.tag == Q + 'Material' and (attr == 'CavityModel' and value != '4'):
        raise InputError('%s: only CavityModel 4 (ISO 15099) is tested on THERM 7.8' % where)
    old = el.get(attr)
    el.set(attr, value)
    return old, value


def patch(path, out_path, args, libraries):
    check_out_name(out_path)
    root = read(path)
    recs = Records()
    for p in libraries:
        recs.load(p)
    notes, changed = list(recs.warnings), False

    def lookup(kind, name, opt):
        rec = recs.find(kind, name)
        if rec is None:
            el = _record_el(root, kind, name)
            rec = dict(el.attrib, Name=normalise_name(el.get('Name'))) if el is not None else None
        if rec is None and kind == 'bc' and name in BUILTIN_BCS:
            rec = dict(BUILTIN_BCS[name])
        if rec is None:
            raise InputError('%s: %s "%s" not in the file or any --library record with a "thmx" dict'
                             % (opt, 'material' if kind == 'material' else 'BC', name))
        return rec

    for name, attr, value in getattr(args, 'set_record', None) or []:
        el = _record_el(root, 'material', name)
        if el is None:
            el = _record_el(root, 'bc', name)
        if el is None:
            raise InputError('--set-record: no material or BC named "%s" (after stripping prefixes and '
                             '_Cavity_<n>) in the file' % name)
        old, new = set_attr(el, attr, value, '--set-record "%s"' % name)
        notes.append('set-record: %s %s %s -> %s' % (el.get('Name'), attr, old, new))
        changed = True
    for old, new in args.swap_material or []:
        hits = [p for p in root.iter(Q + 'Polygon') if normalise_name(p.get('Material', '')) == normalise_name(old)]
        if not hits:
            raise InputError('--swap-material: no polygon uses material "%s"' % old)
        rec = lookup('material', new, '--swap-material')
        old_el = _record_el(root, 'material', old)
        if is_cavity(rec) or (old_el is not None and old_el.get('Type') == '1'):
            raise InputError('--swap-material: frame-cavity materials cannot be swapped by patch; rebuild instead')
        _add_record(root, 'material', rec, notes)
        name = _record_el(root, 'material', rec['Name']).get('Name')
        ids = {p.get('ID') for p in hits}
        for p in hits:
            p.set('Material', name)
        n_b = 0
        for b in root.iter(Q + 'BCPolygon'):
            if b.get('PolygonID') in ids and b.get('EnclosureID', '0') == '0' and 'EmissivityFront' in rec:
                b.set('Emissivity', f6(rec['EmissivityFront']))
                n_b += 1
        _drop_unused(root, 'material', {normalise_name(old)}, notes)
        notes.append('swap-material: %d polygon(s) %s -> %s; emissivity updated on %d boundary(ies)'
                     % (len(hits), old, name, n_b))
        changed = True
    for sel, new in args.set_bc or []:
        rec = lookup('bc', new, '--set-bc')
        hits = _select(root, sel)
        _add_record(root, 'bc', rec, notes)
        name = _record_el(root, 'bc', rec['Name']).get('Name')
        olds = {normalise_name(b.get('BC', '')) for b in hits}
        for b in hits:
            b.set('BC', name)
        _drop_unused(root, 'bc', olds - {normalise_name(name)}, notes)
        notes.append('set-bc: %d boundary(ies) %s -> %s' % (len(hits), sorted(olds), name))
        changed = True
    for sel, tag in args.retag or []:
        hits = _select(root, sel)
        for b in hits:
            b.set('UFactorTag', tag)
        notes.append('retag: %d boundary(ies) -> "%s"' % (len(hits), tag))
        changed = True
    pts = [q for holder in ('Polygons', 'Boundaries') for h in root.findall(Q + holder) for q in h.iter(Q + 'Point')]
    if args.scale is not None:
        s = args.scale
        if s <= 0:
            raise InputError('--scale must be > 0')
        if root.find('%sGlazingSystems/%sGlazingSystem' % (Q, Q)) is not None:
            raise InputError('--scale: model has a glazing system whose width would not scale')
        for q in pts:
            q.set('x', f6(float(q.get('x')) * s))
            q.set('y', f6(float(q.get('y')) * s))
        notes.append('scale %s: moved %d points' % (s, len(pts)))
        changed = True
    if args.offset is not None:
        dx, dy = args.offset
        for q in pts:
            q.set('x', f6(float(q.get('x')) + dx))
            q.set('y', f6(float(q.get('y')) + dy))
        notes.append('offset %s,%s: moved %d points' % (dx, dy, len(pts)))
        changed = True
    if not changed:
        raise InputError('patch: no edit given (use --set-record, --swap-material, --set-bc, --retag, --scale '
                         'or --offset)')
    removed = []
    if not args.keep_results:
        for tag in ('Mesh', 'Results'):
            for e in root.findall(Q + tag):
                root.remove(e)
                removed.append(tag)
    issues = check_ids(root)
    if issues:
        raise InputError('%s: %s' % (path, '; '.join(issues)))
    write(out_path, root)
    return {'format': 'thmx', 'therm_version': '7.8', 'input': os.path.abspath(path),
            'thmx': os.path.abspath(out_path), 'removed_elements': removed, 'changes': notes}

