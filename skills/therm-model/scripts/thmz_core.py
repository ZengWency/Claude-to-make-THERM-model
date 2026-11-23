"""Shared helpers for therm-model: .thmz zip I/O, THERM library records and 2-D geometry.

Standard library only. Formats follow what THERM 8.1.30 saves (see research/headless-interface.md
section 2), not the libraryfemtherm 0.24.0 schemas.
"""
import copy
import json
import os
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

TOL = 0.01  # mm; points closer than this are the same point
RESULT_ENTRIES = ("Mesh.xml", "SteadyStateResults.xml", "SteadyStateMeshResults.xml")
BC_ENTRIES = ("SteadyStateBC.xml", "BoundaryConditions.xml")


class InputError(Exception):
    """A user or input error (exit 1)."""


# ---------------------------------------------------------------- numbers and XML

def fmt(v):
    """Format a number the way THERM saves it: integers without '.0', else up to 6 decimals."""
    v = round(float(v), 6)
    if v == int(v):
        return str(int(v))
    return repr(v)


def sub(parent, tag, text=None):
    e = ET.SubElement(parent, tag)
    if text is not None:
        e.text = text if isinstance(text, str) else fmt(text) if not isinstance(text, bool) \
            else ('true' if text else 'false')
    return e


def xy(parent, tag, p):
    e = sub(parent, tag)
    sub(e, 'x', p[0])
    sub(e, 'y', p[1])
    return e


def to_bytes(root):
    """Serialise like THERM 8.1.30: tab indent, <a></a> for empty elements, no XML declaration."""
    root = copy.deepcopy(root)
    ET.indent(root, '\t')
    return ET.tostring(root, encoding='unicode', short_empty_elements=False).encode('utf-8')


def to_bytes_keep_layout(root):
    """Serialise without re-indenting, so whitespace of untouched elements is kept (used by patch)."""
    return ET.tostring(root, encoding='unicode', short_empty_elements=False).encode('utf-8')


def json_to_et(tag, value):
    """Turn a LibraryFEMTherm .json entry into the equivalent XML element tree."""
    e = ET.Element(tag)
    if isinstance(value, dict):
        for k, v in value.items():
            for item in (v if isinstance(v, list) else [v]):
                e.append(json_to_et(k, item))
    elif isinstance(value, bool):
        e.text = 'true' if value else 'false'
    elif value is not None:
        e.text = str(value)
    return e


# ---------------------------------------------------------------- zip I/O

def read_thmz(path):
    """Return (ordered list of ZipInfo, {name: bytes}). Raises InputError if not a readable zip."""
    if not os.path.isfile(path):
        raise InputError('file not found: %s' % path)
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            return infos, {i.filename: z.read(i.filename) for i in infos}
    except zipfile.BadZipFile:
        raise InputError('not a .thmz (zip) file: %s' % path)


def parse_entry(data, name):
    """Parse a .xml or .json entry to an Element; None if data is None."""
    if data is None:
        return None
    try:
        if name.endswith('.json'):
            d = json.loads(data)
            (tag, value), = d.items()
            return json_to_et(tag, value)
        return ET.fromstring(data)
    except (ET.ParseError, ValueError) as exc:
        raise InputError('cannot parse entry %s: %s' % (name, exc))


def get_entry(entries, base):
    """Find entry 'base' as .xml or .json. Returns (name, Element) or (None, None)."""
    for name in (base + '.xml', base + '.json'):
        if name in entries:
            return name, parse_entry(entries[name], name)
    return None, None


def write_thmz(path, items, infos=None):
    """Write [(name, bytes)] in order. infos maps name -> ZipInfo to keep date and compression."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in items:
            info = (infos or {}).get(name)
            if info is None:
                info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


# ---------------------------------------------------------------- library records

def record_name(rec):
    return (rec.findtext('Name') or '').strip()


def record_uuid(rec):
    return (rec.findtext('UUID') or '').strip()


def strip_project(name):
    """'sample-sill:Butyl Rubber' -> 'Butyl Rubber' (THERM prefixes project records this way)."""
    return name.split(':', 1)[1] if ':' in name else name


def material_emissivity(rec):
    v = rec.findtext('Solid/Optical/Integrated/Infrared/Emissivity-Front') if rec is not None else None
    return float(v) if v not in (None, '') else 0.9


def is_cavity(rec):
    return rec is not None and rec.find('Cavity') is not None


ADIABATIC_XML = """<BoundaryCondition><UUID>61d7bd1c-22c6-4ea0-8720-0696e8c194ad</UUID><Name>Adiabatic</Name>
<ProjectNameTag></ProjectNameTag><Protected>true</Protected><Color>0x000000</Color><IGUSurface>false</IGUSurface>
<Simplified><Temperature>0</Temperature><FilmCoefficient>0</FilmCoefficient><RelativeHumidity>0.5</RelativeHumidity>
</Simplified></BoundaryCondition>"""
AIR_PURE_XML = """<PureGas><UUID>8d33196f-f052-46e6-8353-bccb9a779f9c</UUID><Name>Air</Name><Protected>true</Protected>
<Properties><MolecularWeight>28.97</MolecularWeight><SpecificHeatRatio>1.4</SpecificHeatRatio>
<Conductivity><A>0.002873</A><B>7.76e-05</B><C>0</C></Conductivity><Viscosity><A>3.723e-06</A><B>4.94e-08</B><C>0</C>
</Viscosity><SpecificHeat><A>1002.737</A><B>0.012324</B><C>0</C></SpecificHeat></Properties></PureGas>"""
AIR_MIX_XML = """<Gas><UUID>6c2409e9-5296-46c1-be11-9029b59a549b</UUID><Name>Air</Name><Protected>true</Protected>
<Components><Component><Fraction>1</Fraction><PureGas>Air</PureGas></Component></Components></Gas>"""


def _clean(xml):
    e = ET.fromstring(xml)
    for x in e.iter():
        if x.text is not None and not x.text.strip():
            x.text = None
        x.tail = None
    return e


def simple_material(d, where):
    """Build a THERM Material record from {name, uuid, conductivity, emissivity, ...} or {type: cavity}."""
    name = d.get('name')
    if not name:
        raise InputError('%s: material record needs "name"' % where)
    m = ET.Element('Material')
    sub(m, 'UUID', d.get('uuid') or str(uuid.uuid5(uuid.NAMESPACE_URL, 'therm-model/material/' + name)))
    sub(m, 'Name', name)
    sub(m, 'Protected', bool(d.get('protected', False)))
    sub(m, 'Color', d.get('color', '0x808080'))
    if d.get('type', 'solid') == 'cavity':
        c = sub(m, 'Cavity')
        std = d.get('cavity_standard', 'ISO15099')
        if std not in ('ISO15099', 'ISO15099Ventilated', 'CEN'):
            raise InputError('%s: cavity_standard must be ISO15099, ISO15099Ventilated or CEN' % where)
        sub(c, 'CavityStandard', std)
        sub(c, 'Gas', d.get('gas', 'Air'))
        e = d.get('emissivity', 0.9)
        sub(c, 'EmissivitySide1', float(d.get('emissivity_side1', e)))
        sub(c, 'EmissivitySide2', float(d.get('emissivity_side2', e)))
        return m
    try:
        k = float(d['conductivity'])
    except (KeyError, TypeError, ValueError):
        raise InputError('%s: solid material "%s" needs a numeric "conductivity" (W/m-K)' % (where, name))
    if k <= 0:
        raise InputError('%s: conductivity of "%s" must be > 0' % (where, name))
    s = sub(m, 'Solid')
    h = sub(s, 'HygroThermal')
    sub(h, 'ThermalConductivityDry', k)
    for key, tag in (('density', 'BulkDensity'), ('porosity', 'Porosity'),
                     ('specific_heat', 'SpecificHeatCapacityDry'),
                     ('vapour_resistance', 'WaterVaporDiffusionResistanceFactor')):
        if key in d:
            sub(h, tag, float(d[key]))
    ir = sub(sub(sub(s, 'Optical'), 'Integrated'), 'Infrared')
    e = float(d.get('emissivity', 0.9))
    for side, key in (('Front', 'emissivity_front'), ('Back', 'emissivity_back')):
        v = float(d.get(key, e))
        if not 0 <= v <= 1:
            raise InputError('%s: emissivity of "%s" must be 0..1' % (where, name))
        sub(ir, 'Emissivity-' + side, v)
    return m


def simple_bc(d, where):
    """Build a steady-state BoundaryCondition record. type comprehensive (default) or simplified."""
    name = d.get('name')
    if not name:
        raise InputError('%s: boundary condition record needs "name"' % where)
    try:
        t, h = float(d['temperature']), float(d['film_coefficient'])
    except (KeyError, TypeError, ValueError):
        raise InputError('%s: BC "%s" needs numeric "temperature" (C) and "film_coefficient" (W/m2K)'
                         % (where, name))
    b = ET.Element('BoundaryCondition')
    sub(b, 'UUID', d.get('uuid') or str(uuid.uuid5(uuid.NAMESPACE_URL, 'therm-model/bc/' + name)))
    sub(b, 'Name', name)
    sub(b, 'ProjectNameTag', '')
    sub(b, 'Protected', bool(d.get('protected', False)))
    sub(b, 'Color', d.get('color', '0x0080c0'))
    sub(b, 'IGUSurface', False)
    rh = float(d.get('relative_humidity', 0.5))
    kind = d.get('type', 'comprehensive')
    if kind == 'simplified':
        s = sub(b, 'Simplified')
        sub(s, 'Temperature', t)
        sub(s, 'FilmCoefficient', h)
        sub(s, 'RelativeHumidity', rh)
        return b
    if kind != 'comprehensive':
        raise InputError('%s: BC type must be "comprehensive" or "simplified"' % where)
    c = sub(b, 'Comprehensive')
    sub(c, 'RelativeHumidity', rh)
    cv = sub(c, 'Convection')
    sub(cv, 'Temperature', t)
    sub(cv, 'FilmCoefficient', h)
    sub(sub(c, 'ConstantFlux'), 'Flux', 0)
    rad = d.get('radiation', 'automatic')
    if rad != 'none':
        tag = {'automatic': 'AutomaticEnclosure', 'blackbody': 'BlackBodyRadiation'}.get(rad)
        if tag is None:
            raise InputError('%s: radiation must be automatic, blackbody or none' % where)
        r = sub(sub(c, 'Radiation'), tag)
        sub(r, 'Temperature', float(d.get('radiation_temperature', t)))
        sub(r, 'Emissivity', float(d.get('radiation_emissivity', 1)))
    return b


class Library:
    """Named records by kind: material, bc, puregas, gas. First definition of a name wins."""

    KINDS = {'Material': 'material', 'BoundaryCondition': 'bc', 'PureGas': 'puregas', 'Gas': 'gas'}

    def __init__(self):
        self.by_name = {k: {} for k in self.KINDS.values()}
        self.by_uuid = {k: {} for k in self.KINDS.values()}

    def add(self, rec, source):
        """Records from the spec ('spec ...' sources) win over library files; within each, first wins."""
        kind = self.KINDS.get(rec.tag)
        if kind is None:
            return
        name, uid = record_name(rec), record_uuid(rec)
        old = self.by_name[kind].get(name)
        if old is not None:
            if record_uuid(old[0]) == uid or not source.startswith('spec'):
                return
            if old[1].startswith('spec'):
                raise InputError('%s: %s "%s" is defined twice with different UUIDs' % (source, kind, name))
        self.by_name[kind][name] = (rec, source)
        if uid:
            self.by_uuid[kind][uid] = (rec, source)

    def add_root(self, root, source):
        for rec in root:
            self.add(rec, source)

    def load(self, path):
        """Load a THERM lib XML (Materials/BoundaryConditions/Gases), a .thmz, or a JSON record file."""
        if path.lower().endswith('.thmz'):
            _, entries = read_thmz(path)
            for base in ('Materials', 'SteadyStateBC', 'BoundaryConditions', 'Gases'):
                _, root = get_entry(entries, base)
                if root is not None:
                    self.add_root(root, path)
            return
        if not os.path.isfile(path):
            raise InputError('library file not found: %s' % path)
        if path.lower().endswith('.json'):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except ValueError as exc:
                raise InputError('library %s: bad JSON: %s' % (path, exc))
            self.add_json(data, path)
            return
        try:
            self.add_root(ET.parse(path).getroot(), path)
        except ET.ParseError as exc:
            raise InputError('library %s: bad XML: %s' % (path, exc))

    def add_json(self, data, source):
        for key, kind in (('materials', 'material'), ('boundary_conditions', 'bc'), ('gases', 'gas')):
            for i, d in enumerate(data.get(key, [])):
                self.add(self.record_from_json(kind, d, '%s %s[%d]' % (source, key, i)), source)

    @staticmethod
    def record_from_json(kind, d, where):
        if isinstance(d, dict) and 'xml' in d:
            try:
                return _clean(d['xml'])
            except ET.ParseError as exc:
                raise InputError('%s: bad "xml": %s' % (where, exc))
        if kind == 'material':
            return simple_material(d, where)
        if kind == 'bc':
            return simple_bc(d, where)
        raise InputError('%s: gas records must carry THERM "xml"' % where)

    def find(self, kind, name=None, uid=None):
        """Find a record by uuid and/or name. Returns rec or None. Raises if uuid and name disagree."""
        hit = None
        if uid:
            hit = self.by_uuid[kind].get(uid)
            if hit and name and record_name(hit[0]) != name:
                raise InputError('%s UUID %s belongs to "%s", not "%s"' % (kind, uid, record_name(hit[0]), name))
        if hit is None and name:
            hit = self.by_name[kind].get(name)
            if hit and uid and record_uuid(hit[0]) != uid:
                raise InputError('%s "%s" has UUID %s, not %s' % (kind, name, record_uuid(hit[0]), uid))
        return hit[0] if hit else None


def builtin_library():
    lib = Library()
    for x in (ADIABATIC_XML, AIR_PURE_XML, AIR_MIX_XML):
        lib.add(_clean(x), 'built-in')
    return lib


# ---------------------------------------------------------------- geometry (mm, y up)

def signed_area(pts):
    n = len(pts)
    return sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2.0


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def seg_param(p, a, b):
    """Return (t, distance) of point p projected on segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return 0.0, dist(p, a)
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    tc = min(1.0, max(0.0, t))
    return t, dist(p, (a[0] + tc * dx, a[1] + tc * dy))


def on_segment(p, a, b, tol=TOL):
    t, d = seg_param(p, a, b)
    return d <= tol and -1e-9 <= t <= 1 + 1e-9


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(a, b, c, d):
    """True if segments a-b and c-d cross at a point interior to both (not touching, not collinear)."""
    d1, d2, d3, d4 = _orient(c, d, a), _orient(c, d, b), _orient(a, b, c), _orient(a, b, d)
    eps = 1e-9 * max(1.0, dist(a, b) * dist(c, d))
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
        ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps))


def point_in_polygon(p, pts, tol=TOL):
    """1 strictly inside, 0 on the boundary, -1 outside."""
    n = len(pts)
    for i in range(n):
        if on_segment(p, pts[i], pts[(i + 1) % n], tol):
            return 0
    inside = False
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return 1 if inside else -1


class Snapper:
    """Merge points closer than TOL so later matching can use exact tuple equality."""

    def __init__(self):
        self.points = []

    def __call__(self, p):
        p = (float(p[0]), float(p[1]))
        for q in self.points:
            if dist(p, q) <= TOL:
                return q
        self.points.append(p)
        return p


def insert_vertices(pts, candidates):
    """Insert every candidate point that lies strictly inside an edge of pts. Returns (new_pts, n_inserted)."""
    out, n = [], 0
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        out.append(a)
        mids = []
        for p in candidates:
            if p == a or p == b:
                continue
            t, d = seg_param(p, a, b)
            if d <= TOL and 0 < t < 1:
                mids.append((t, p))
        for _, p in sorted(mids):
            if p != out[-1]:
                out.append(p)
                n += 1
    return out, n


def outer_loops(edges):
    """Chain directed outer edges into loops. Returns list of point lists."""
    nxt = {}
    for a, b in edges:
        nxt.setdefault(a, []).append(b)
    loops, used = [], set()
    for a, b in edges:
        if (a, b) in used:
            continue
        loop, cur, start = [a], (a, b), a
        while cur not in used:
            used.add(cur)
            p = cur[1]
            if p == start:
                break
            loop.append(p)
            options = [q for q in nxt.get(p, []) if (p, q) not in used]
            if not options:
                break
            cur = (p, options[0])
        loops.append(loop)
    return loops
