#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 The bim-tracer authors. See LICENSE.
"""bim-tracer core: turn a manufacturer's vector section drawing (PDF) into profile polygons in mm.

Steps (also available as a CLI, `python -m bimtracer.core <cmd> --help`):
  pages     list pages, size, rotation
  text      text lines with positions (display pt) - find the section and its dimensions
  scale     calibrate the drawing scale from printed dimensions vs. dimension lines
  overview  whole page as PNG with a 50 pt grid (to choose a clip)
  render    render one section (clip in pt) at a known scale into a job folder
  regions   number every white region of the render: table + labelled PNG
  trace     regions -> profile polygons (mm), checked against the BIM engine's profile rules

Coordinates: a job's "crop mm" frame is real millimetres, x to the right, y up, (0,0) at the
bottom-left of the clip. The trace spec moves and flips that into the profile frame: x = depth
(0 = the outer face), y = towards the opening centre (0 = the frame's outer edge).
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

STD_SCALES = [1, 2, 2.5, 4, 5, 10, 20, 25, 50, 100, 200, 500]
PT_PER_MM = 72 / 25.4


class TracerError(Exception):
    """A problem with the input (bad page, clip, spec, ...); the message says what to fix."""


def die(msg):
    raise TracerError(msg)


def load_json(p):
    return json.loads(Path(p).read_text())


def save_json(p, obj):
    Path(p).write_text(json.dumps(obj, indent=1))


# --------------------------------------------------------------------------- PDF helpers
def open_page(pdf, page):
    import pymupdf
    doc = pymupdf.open(pdf)
    if not 1 <= page <= doc.page_count:
        die(f'page {page} not in 1..{doc.page_count}')
    return doc, doc[page - 1]


def parse_clip(s):
    v = [float(t) for t in s.split(',')]
    if len(v) != 4 or v[2] <= v[0] or v[3] <= v[1]:
        die('clip must be x0,y0,x1,y1 in pt with x1>x0 and y1>y0')
    return v


def text_lines(page):
    """Text lines in display coordinates (the page as it is shown, rotation applied)."""
    import pymupdf
    M = page.rotation_matrix   # text comes in unrotated page space, like drawings
    out = []
    d = page.get_text('dict')
    for b in d['blocks']:
        for ln in b.get('lines', []):
            t = ''.join(s['text'] for s in ln['spans']).strip()
            if not t:
                continue
            r = pymupdf.Rect(ln['bbox']) * M
            dv = pymupdf.Point(ln['dir']) * pymupdf.Matrix(M.a, M.b, M.c, M.d, 0, 0)
            out.append({'text': t, 'bbox': [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)],
                        'dir': [round(dv.x, 2), round(dv.y, 2)]})
    return out


def segments(page):
    """Straight segments of all vector paths, in display coordinates."""
    M = page.rotation_matrix
    segs = []
    for dr in page.get_drawings():
        for it in dr['items']:
            if it[0] == 'l':
                a, b = it[1] * M, it[2] * M
                segs.append((a.x, a.y, b.x, b.y))
            elif it[0] == 're':
                r = it[1] * M
                segs += [(r.x0, r.y0, r.x1, r.y0), (r.x1, r.y0, r.x1, r.y1),
                         (r.x1, r.y1, r.x0, r.y1), (r.x0, r.y1, r.x0, r.y0)]
    return segs


# --------------------------------------------------------------------------- pages / text
def cmd_pages(a):
    import pymupdf
    doc = pymupdf.open(a.pdf)
    for i, p in enumerate(doc):
        r = p.rect
        print(f'page {i+1}: {r.width:.0f} x {r.height:.0f} pt (display), rotation {p.rotation}, '
              f'{len(text_lines(p))} text lines, {len(p.get_drawings())} vector paths')


def cmd_text(a):
    _, p = open_page(a.pdf, a.page)
    rx = re.compile(a.grep) if a.grep else None
    clip = parse_clip(a.clip) if a.clip else None
    for ln in text_lines(p):
        if rx and not rx.search(ln['text']):
            continue
        b = ln['bbox']
        if clip and not (clip[0] <= b[0] and b[2] <= clip[2] and clip[1] <= b[1] and b[3] <= clip[3]):
            continue
        print(f"{b[0]:7.1f} {b[1]:7.1f} {b[2]:7.1f} {b[3]:7.1f}  dir={ln['dir'][0]:+.0f},{ln['dir'][1]:+.0f}  {ln['text']}")


# --------------------------------------------------------------------------- scale
def scale_votes(p, clip=None):
    """Pairs printed dimensions with dimension lines. Returns (votes, scale n of 1:n or None)."""
    clip = clip or [-1e9, -1e9, 1e9, 1e9]
    segs = segments(p)
    hz = [(min(x0, x1), max(x0, x1), (y0 + y1) / 2) for x0, y0, x1, y1 in segs if abs(y1 - y0) < 0.3 and abs(x1 - x0) > 1]
    vt = [(min(y0, y1), max(y0, y1), (x0 + x1) / 2) for x0, y0, x1, y1 in segs if abs(x1 - x0) < 0.3 and abs(y1 - y0) > 1]
    votes = []
    for ln in text_lines(p):
        t = ln['text'].replace(',', '.')
        if not re.fullmatch(r'\d+(\.\d+)?', t):
            continue
        val = float(t)
        if val <= 0:
            continue
        x0, y0, x1, y1 = ln['bbox']
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (clip[0] <= cx <= clip[2] and clip[1] <= cy <= clip[3]):
            continue
        horizontal = abs(ln['dir'][0]) > 0.7
        lines, along, across, size = (hz, cx, cy, x1 - x0) if horizontal else (vt, cy, cx, y1 - y0)
        best = None
        # cluster candidate segments by their perpendicular coordinate
        near = sorted([s for s in lines if abs(s[2] - across) < 14], key=lambda s: abs(s[2] - across))
        seen = set()
        for s in near:
            key = round(s[2] * 2)
            if key in seen:
                continue
            seen.add(key)
            row = sorted([q for q in lines if abs(q[2] - s[2]) < 0.4])
            chains, cur = [], None
            for q in row:
                if cur and q[0] - cur[1] <= max(30, size + 12):
                    cur[1] = max(cur[1], q[1])
                else:
                    cur = [q[0], q[1]]
                    chains.append(cur)
            for c0, c1 in chains:
                if c0 - 2 <= along <= c1 + 2 and c1 - c0 > size:
                    best = (c1 - c0, abs(s[2] - across))
                    break
            if best:
                break
        if not best:
            continue
        L = best[0]
        n = PT_PER_MM * val / L          # scale 1:n
        snap = min(STD_SCALES, key=lambda s: abs(s - n) / s)
        votes.append((ln['text'], round(L, 1), round(n, 3), snap if abs(snap - n) / snap < 0.03 else None, ln['bbox']))
    tally = {}
    for v in votes:
        if v[3]:
            tally[v[3]] = tally.get(v[3], 0) + 1
    best = max(tally, key=tally.get) if tally else None
    return [{'text': v[0], 'line_pt': v[1], 'implied': v[2], 'snapped': v[3], 'bbox': v[4]} for v in votes], \
        ({'scale': best, 'agree': tally[best], 'of': len(votes)} if best else None)


def cmd_scale(a):
    _, p = open_page(a.pdf, a.page)
    votes, best = scale_votes(p, parse_clip(a.clip) if a.clip else None)
    if not votes:
        die('no printed dimension could be paired with a dimension line; give --scale yourself (title block)')
    print('text  line_pt  implied_1:n  snapped  text_bbox')
    for v in votes:
        print(f"{v['text']:>5} {v['line_pt']:8.1f} {v['implied']:10.3f}  {str(v['snapped']):>7}  {v['bbox']}")
    if best:
        print(f"\nSCALE 1:{best['scale']:g}  ({best['agree']} of {best['of']} dimensions agree)")
    else:
        print('\nno standard scale fits within 3%: check the votes, or the drawing is not to scale')
    return {'votes': votes, 'best': best}


# --------------------------------------------------------------------------- overview / render
def cmd_overview(a):
    import pymupdf
    _, p = open_page(a.pdf, a.page)
    doc = pymupdf.open()
    W, H = p.rect.width, p.rect.height
    pg = doc.new_page(width=W, height=H)
    pg.show_pdf_page(pg.rect, p.parent, p.number)
    sh = pg.new_shape()
    for x in range(0, int(W) + 1, 50):
        sh.draw_line((x, 0), (x, H))
    for y in range(0, int(H) + 1, 50):
        sh.draw_line((0, y), (W, y))
    sh.finish(color=(1, 0, 0), width=0.3, stroke_opacity=0.5)
    sh.commit()
    for x in range(0, int(W) + 1, 100):
        for y in range(0, int(H) + 1, 100):
            pg.insert_text((x + 1, y + 7), f'{x},{y}', fontsize=6, color=(1, 0, 0))
    pg.get_pixmap(dpi=a.dpi).save(a.out)
    print(f'wrote {a.out} ({W:.0f} x {H:.0f} pt, grid 50 pt, labels x,y every 100 pt)')


def cmd_render(a):
    import pymupdf
    _, p = open_page(a.pdf, a.page)
    clip = parse_clip(a.clip)
    dpi = int(round(a.pxmm * 25.4 * a.scale))
    if dpi > 2400:
        die(f'{dpi:.0f} dpi is too much; lower --pxmm')
    job = Path(a.job)
    job.mkdir(parents=True, exist_ok=True)
    pix = p.get_pixmap(clip=pymupdf.Rect(*clip), dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    pix.save(job / 'crop.png')
    pxmm = pix.width / ((clip[2] - clip[0]) / PT_PER_MM * a.scale)
    meta = {'pdf': str(Path(a.pdf).resolve()), 'page': a.page, 'clip_pt': clip, 'scale': a.scale,
            'pxmm': pxmm, 'size_px': [pix.width, pix.height],
            'size_mm': [pix.width / pxmm, pix.height / pxmm]}
    save_json(job / 'meta.json', meta)
    print(f"wrote {job/'crop.png'}: {pix.width}x{pix.height} px, {pxmm:.2f} px per real mm, "
          f"crop {meta['size_mm'][0]:.1f} x {meta['size_mm'][1]:.1f} mm at 1:{a.scale:g}")


# --------------------------------------------------------------------------- regions
def load_job(job):
    import cv2
    job = Path(job)
    meta = load_json(job / 'meta.json')
    img = cv2.imread(str(job / 'crop.png'), cv2.IMREAD_GRAYSCALE)
    return job, meta, img


def px2mm(meta, x, y):
    return x / meta['pxmm'], meta['size_px'][1] / meta['pxmm'] - y / meta['pxmm']


def mm2px(meta, x, y):
    return x * meta['pxmm'], (meta['size_mm'][1] - y) * meta['pxmm']


def voronoi(meta, labels, ink, max_mm):
    """Give every ink pixel within max_mm of a region the id of its nearest region."""
    import cv2
    import numpy as np
    dist, lab_px = cv2.distanceTransformWithLabels(ink.astype(np.uint8), cv2.DIST_L2, 5,
                                                   labelType=cv2.DIST_LABEL_PIXEL)
    zero_idx = np.flatnonzero(ink.ravel() == 0)          # label k <-> k-th zero pixel in raster order
    near = labels.ravel()[zero_idx[np.clip(lab_px.ravel() - 1, 0, len(zero_idx) - 1)]].reshape(labels.shape)
    vor = np.where(ink == 0, labels, np.where(dist <= max_mm * meta['pxmm'], near, 0))
    return vor, dist


def cmd_regions(a):
    import cv2
    import numpy as np
    job, meta, img = load_job(a.job)
    ink = (img < a.ink).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats((1 - ink).astype(np.uint8), connectivity=4)
    vor, _ = voronoi(meta, labels, ink, 2.0)
    np.save(job / 'labels.npy', labels.astype(np.int32))
    np.save(job / 'vor.npy', vor.astype(np.int32))
    # adjacency: neighbouring pixels of the ink partition with different owners
    pairs = []
    for A, B in ((vor[:, :-1], vor[:, 1:]), (vor[:-1, :], vor[1:, :])):
        m = (A > 0) & (B > 0) & (A != B)
        pairs.append(np.stack([np.minimum(A[m], B[m]), np.maximum(A[m], B[m])], 1))
    pairs = np.concatenate(pairs) if pairs else np.zeros((0, 2), int)
    up, cnt = np.unique(pairs, axis=0, return_counts=True) if len(pairs) else (np.zeros((0, 2), int), [])
    nb = {}
    for (p, q), c in zip(up.tolist(), list(cnt), strict=True):
        L = c / meta['pxmm']
        nb.setdefault(p, []).append((q, L))
        nb.setdefault(q, []).append((p, L))
    pxmm2 = meta['pxmm'] ** 2
    H, W = labels.shape
    regs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        x0, y1m = px2mm(meta, x, y)
        x1, y0m = px2mm(meta, x + w, y + h)
        regs.append({'id': i, 'area_mm2': round(area / pxmm2, 2),
                     'bbox_mm': [round(x0, 2), round(y0m, 2), round(x1, 2), round(y1m, 2)],
                     'centroid_mm': [round(v, 2) for v in px2mm(meta, *cents[i])],
                     'edge': bool(x == 0 or y == 0 or x + w == W or y + h == H),
                     'neighbours': [[q, round(L, 1)] for q, L in sorted(nb.get(i, []), key=lambda t: -t[1])]})
    # clusters of small regions = probably a hatched (or patterned) solid
    small = {r['id'] for r in regs if r['area_mm2'] < a.small}
    parent = {i: i for i in small}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for r in regs:
        if r['id'] in small:
            for q, _ in r['neighbours']:
                if q in small:
                    parent[find(r['id'])] = find(q)
    groups = {}
    for i in small:
        groups.setdefault(find(i), []).append(i)
    byid = {r['id']: r for r in regs}
    clusters = []
    for g in groups.values():
        if len(g) < a.hatch_min:
            continue
        bb = [min(byid[i]['bbox_mm'][0] for i in g), min(byid[i]['bbox_mm'][1] for i in g),
              max(byid[i]['bbox_mm'][2] for i in g), max(byid[i]['bbox_mm'][3] for i in g)]
        clusters.append({'regions': len(g), 'bbox_mm': [round(v, 2) for v in bb],
                         'area_mm2': round(sum(byid[i]['area_mm2'] for i in g), 1)})
    save_json(job / 'regions.json', {'regions': regs, 'hatch_clusters': clusters})
    # labelled image
    big = [r for r in regs if r['area_mm2'] >= a.min_area and not r['edge']]
    rng = np.random.default_rng(7)
    colours = rng.integers(90, 255, size=(n, 3), dtype=np.uint8)
    out = np.full((H, W, 3), 255, np.uint8)
    show = np.zeros(n, bool)
    for r in big:
        show[r['id']] = True
    mask = show[labels]
    out[mask] = colours[labels[mask]]
    out[ink == 1] = (0, 0, 0)
    scale = 1600 / max(W, H)
    out = cv2.resize(out, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    fs = 0.4 + 0.25 * scale
    for r in big:
        cx, cy = mm2px(meta, *r['centroid_mm'])
        txt = str(r['id'])
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
        p = (int(cx * scale - tw / 2), int(cy * scale + th / 2))
        cv2.putText(out, txt, p, cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(out, txt, p, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(job / 'regions.png'), out)
    print(f"{n-1} white regions; table of those >= {a.min_area} mm2 (E = touches the clip edge, i.e. outside air or cut off).")
    print(f"Coordinates are crop mm (real size, y up). Wrote {job/'regions.json'} and {job/'regions.png'}.\n")
    print('  id   area   x0-x1           y0-y1          E  neighbours (id:shared mm)')
    for r in sorted(big + [r for r in regs if r['edge'] and r['area_mm2'] >= a.min_area], key=lambda r: -r['area_mm2']):
        b = r['bbox_mm']
        nbs = ' '.join(f'{q}:{L:g}' for q, L in r['neighbours'][:8] if byid.get(q, {}).get('area_mm2', 0) >= a.min_area or q in small)
        print(f"{r['id']:4d} {r['area_mm2']:7.1f}  {b[0]:6.1f}-{b[2]:6.1f}  {b[1]:6.1f}-{b[3]:6.1f}  {'E' if r['edge'] else ' '}  {nbs}")
    if clusters:
        print('\nclusters of small regions (likely hatched solids; select them with "smallIn"):')
        for c in clusters:
            b = c['bbox_mm']
            print(f"  {c['regions']:4d} regions, {c['area_mm2']:6.1f} mm2, box x {b[0]:.1f}-{b[2]:.1f} y {b[1]:.1f}-{b[3]:.1f}")


# --------------------------------------------------------------------------- polygon helpers
def ring_area(r):
    return 0.5 * sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r)))


def seg_dist(p, q, a, b):
    def pd(pt, s0, s1):
        dx, dy = s1[0] - s0[0], s1[1] - s0[1]
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((pt[0] - s0[0]) * dx + (pt[1] - s0[1]) * dy) / L2))
        return math.hypot(pt[0] - s0[0] - t * dx, pt[1] - s0[1] - t * dy)
    if seg_cross(p, q, a, b):
        return 0.0
    return min(pd(p, a, b), pd(q, a, b), pd(a, p, q), pd(b, p, q))


def seg_cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def point_in(pt, ring):
    x, y = pt
    c = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i], ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def merge_short(r, min_edge):
    r = [list(q) for q in r]
    changed = True
    while changed and len(r) > 3:
        changed = False
        for i in range(len(r)):
            a, b = r[i], r[(i + 1) % len(r)]
            if math.dist(a, b) < min_edge:
                def loss(k):
                    p, q, s = r[k - 1], r[k], r[(k + 1) % len(r)]
                    return abs((q[0] - p[0]) * (s[1] - p[1]) - (q[1] - p[1]) * (s[0] - p[0]))
                k = i if loss(i) <= loss((i + 1) % len(r)) else (i + 1) % len(r)
                r.pop(k)
                changed = True
                break
    # drop collinear midpoints
    out = []
    for i in range(len(r)):
        p, q, s = r[i - 1], r[i], r[(i + 1) % len(r)]
        if abs((q[0] - p[0]) * (s[1] - p[1]) - (q[1] - p[1]) * (s[0] - p[0])) > 1e-6:
            out.append(q)
    return out


def ring_problems(rings, min_edge=0.5, min_wall=0.5):
    """The engine's profile rules (packages/core/src/profile/validate.ts), checked locally."""
    probs = []
    for k, r in enumerate(rings):
        name = 'outer' if k == 0 else f'hole {k-1}'
        if len(r) < 3:
            probs.append(f'{name}: fewer than 3 points')
            continue
        if len(r) > 64:
            probs.append(f'{name}: {len(r)} points (max 64)')
        for i in range(len(r)):
            if math.dist(r[i], r[(i + 1) % len(r)]) < min_edge:
                probs.append(f'{name}: edge {i} shorter than {min_edge} mm')
        n = len(r)
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                if seg_dist(r[i], r[(i + 1) % n], r[j], r[(j + 1) % n]) < 1e-6:
                    probs.append(f'{name}: edges {i} and {j} touch or cross')
        if abs(ring_area(r)) < 1.0:
            probs.append(f'{name}: area below 1 mm2')
    if len(rings) - 1 > 16:
        probs.append(f'{len(rings)-1} holes (max 16)')
    for k in range(1, len(rings)):
        if not point_in(rings[k][0], rings[0]):
            probs.append(f'hole {k-1} is not inside the outer ring')
    for j in range(len(rings)):
        for k in range(max(j + 1, 1), len(rings)):
            a, b = rings[j], rings[k]
            d = min(seg_dist(a[i], a[(i + 1) % len(a)], b[m], b[(m + 1) % len(b)])
                    for i in range(len(a)) for m in range(len(b)))
            if d < min_wall:
                probs.append(f"{'outer' if j == 0 else f'hole {j-1}'} and hole {k-1} are {d:.2f} mm apart (min {min_wall})")
    return probs


def simplify(contour_px, meta, maxn, eps0, min_edge):
    import cv2
    eps = eps0
    while True:
        ap = cv2.approxPolyDP(contour_px, eps * meta['pxmm'], True).reshape(-1, 2)
        ring = [list(px2mm(meta, float(x), float(y))) for x, y in ap]
        ring = merge_short(ring, min_edge)
        if len(ring) <= maxn:
            return ring, eps
        eps *= 1.2


# --------------------------------------------------------------------------- trace
def select_regions(spec_part, regs, part_name):
    ids = set(spec_part.get('regions', []))
    for box in spec_part.get('smallIn', []):     # [x0,y0,x1,y1] crop mm: all small regions with centroid inside
        lim = spec_part.get('smallMaxArea', 6.0)
        for r in regs:
            cx, cy = r['centroid_mm']
            if r['area_mm2'] < lim and box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
                ids.add(r['id'])
    ids -= set(spec_part.get('exclude', []))
    if not ids and 'polygon' not in spec_part:
        die(f'part {part_name}: no regions selected')
    return ids


def cmd_trace(a):
    import cv2
    import numpy as np
    job, meta, img = load_job(a.job)
    spec = load_json(a.spec)
    regs = load_json(job / 'regions.json')['regions']
    labels = np.load(job / 'labels.npy')
    ink = (labels == 0)
    vor, dist = voronoi(meta, labels, ink.astype(np.uint8), spec.get('maxInkMm', 1.5))
    auto = spec.get('origin', 'auto') == 'auto'
    ox, oy = (0.0, 0.0) if auto else (spec['origin']['x'], spec['origin']['y'])
    fx = -1 if spec.get('flipX') else 1
    fy = -1 if spec.get('flipY') else 1
    swap = bool(spec.get('swapXY'))

    def to_profile(p):
        x, y = (p[0] - ox) * fx, (p[1] - oy) * fy
        return [y, x] if swap else [x, y]
    # each part's region set; the ink partition (vor) then gives every boundary at the stroke centre
    owners = {}
    parts = spec['parts']
    for name, sp in parts.items():
        owners[name] = select_regions(sp, regs, name)
    taken = {}
    for name, ids in owners.items():
        for i in ids:
            if i in taken:
                die(f'region {i} is in both {taken[i]} and {name}')
            taken[i] = name
    results, overlay, traced = {}, [], []
    min_edge = spec.get('minEdgeMm', 0.6)
    for name, sp in parts.items():
        warn = []
        if 'polygon' in sp:
            outer = [to_profile(p) for p in sp['polygon']]
            rings = [outer]
            overlay.append((name, [sp['polygon']]))
        else:
            ids = np.array(sorted(owners[name]), np.int32)
            mask = np.isin(vor, ids).astype(np.uint8)
            if sp.get('hatched'):
                # hatched/solid part: also take the ink that no neighbour owns. Other parts keep their
                # full ink share; unselected regions (air, chambers) claim only claimMm of ink.
                others = np.array(sorted(i for n2, s2 in owners.items() if n2 != name for i in s2), np.int32)
                claim = spec.get('claimMm', 0.65) * meta['pxmm']
                own_cells = mask.astype(bool)
                other_sel = np.isin(vor, others)
                unsel = (vor > 0) & ~np.isin(vor, ids) & ~other_sel & ((labels > 0) | (dist <= claim))
                free = ink & ~other_sel & ~unsel
                cand = (own_cells | free).astype(np.uint8)
                n_c, lab_c = cv2.connectedComponents(cand, connectivity=4)
                hit = np.unique(lab_c[own_cells])
                mask = np.isin(lab_c, hit[hit > 0]).astype(np.uint8)
            if sp.get('closeMm'):
                k = max(1, int(sp['closeMm'] * meta['pxmm']))
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
            cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
            if not cnts:
                die(f'part {name}: empty mask')
            hier = hier[0]
            areas = [cv2.contourArea(c) / meta['pxmm'] ** 2 for c in cnts]
            outers = [i for i in range(len(cnts)) if hier[i][3] < 0]
            main = max(outers, key=lambda i: areas[i])
            for i in outers:
                if i != main and areas[i] > 0.3:
                    warn.append(f'separate piece of {areas[i]:.1f} mm2 dropped (add the gap region, or use closeMm)')
            holes = [i for i in range(len(cnts)) if hier[i][3] == main and areas[i] >= spec.get('minHoleMm2', 0.4)]
            holes.sort(key=lambda i: -areas[i])
            if len(holes) > 16:
                warn.append(f'{len(holes)-16} smallest holes dropped (max 16)')
                holes = holes[:16]
            eps0 = sp.get('epsMm', 0.05)
            outer, eps = simplify(cnts[main], meta, 64, eps0, min_edge)
            rings = [outer]
            for i in holes:
                h, _ = simplify(cnts[i], meta, 64, eps0, min_edge)
                if len(h) >= 3 and abs(ring_area(h)) >= 1.0:
                    rings.append(h)
                else:
                    warn.append(f'hole of {areas[i]:.1f} mm2 too small after simplifying, dropped')
            if eps > 0.3:
                warn.append(f'outer simplified with {eps:.2f} mm tolerance to fit 64 points')
            overlay.append((name, rings))
            rings = [[to_profile(p) for p in r] for r in rings]
        traced.append((name, sp, rings, warn))
    # "auto" origin: profile (0,0) = the outermost depth face and frame edge over all frame parts
    gx = gy = 0.0
    if auto:
        frame = [r for n, sp, r, _ in traced if sp.get('role', 'frame') != 'pane'] or [r for _, _, r, _ in traced]
        gx = min(p[0] for r in frame for p in r[0])
        gy = min(p[1] for r in frame for p in r[0])
    for name, sp, rings, warn in traced:
        rings = [[[p[0] - gx, p[1] - gy] for p in r] for r in rings]
        # drop holes that break the wall rule (0.5 mm) rather than fail the whole part
        keep = [rings[0]]
        for h in rings[1:]:
            pr = ring_problems([rings[0], h], 0.0, 0.55)
            if pr and not ring_problems([rings[0]], 0.0, 0.55):
                warn.append(f'hole of {abs(ring_area(h)):.1f} mm2 dropped: {pr[0]}')
                continue
            pr = [q for k in keep[1:] for q in ring_problems([[[-1e6, -1e6], [1e6, -1e6], [1e6, 1e6], [-1e6, 1e6]], k, h], 0.0, 0.55)]
            if pr:
                warn.append(f'hole of {abs(ring_area(h)):.1f} mm2 dropped: {pr[0]} (walls under 0.5 mm are not allowed)')
                continue
            keep.append(h)
        rings = keep
        bx = min(p[0] for p in rings[0])
        by = min(p[1] for p in rings[0])
        rings = [[[round(p[0] - bx, 1), round(p[1] - by, 1)] for p in r] for r in rings]
        rings = [merge_short(r, min_edge) for r in rings]
        sx = min(p[0] for p in rings[0])
        sy = min(p[1] for p in rings[0])
        rings = [[[round(p[0] - sx, 1), round(p[1] - sy, 1)] for p in r] for r in rings]
        bx, by = round(bx + sx, 2), round(by + sy, 2)
        probs = ring_problems(rings)
        w = max(p[0] for p in rings[0])
        d = max(p[1] for p in rings[0])
        area = abs(ring_area(rings[0])) - sum(abs(ring_area(h)) for h in rings[1:])
        results[name] = {'offset': {'x': bx, 'y': by}, 'size': {'w': w, 'd': d}, 'area_mm2': round(area, 1),
                         'rings': rings, 'problems': probs, 'warnings': warn,
                         'definition': sp.get('definition'), 'role': sp.get('role', 'frame'),
                         'copies': sp.get('copies', [0]), 'thickness': sp.get('thickness')}
        flag = 'OK ' if not probs else 'BAD'
        print(f"{flag} {name:14s} profile x {bx:7.2f}..{bx+w:7.2f}  y {by:7.2f}..{by+d:7.2f}  "
              f"{len(rings[0])} pts, {len(rings)-1} holes, {area:7.1f} mm2")
        for m in warn:
            print('      warning: ' + m)
        for m in probs:
            print('      PROBLEM: ' + m)
    save_json(job / 'profiles.json', {'spec': spec, 'parts': results})
    # overlay on the crop
    base = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    H, W = img.shape
    s = 1600 / max(W, H)
    base = cv2.resize(base, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA)
    lay = base.copy()
    pal = [(60, 60, 230), (60, 170, 60), (230, 120, 40), (40, 160, 230), (200, 60, 200),
           (40, 200, 200), (120, 80, 30), (150, 150, 150), (30, 30, 140), (0, 120, 255)]
    for k, (name, rings) in enumerate(overlay):
        c = pal[k % len(pal)]
        pts = [np.array([[mm2px(meta, *p)[0] * s, mm2px(meta, *p)[1] * s] for p in r], np.int32) for r in rings]
        cv2.fillPoly(lay, pts, c)
        cv2.polylines(base, pts, True, c, 2, cv2.LINE_AA)
        x, y = pts[0][0]
        cv2.putText(base, name, (int(x), int(y) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2, cv2.LINE_AA)
    cv2.addWeighted(lay, 0.35, base, 0.65, 0, base)
    cv2.imwrite(str(job / 'overlay.png'), base)
    print(f"\nwrote {job/'profiles.json'} and {job/'overlay.png'}")
    return results


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('pages'); p.add_argument('pdf'); p.set_defaults(f=cmd_pages)
    p = sub.add_parser('text'); p.add_argument('pdf'); p.add_argument('--page', type=int, default=1)
    p.add_argument('--grep'); p.add_argument('--clip', help='x0,y0,x1,y1 pt'); p.set_defaults(f=cmd_text)
    p = sub.add_parser('scale'); p.add_argument('pdf'); p.add_argument('--page', type=int, default=1)
    p.add_argument('--clip', help='only dimensions inside x0,y0,x1,y1 pt'); p.set_defaults(f=cmd_scale)
    p = sub.add_parser('overview'); p.add_argument('pdf'); p.add_argument('--page', type=int, default=1)
    p.add_argument('--dpi', type=int, default=110); p.add_argument('--out', default='overview.png'); p.set_defaults(f=cmd_overview)
    p = sub.add_parser('render'); p.add_argument('pdf'); p.add_argument('--page', type=int, default=1)
    p.add_argument('--clip', required=True, help='x0,y0,x1,y1 pt (display coordinates, as text/overview show)')
    p.add_argument('--scale', type=float, required=True, help='n of the drawing scale 1:n')
    p.add_argument('--pxmm', type=float, default=16, help='pixels per real mm (default 16)')
    p.add_argument('--job', required=True); p.set_defaults(f=cmd_render)
    p = sub.add_parser('regions'); p.add_argument('job')
    p.add_argument('--ink', type=int, default=128, help='grey level below which a pixel is ink (0-255)')
    p.add_argument('--min-area', type=float, default=0.5, help='mm2; smaller regions are not listed')
    p.add_argument('--small', type=float, default=2.0, help='mm2; regions below this can form hatch clusters')
    p.add_argument('--hatch-min', type=int, default=6); p.set_defaults(f=cmd_regions)
    p = sub.add_parser('trace'); p.add_argument('job'); p.add_argument('--spec', required=True); p.set_defaults(f=cmd_trace)
    a = ap.parse_args()
    try:
        r = a.f(a)
    except TracerError as e:
        print('ERROR: ' + str(e), file=sys.stderr)
        sys.exit(2)
    if a.cmd == 'trace' and r and any(p['problems'] for p in r.values()):
        sys.exit(1)


if __name__ == '__main__':
    main()
