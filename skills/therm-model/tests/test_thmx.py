"""THERM 7.8 .thmx tests for therm-model. Run from the skill folder: python -m unittest"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, 'scripts')
EXAMPLES = os.path.join(SKILL, 'examples')
RESEARCH = os.path.join(SKILL, '..', '..', 'research')
SILL78 = os.path.join(RESEARCH, 'samples', 'therm78', 'shipped', 'sample-sill_7.8.55.thmx')
FIXTURE = os.path.join(RESEARCH, 'fixtures', 'library-78.json')
Q = '{http://windows.lbl.gov}'

sys.path.insert(0, SCRIPTS)
spec_ = importlib.util.spec_from_file_location('therm_model', os.path.join(SCRIPTS, 'therm_model.py'))
tm = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(tm)
import thmx_core  # noqa: E402

# Copied from research/library-record-78.md section 3.
NAME_CASES = [
    ('fromx:sample-sill:Wood (Pine, Spruce, Fir)', 'Wood (Pine, Spruce, Fir)'),
    ('Frame Cavity_Cavity_16_Cavity_16', 'Frame Cavity'),
    ('sample-sill:Frame Cavity NFRC 100-2001_Cavity_16_Cavity_16', 'Frame Cavity NFRC 100-2001'),
    ('sample_sill:Butyl Rubber ', 'Butyl Rubber'),
    ('sample-sill:Sample GlzSys (ID:8) U-factor Inside Film', 'Sample GlzSys (ID:8) U-factor Inside Film'),
    ('Int simple 7.7', 'Int simple 7.7'),
    ('  NFRC 100-2010 Exterior ', 'NFRC 100-2010 Exterior'),
    ('Frame Cavity NFRC 100_Cavity_49', 'Frame Cavity NFRC 100'),
]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tm.main([str(a) for a in argv])
    return code, (json.loads(out.getvalue()) if code == 0 else None), err.getvalue()


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def dump(doc, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f)


def ids(root):
    return [e.get('ID') for e in list(root.iter(Q + 'Polygon')) + list(root.iter(Q + 'BCPolygon'))]


class Thmx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_normalise_name(self):
        for raw, want in NAME_CASES:
            self.assertEqual(thmx_core.normalise_name(raw), want, raw)

    def test_default_output_is_thmx(self):
        spec = self.path('wall-78.json')
        shutil.copy(os.path.join(EXAMPLES, 'wall-78.json'), spec)
        code, doc, err = run('build', spec)
        self.assertEqual(code, 0, err)
        self.assertEqual(doc['thmx'], self.path('wall-78.thmx'))
        self.assertEqual(ET.parse(doc['thmx']).getroot().tag, Q + 'THERM-XML')

    def test_extension_picks_format(self):
        code, doc, err = run('build', os.path.join(EXAMPLES, 'wall-3poly.json'), '-o', self.path('w.thmz'))
        self.assertEqual(code, 0, err)
        self.assertIn('entries', doc)  # 8.1 zip
        code, _, err = run('build', os.path.join(EXAMPLES, 'wall-3poly.json'), '-o', self.path('w.thmx'))
        self.assertEqual(code, 1)
        self.assertIn('simplified', err)  # 7.8 path: comprehensive automatic BCs need a thmx record

    def test_wall_build(self):
        out = self.path('mdl_wall.thmx')
        code, doc, err = run('build', os.path.join(EXAMPLES, 'wall-78.json'), '-o', out)
        self.assertEqual(code, 0, err)
        root = ET.parse(out).getroot()
        self.assertEqual(len(ids(root)), len(set(ids(root))))
        self.assertEqual(doc['counts'], {'polygons': 1, 'boundaries': 4, 'frame_cavities': 0})
        poly = root.find('%sPolygons/%sPolygon' % (Q, Q))
        self.assertEqual((poly.get('units'), poly.get('Material')), ('mm', 'Wood k0.14'))
        self.assertEqual([p.get('x') for p in poly], ['0.000000', '100.000000', '100.000000', '0.000000'])
        mat = root.find('%sMaterials/%sMaterial' % (Q, Q))
        self.assertEqual(list(mat.attrib)[:4], ['Name', 'Index', 'Type', 'Conductivity'])
        self.assertEqual(mat.get('Conductivity'), '0.140000')
        bcs = {b.get('Name'): b for b in root.iter(Q + 'BoundaryCondition')}
        self.assertEqual(bcs['Int simple 7.7'].get('H'), '7.700000')
        self.assertEqual(bcs['Adiabatic'].get('FluxFlag'), '1')
        tagged = [b for b in root.iter(Q + 'BCPolygon') if b.get('UFactorTag') == 'Frame']
        self.assertEqual([b.get('BC') for b in tagged], ['Int simple 7.7'])
        self.assertEqual(run('inspect', out)[1]['issues'], [])

    def test_period_in_output_refused(self):
        code, _, err = run('build', os.path.join(EXAMPLES, 'wall-78.json'), '-o', self.path('wall.v2.thmx'))
        self.assertEqual(code, 1)
        self.assertIn('period', err)

    def test_frame_cavities(self):
        out = self.path('frame.thmx')
        code, doc, err = run('build', os.path.join(EXAMPLES, 'simple-frame.json'), '-o', out)
        self.assertEqual(code, 0, err)
        root = ET.parse(out).getroot()
        self.assertEqual(len(ids(root)), len(set(ids(root))))
        cav = [m for m in root.iter(Q + 'Material') if m.get('Type') == '1']
        self.assertEqual([m.get('CavityModel') for m in cav], ['4'])
        surf = [b for b in root.iter(Q + 'BCPolygon') if b.get('BC') == 'Frame Cavity Surface']
        self.assertEqual(sorted({b.get('EnclosureID') for b in surf}), ['1', '2', '3'])
        self.assertEqual(doc['counts']['frame_cavities'], 3)
        self.assertEqual(doc.get('warnings', []), [])  # the example is quiet on 7.8
        spec = load(os.path.join(EXAMPLES, 'simple-frame.json'))
        spec.update({'gravity': 'Down', 'u_value_basis': 'Y-projected', 'mesh': {'type': 'simmetrix'}})
        noisy = self.path('noisy.json')
        dump(spec, noisy)
        code, doc, err = run('build', noisy, '-o', self.path('noisy.thmx'))
        self.assertEqual(code, 0, err)
        self.assertEqual(doc['warnings'], ['8.1-only settings ignored on THERM 7.8 (QuadTree mesher, nothing '
                                           'stored): mesh.type simmetrix, gravity, u_value_basis'])
        spec = load(os.path.join(EXAMPLES, 'simple-frame.json'))
        spec['materials'][3]['cavity_standard'] = 'CEN'
        bad = self.path('cen.json')
        dump(spec, bad)
        code, _, err = run('build', bad, '-o', self.path('cen.thmx'))
        self.assertEqual(code, 1)
        self.assertIn('CavityModel 4', err)

    def test_fixture_records_written_inline(self):
        wood = 'Wood (Pine, Spruce, Fir)'
        spec = {'units': 'mm', 'polygons': [
            {'id': 'l', 'material': wood, 'points': [[0, 0], [20, 0], [20, 60], [0, 60]]},
            {'id': 'r', 'material': wood, 'points': [[40, 0], [60, 0], [60, 60], [40, 60]]},
            {'id': 'b', 'material': wood, 'points': [[20, 0], [40, 0], [40, 20], [20, 20]]},
            {'id': 't', 'material': wood, 'points': [[20, 40], [40, 40], [40, 60], [20, 60]]},
            {'id': 'c', 'material': 'Frame Cavity NFRC 100-2001', 'points': [[20, 20], [40, 20], [40, 40], [20, 40]]}],
            'boundaries': [{'start': [0, 0], 'end': [0, 60], 'bc': 'NFRC 100-2010 Exterior'},
                           {'start': [60, 0], 'end': [60, 60], 'bc': 'Sample GlzSys (ID:8) U-factor Inside Film',
                            'tag': 'Frame'}]}
        sp = self.path('fx.json')
        dump(spec, sp)
        out = self.path('fx.thmx')
        code, doc, err = run('build', sp, '-o', out, '--library', FIXTURE)
        self.assertEqual(code, 0, err)
        root = ET.parse(out).getroot()
        fx = load(FIXTURE)
        want = {r['name']: r['thmx'] for r in fx['materials'] + fx['boundary_conditions']}
        for el in list(root.iter(Q + 'Material')) + list(root.iter(Q + 'BoundaryCondition')):
            if el.get('Name') in want:
                exp = dict(want[el.get('Name')])
                if el.tag == Q + 'Material':
                    exp['Index'] = el.get('Index')
                self.assertEqual(dict(el.attrib), exp, el.get('Name'))
        self.assertEqual(root.find('.//%sBoundaryCondition[@Name="NFRC 100-2010 Exterior"]' % Q).get('Hr'),
                         '-431602080.000000')
        self.assertEqual(len(ids(root)), len(set(ids(root))))

    def test_patch_set_record_r1woodx2(self):
        out = self.path('mdl_r1woodx2.thmx')
        code, doc, err = run('patch', SILL78, '-o', out, '--set-record', 'Wood (Pine, Spruce, Fir)',
                             'Conductivity', '0.28')
        self.assertEqual(code, 0, err)
        before, after = ET.parse(SILL78).getroot(), ET.parse(out).getroot()
        self.assertIsNone(after.find(Q + 'Results'))
        b = [dict(m.attrib) for m in before.iter(Q + 'Material')]
        a = [dict(m.attrib) for m in after.iter(Q + 'Material')]
        diff = [(x, y) for x, y in zip(b, a) if x != y]
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0][1]['Conductivity'], '0.280000')
        self.assertEqual(diff[0][1]['Name'], 'sample-sill:Wood (Pine, Spruce, Fir)')
        self.assertEqual([dict(p.attrib) for p in before.iter(Q + 'Polygon')],
                         [dict(p.attrib) for p in after.iter(Q + 'Polygon')])
        code, _, err = run('patch', SILL78, '-o', self.path('x.thmx'), '--set-record', 'Wood (Pine, Spruce, Fir)',
                           'Bogus', '1')
        self.assertEqual(code, 1)

    def test_patch_swap_and_set_bc(self):
        wall = self.path('wall.thmx')
        self.assertEqual(run('build', os.path.join(EXAMPLES, 'wall-78.json'), '-o', wall)[0], 0)
        out = self.path('wall_nfrc.thmx')
        code, doc, err = run('patch', wall, '-o', out, '--library', FIXTURE,
                             '--swap-material', 'Wood k0.14', 'Wood (Pine, Spruce, Fir)',
                             '--set-bc', 'Ext simple 26', 'NFRC 100-2010 Exterior', '--retag', 'tag:Frame', 'Wall')
        self.assertEqual(code, 0, err)
        root = ET.parse(out).getroot()
        self.assertEqual([m.get('Name') for m in root.iter(Q + 'Material')], ['Wood (Pine, Spruce, Fir)'])
        names = {b.get('Name') for b in root.iter(Q + 'BoundaryCondition')}
        self.assertIn('NFRC 100-2010 Exterior', names)
        self.assertNotIn('Ext simple 26', names)
        self.assertEqual({b.get('UFactorTag') for b in root.iter(Q + 'BCPolygon')}, {'', 'Wall'})
        self.assertEqual(len(ids(root)), len(set(ids(root))))

    def test_patch_keeps_format_and_default_name(self):
        code, _, err = run('patch', SILL78, '-o', self.path('x.thmz'), '--retag', 'tag:Frame', 'F')
        self.assertEqual(code, 1)
        src = self.path('mdl_sill.thmx')
        shutil.copy(SILL78, src)
        code, doc, err = run('patch', src, '--offset=5,0')
        self.assertEqual(code, 0, err)
        self.assertEqual(doc['thmx'], self.path('mdl_sill_patched.thmx'))

    def test_inspect_sample(self):
        code, doc, err = run('inspect', SILL78)
        self.assertEqual(code, 0, err)
        self.assertEqual(doc['counts'], {'polygons': 18, 'boundaries': 61, 'frame_cavities': 4})
        self.assertTrue(doc['solved'])
        self.assertEqual(set(doc['tags']), {'Frame', 'Edge', 'SHGC Exterior'})
        self.assertTrue(any('period' in i for i in doc['issues']))  # sample-sill_7.8.55 has periods

    def test_inspect_flags_id_collision(self):
        bad = os.path.join(RESEARCH, 'samples', 'therm78', 'wall', 'handmade-wall_BAD-id-collision.thmx')
        code, doc, err = run('inspect', bad)
        self.assertEqual(code, 0, err)
        self.assertTrue(any('used twice' in i for i in doc['issues']))


if __name__ == '__main__':
    unittest.main()
