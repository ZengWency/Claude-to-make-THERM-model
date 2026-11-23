"""Read results from a THERM 7.8 .thmx (stdlib only). Research demo, not skill code.

U-factors per tag and case come from Results/Case/U-factors. Nodal temperatures exist only when
THERM saved the file with ExportMeshToTHMX=1 in its ini. Per-tag surface temperatures are then
computed by matching convection-BC mesh nodes to the tagged BCPolygon segments:
  mesh node (metres) * 1000 + centre of the polygon bounding box (mm) = model coordinate (mm).
That origin rule was checked on 7.8.80 against sample-sill, sample-jamb and Divider (100 % of
convection-BC nodes land on a BCPolygon segment). Tmean is a plain node mean, not length-weighted.

Usage: python read_thmx_results.py solved.thmx
"""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

NS = {'t': 'http://windows.lbl.gov'}


def on_seg(p, a, b, tol=0.05):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    cx, cy = a[0] + t * dx, a[1] + t * dy
    return (p[0] - cx) ** 2 + (p[1] - cy) ** 2 <= tol * tol


def main(path):
    r = ET.parse(path).getroot()
    print('file', path, '|', r.findtext('t:ThermVersion', namespaces=NS), '|', r.findtext('t:CrossSectionType', namespaces=NS))
    pts = [(float(p.get('x')), float(p.get('y'))) for p in r.findall('t:Polygons/t:Polygon/t:Point', NS)]
    centre = ((min(x for x, _ in pts) + max(x for x, _ in pts)) / 2, (min(y for _, y in pts) + max(y for _, y in pts)) / 2)
    segs = []
    for b in r.findall('t:Boundaries/t:BCPolygon', NS):
        q = [(float(e.get('x')), float(e.get('y'))) for e in b.findall('t:Point', NS)]
        if b.get('UFactorTag'):
            segs.append((b.get('UFactorTag'), q[0], q[1]))
    mesh_in = {m.findtext('t:ModelType', namespaces=NS): m for m in r.findall('t:Mesh/t:MeshInput', NS)}
    for case in r.findall('t:Results/t:Case', NS):
        mt = case.findtext('t:ModelType', namespaces=NS)
        print('case', mt)
        for u in case.findall('t:U-factors', NS):
            proj = {p.findtext('t:Length-type', namespaces=NS): (p.find('t:Length', NS).get('value'), p.find('t:U-factor', NS).get('value'))
                    for p in u.findall('t:Projection', NS)}
            L, U = proj.get('Projected Y', ('?', '?'))
            print('  U  %-15s dT=%s  ProjY L=%s mm U=%s W/m2K  (glass plane: U=%s)' % (
                u.findtext('t:Tag', namespaces=NS), u.find('t:DeltaT', NS).get('value'), L, U,
                proj.get('Projected in glass plane', ('', '?'))[1]))
        nodes_out = case.findall('t:MeshResults/t:NodesOutput/t:NodeResults', NS)
        if not nodes_out or mt not in mesh_in:
            print('  no nodal temperatures (save with ExportMeshToTHMX=1)')
            continue
        temps = {n.get('index'): float(n.get('temperature')) for n in nodes_out}
        mi = mesh_in[mt]
        xy = {n.get('index'): (float(n.get('x')) * 1000 + centre[0], float(n.get('y')) * 1000 + centre[1])
              for n in mi.findall('t:Nodes/t:Node', NS)}
        surf = set()
        for c in mi.findall('t:BCInputs/t:ConvectionBCInput', NS):
            surf.update((c.get('node1'), c.get('node2')))
        print('  T  all nodes: n=%d Tmin=%.2f Tmax=%.2f C' % (len(temps), min(temps.values()), max(temps.values())))
        per = defaultdict(set)
        for n in surf:
            for tag, a, b in segs:
                if on_seg(xy[n], a, b):
                    per[tag].add(n)
        for tag in sorted(per):
            ts = [temps[n] for n in per[tag]]
            print('  T  %-15s n=%3d Tmin=%.2f Tmax=%.2f Tmean=%.2f C' % (tag, len(ts), min(ts), max(ts), sum(ts) / len(ts)))


if __name__ == '__main__':
    main(sys.argv[1])
