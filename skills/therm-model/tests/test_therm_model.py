"""Tests for therm-model against the research samples. Run from the skill folder: python -m unittest"""
import contextlib
import glob
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPT = os.path.join(SKILL, 'scripts', 'therm_model.py')
EXAMPLES = os.path.join(SKILL, 'examples')
SAMPLES = os.path.join(SKILL, '..', '..', 'research', 'samples')
FAIRY = os.path.join(SAMPLES, 'fairyfly')
LBNL = os.path.join(SAMPLES, 'lbnl-libraryfemtherm', 'products')
LIB = os.path.join(SAMPLES, 'therm81-lib-copies')
ALL_SAMPLES = sorted(glob.glob(os.path.join(SAMPLES, '**', '*.thmz'), recursive=True))

spec_ = importlib.util.spec_from_file_location('therm_model', SCRIPT)
tm = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(tm)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tm.main([str(a) for a in argv])
    return code, (json.loads(out.getvalue()) if code == 0 else None), err.getvalue()


def load(name):
    with open(os.path.join(EXAMPLES, name), encoding='utf-8') as f:
        return json.load(f)


def entry(path, name):
    with zipfile.ZipFile(path) as z:
        return z.read(name)


def tag_paths(data):
    def walk(e, pre):
        p = pre + '/' + e.tag
        yield p
        for c in e:
            yield from walk(c, p)
    return list(walk(ET.fromstring(data), ''))


def canon(data):
    """Element tree as nested tuples, whitespace-insensitive."""
    def c(e):
        return (e.tag, (e.text or '').strip(), tuple(c(x) for x in e))
    return c(ET.fromstring(data))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def build_spec(self, spec, *extra):
        path = self.p('spec.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(spec, f)
        return run('build', path, '--thmz', self.p('out.thmz'), *extra)


class BuildWall(Tmp):
    """wall-3poly.json must reproduce fairyfly test_no_result.thmz structurally."""

    def setUp(self):
        super().setUp()
        self.code, self.res, _ = run('build', os.path.join(EXAMPLES, 'wall-3poly.json'), '--thmz', self.p('w.thmz'))
        self.ref = os.path.join(FAIRY, 'test_no_result.thmz')

    def test_same_entries_and_element_order(self):
        self.assertEqual(self.code, 0)
        with zipfile.ZipFile(self.ref) as a, zipfile.ZipFile(self.p('w.thmz')) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for n in a.namelist():
                self.assertEqual(tag_paths(a.read(n)), tag_paths(b.read(n)), n)

    def test_same_geometry_materials_and_tags(self):
        _, a, _ = run('inspect', self.ref)
        _, b, _ = run('inspect', self.p('w.thmz'))
        strip = lambda ps: [{k: v for k, v in p.items() if k != 'uuid'} for p in ps]  # noqa: E731
        self.assertEqual(strip(a['polygons']), strip(b['polygons']))
        self.assertEqual(sorted(a['materials'], key=str), sorted(b['materials'], key=str))
        self.assertEqual(sorted(a['boundary_conditions'], key=str), sorted(b['boundary_conditions'], key=str))
        self.assertEqual(a['tags'], b['tags'])
        self.assertEqual(sorted(a['boundary_groups'], key=str), sorted(b['boundary_groups'], key=str))
        self.assertEqual(entry(self.ref, 'Gases.xml'), entry(self.p('w.thmz'), 'Gases.xml'))
        self.assertEqual(b['issues'], [])

    def test_deterministic(self):
        spec = load('wall-3poly.json')
        spec['created'] = '2026-01-01 00:00:00'
        self.build_spec(spec)
        first = entry(self.p('out.thmz'), 'Model.xml')
        self.build_spec(spec)
        self.assertEqual(first, entry(self.p('out.thmz'), 'Model.xml'))


class BuildFrame(Tmp):
    def setUp(self):
        super().setUp()
        self.code, self.res, self.err = run('build', os.path.join(EXAMPLES, 'simple-frame.json'),
                                             '--thmz', self.p('f.thmz'))
        self.model = ET.fromstring(entry(self.p('f.thmz'), 'Model.xml'))

    def test_summary(self):
        self.assertEqual(self.code, 0, self.err)
        self.assertEqual(self.res['counts'], {'polygons': 14, 'boundaries': 29, 'frame_cavities': 3})
        self.assertEqual(self.res['tags'], {'Frame': {'segments': 3, 'length_mm': 71.0},
                                            'Edge': {'segments': 1, 'length_mm': 63.5}})
        self.assertEqual(self.res['gases'], [{'kind': 'PureGas', 'name': 'Air'}, {'kind': 'Gas', 'name': 'Air'}])
        self.assertEqual(len(self.res['adiabatic_edges']), 8)  # frame bottom (7 pieces) + panel top
        self.assertTrue(any('inserted' in f for f in self.res['fixes']))

    def test_cavities_linked(self):
        self.assertEqual([c.tag for c in self.model][4:7], ['GlazingOrigin', 'Cavities', 'Polygons'])
        cav = {c.findtext('UUID') for c in self.model.iter('Cavity')}
        polys = [p for p in self.model.iter('Polygon') if p.findtext('CavityUUID')]
        self.assertEqual({p.findtext('CavityUUID') for p in polys}, cav)
        surf = [b for b in self.model.iter('Boundary') if b.findtext('Type') == 'Frame Cavity']
        self.assertEqual(len(surf), sum(len(p.find('Points')) for p in polys))
        self.assertTrue(all(b.findtext('NeighborPolygonUUID2') for b in surf))

    def test_only_used_records_copied(self):
        mats = ET.fromstring(entry(self.p('f.thmz'), 'Materials.xml'))
        self.assertEqual(len(mats.findall('Material')), 4)
        bcs = ET.fromstring(entry(self.p('f.thmz'), 'SteadyStateBC.xml'))
        self.assertEqual([b.findtext('Name') for b in bcs.findall('BoundaryCondition')],
                         ['Adiabatic', 'ISO 10077-2 Exterior (Rse 0.04)', 'ISO 10077-2 Interior (Rsi 0.13)'])

    def test_inspect_clean(self):
        _, r, _ = run('inspect', self.p('f.thmz'))
        self.assertEqual(r['issues'], [])
        self.assertFalse(r['solved'])


class LibraryRefs(Tmp):
    def test_names_resolved_from_therm_lib_and_only_used_copied(self):
        spec = load('wall-3poly.json')
        spec.pop('boundary_conditions')
        spec['materials'] = spec['materials'][1:]
        spec['polygons'][0]['material'] = {'name': 'Aluminum Alloy (painted)'}
        spec['boundaries'][0]['bc'] = 'NFRC 100-2010 Exterior'
        spec['boundaries'][1]['bc'] = 'Inerior Aluminum Frame (convection only)'
        code, r, err = self.build_spec(spec, '--library', os.path.join(LIB, 'Materials.xml'),
                                       '--library', os.path.join(LIB, 'BoundaryConditionsSteadyState.xml'))
        self.assertEqual(code, 0, err)
        self.assertEqual(r['materials'], ['Aluminum Alloy (painted)', 'Insulation', 'Aerated Concrete'])
        mats = ET.fromstring(entry(self.p('out.thmz'), 'Materials.xml'))
        self.assertEqual(len(mats.findall('Material')), 3)
        bcs = ET.fromstring(entry(self.p('out.thmz'), 'SteadyStateBC.xml'))
        self.assertEqual(len(bcs.findall('BoundaryCondition')), 3)

    def test_uuid_name_mismatch_fails(self):
        spec = load('wall-3poly.json')
        spec['polygons'][0]['material'] = {'name': 'Insulation', 'uuid': '6442842d-7c8f-4231-a9b3-64302e3b2bc4'}
        code, _, err = self.build_spec(spec)
        self.assertEqual(code, 1)
        self.assertIn('polygons[0]', err)

    def test_json_library(self):
        lib = {'materials': [{'name': 'Mat A', 'uuid': '11111111-1111-4111-8111-111111111111', 'conductivity': 1.0,
                              'emissivity': 0.9}]}
        with open(self.p('lib.json'), 'w') as f:
            json.dump(lib, f)
        spec = load('wall-3poly.json')
        spec['polygons'][1]['material'] = 'Mat A'
        code, r, err = self.build_spec(spec, '--library', self.p('lib.json'))
        self.assertEqual(code, 0, err)
        self.assertIn('Mat A', r['materials'])
        self.assertNotIn('Insulation', r['materials'])


class Validation(Tmp):
    def expect_error(self, spec, *words):
        code, _, err = self.build_spec(spec)
        self.assertEqual(code, 1, err)
        for w in words:
            self.assertIn(w, err)
        self.assertFalse(os.path.exists(self.p('out.thmz')))

    def test_clockwise_is_fixed_and_reported(self):
        spec = load('wall-3poly.json')
        spec['polygons'][1]['points'].reverse()
        code, r, _ = self.build_spec(spec)
        self.assertEqual(code, 0)
        self.assertTrue(any("'insulation'" in f and 'clockwise' in f for f in r['fixes']))

    def test_overlap(self):
        spec = load('wall-3poly.json')
        spec['polygons'][1]['points'] = [[150, -100], [150, -600], [400, -600], [400, -100]]
        self.expect_error(spec, 'overlaps', "'concrete'", "'insulation'")

    def test_unknown_material(self):
        spec = load('wall-3poly.json')
        spec['polygons'][2]['material'] = 'Unobtainium'
        self.expect_error(spec, "polygons[2] 'aerated'", 'Unobtainium')

    def test_open_polygon(self):
        spec = load('wall-3poly.json')
        spec['polygons'][0]['points'] = [[100, -100], [100, -600]]
        self.expect_error(spec, "polygons[0] 'concrete'", 'closed')

    def test_self_intersecting(self):
        spec = load('wall-3poly.json')
        spec['polygons'][0]['points'] = [[100, -100], [100, -600], [200, -600], [200, -100], [150, -700]]
        self.expect_error(spec, "polygons[0] 'concrete'", 'self-intersecting')

    def test_hole(self):
        spec = load('wall-3poly.json')
        ring = [[[200, -100], [200, -600], [250, -600], [250, -100]], [[350, -100], [350, -600], [400, -600], [400, -100]],
                [[250, -600], [350, -600], [350, -400], [250, -400]], [[250, -200], [350, -200], [350, -100], [250, -100]]]
        spec['polygons'][1:2] = [{'id': 'ins%d' % i, 'material': 'Insulation', 'points': r} for i, r in enumerate(ring)]
        self.expect_error(spec, 'unfilled hole')

    def test_disjoint(self):
        spec = load('wall-3poly.json')
        spec['polygons'][2]['points'] = [[420, -100], [420, -600], [500, -600], [500, -100]]
        self.expect_error(spec, 'more than one separate region')

    def test_boundary_off_the_outside(self):
        spec = load('wall-3poly.json')
        spec['boundaries'][0] = {'start': [200, -100], 'end': [200, -600], 'bc': 'Generic Exterior'}
        self.expect_error(spec, 'boundaries[0]', 'outer edge')

    def test_needs_two_bcs(self):
        spec = load('wall-3poly.json')
        spec['boundaries'] = spec['boundaries'][:1]
        self.expect_error(spec, 'two different non-adiabatic')

    def test_bad_cross_section(self):
        spec = load('wall-3poly.json')
        spec['cross_section'] = 'Window'
        self.expect_error(spec, 'cross_section')

    def test_mullion_alias(self):
        spec = load('wall-3poly.json')
        spec['cross_section'] = 'Mullion'
        code, _, _ = self.build_spec(spec)
        self.assertEqual(code, 0)
        m = ET.fromstring(entry(self.p('out.thmz'), 'Model.xml'))
        self.assertEqual(m.findtext('Properties/ModelExposure/Exposure/WindowCrossSection'), 'Vertical Divider')


class InspectSamples(Tmp):
    SOLVED = {'test_result.thmz', 'sample-sill.thmz', 'ID50_CS_single.thmz', 'mullion-jamb-xml.thmz',
              'mullion-jamb-json.thmz'}

    def test_every_sample(self):
        self.assertEqual(len(ALL_SAMPLES), 9)
        for path in ALL_SAMPLES:
            with self.subTest(sample=os.path.basename(path)):
                code, r, err = run('inspect', path)
                self.assertEqual(code, 0, err)
                self.assertEqual((r['skill'], r['version']), ('therm-model', 1))
                self.assertEqual(r['solved'], os.path.basename(path) in self.SOLVED)
                self.assertEqual(r['issues'], [])
                self.assertGreater(r['counts']['polygons'], 0)

    def test_json_variant_matches_xml(self):
        _, a, _ = run('inspect', os.path.join(LBNL, 'mullion-jamb-xml.thmz'))
        _, b, _ = run('inspect', os.path.join(LBNL, 'mullion-jamb-json.thmz'))
        self.assertEqual(a['counts'], b['counts'])
        self.assertEqual(a['tags'], b['tags'])
        self.assertEqual(b['format'], 'json')

    def test_round_trip_through_patch(self):
        """Re-writing Model.xml (no-op offset) keeps every value: inspect and element trees are unchanged."""
        for path in ALL_SAMPLES:
            if 'json' in os.path.basename(path):
                continue
            with self.subTest(sample=os.path.basename(path)):
                code, _, err = run('patch', path, '--thmz', self.p('rt.thmz'), '--offset', '0,0', '--keep-results')
                self.assertEqual(code, 0, err)
                _, a, _ = run('inspect', path)
                _, b, _ = run('inspect', self.p('rt.thmz'))
                a.pop('input'), b.pop('input')
                self.assertEqual(a, b)
                self.assertEqual(canon(entry(path, 'Model.xml')), canon(entry(self.p('rt.thmz'), 'Model.xml')))


class Patch(Tmp):
    def setUp(self):
        super().setUp()
        run('build', os.path.join(EXAMPLES, 'simple-frame.json'), '--thmz', self.p('f.thmz'))

    def test_swap_setbc_retag_change_only_targets(self):
        code, r, err = run('patch', self.p('f.thmz'), '--thmz', self.p('g.thmz'),
                           '--library', os.path.join(LIB, 'Materials.xml'),
                           '--library', os.path.join(LIB, 'BoundaryConditionsSteadyState.xml'),
                           '--swap-material', 'Polyamide 6.6 with 25% glass fibre (ISO 10077-2)',
                           'Polyamide 6.6 with 25% glass fiber',
                           '--set-bc', 'ISO 10077-2 Exterior (Rse 0.04)', 'NFRC 100-2010 Exterior',
                           '--retag', 'tag:Edge', '')
        self.assertEqual(code, 0, err)
        self.assertEqual(entry(self.p('f.thmz'), 'Gases.xml'), entry(self.p('g.thmz'), 'Gases.xml'))
        a = ET.fromstring(entry(self.p('f.thmz'), 'Model.xml'))
        b = ET.fromstring(entry(self.p('g.thmz'), 'Model.xml'))
        diffs = set()
        for x, y in zip(a.iter(), b.iter()):
            self.assertEqual(x.tag, y.tag)
            if (x.text or '').strip() != (y.text or '').strip():
                diffs.add(x.tag)
        self.assertEqual(diffs, {'MaterialUUID', 'MaterialName', 'Name', 'FluxTag'})
        _, i, _ = run('inspect', self.p('g.thmz'))
        self.assertEqual(i['issues'], [])
        self.assertNotIn('Edge', i['tags'])
        names = [m['name'] for m in i['materials']]
        self.assertIn('Polyamide 6.6 with 25% glass fiber', names)
        self.assertNotIn('Polyamide 6.6 with 25% glass fibre (ISO 10077-2)', names)

    def test_retag_is_a_one_line_text_diff(self):
        src = os.path.join(LBNL, 'sample-sill.thmz')
        code, r, _ = run('patch', src, '--thmz', self.p('s.thmz'), '--retag', 'tag:Edge', 'EdgeX', '--keep-results')
        self.assertEqual(code, 0)
        old = entry(src, 'Model.xml').decode().splitlines()
        new = entry(self.p('s.thmz'), 'Model.xml').decode().splitlines()
        changed = [(o, n) for o, n in zip(old, new) if o != n]
        self.assertEqual(changed, [('\t\t\t<FluxTag>Edge</FluxTag>', '\t\t\t<FluxTag>EdgeX</FluxTag>')])
        for n in ('Materials.xml', 'SteadyStateBC.xml', 'Mesh.xml', 'SteadyStateResults.xml'):
            self.assertEqual(entry(src, n), entry(self.p('s.thmz'), n))

    def test_results_dropped_by_default(self):
        code, r, _ = run('patch', os.path.join(LBNL, 'sample-sill.thmz'), '--thmz', self.p('s.thmz'),
                         '--retag', 'tag:Edge', 'Edge')
        self.assertEqual(code, 0)
        self.assertEqual(r['removed_entries'], ['Mesh.xml', 'SteadyStateResults.xml', 'SteadyStateMeshResults.xml'])
        with zipfile.ZipFile(self.p('s.thmz')) as z:
            self.assertNotIn('SteadyStateResults.xml', z.namelist())

    def test_offset_and_back(self):
        run('patch', self.p('f.thmz'), '--thmz', self.p('o.thmz'), '--offset', '10.5,-3')
        run('patch', self.p('o.thmz'), '--thmz', self.p('b.thmz'), '--offset=-10.5,3')
        self.assertEqual(canon(entry(self.p('f.thmz'), 'Model.xml')), canon(entry(self.p('b.thmz'), 'Model.xml')))

    def test_scale_updates_cavity_area(self):
        run('patch', self.p('f.thmz'), '--thmz', self.p('s.thmz'), '--scale', '2')
        a = ET.fromstring(entry(self.p('f.thmz'), 'Model.xml')).find('Cavities/Cavity/Area').text
        b = ET.fromstring(entry(self.p('s.thmz'), 'Model.xml')).find('Cavities/Cavity/Area').text
        self.assertAlmostEqual(float(b), 4 * float(a))

    def test_refusals(self):
        cases = [
            (os.path.join(LBNL, 'sample-sill.thmz'), ['--scale', '2'], 'glazing system'),
            (self.p('f.thmz'), ['--swap-material', 'Frame Cavity ISO 15099 (air)', 'Calibration panel (ISO 10077-2)'],
             'frame-cavity'),
            (self.p('f.thmz'), ['--swap-material', 'nope', 'x'], 'nope'),
            (self.p('f.thmz'), ['--set-bc', 'tag:Nope', 'Adiabatic'], 'tag:Nope'),
            (self.p('f.thmz'), [], 'no edit'),
            (os.path.join(LBNL, 'mullion-jamb-json.thmz'), ['--offset', '0,0'], 'JSON'),
        ]
        for src, args, word in cases:
            with self.subTest(args=args):
                code, _, err = run('patch', src, '--thmz', self.p('x.thmz'), *args)
                self.assertEqual(code, 1)
                self.assertIn(word, err)


if __name__ == '__main__':
    unittest.main()
