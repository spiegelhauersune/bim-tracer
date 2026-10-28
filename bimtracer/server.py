# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 The bim-tracer authors. See LICENSE.
"""HTTP service around bimtracer.core. Stateless: every request carries the PDF.

POST /regions and POST /trace with the same pdf, page, clip, scale, pxmm and ink give the same
region ids, so a client can pick regions from /regions and send them to /trace.

  GET  /health
  POST /pages    {pdf}
  POST /text     {pdf, page?, grep?, clip?}
  POST /scale    {pdf, page?, clip?}
  POST /regions  {pdf, page?, clip, scale?, scaleClip?, pxmm?, ink?, minArea?, image?}
  POST /trace    {pdf, page?, clip, scale?, scaleClip?, pxmm?, ink?, spec, image?}

pdf is base64. clip and scaleClip are [x0, y0, x1, y1] in display pt. scale is n of 1:n; without
it the scale is calibrated from the printed dimensions inside scaleClip (default: clip).
"""
import argparse
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, core

MAX_BODY = int(os.environ.get('TRACER_MAX_BODY_BYTES', 30 * 1024 * 1024))  # 25 MB PDF as base64
JOBS = threading.BoundedSemaphore(int(os.environ.get('TRACER_MAX_JOBS', 2)))


class BadRequestError(Exception):
    pass


def _clip(v, name='clip'):
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v)):
        raise BadRequestError(f'{name} must be [x0, y0, x1, y1] in pt')
    return ','.join(str(x) for x in v)


def _pdf(req, work):
    data = req.get('pdf')
    if not isinstance(data, str):
        raise BadRequestError('pdf (base64) is required')
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as e:
        raise BadRequestError('pdf is not valid base64') from e
    if not raw.startswith(b'%PDF'):
        raise BadRequestError('pdf does not look like a PDF')
    path = Path(work) / 'drawing.pdf'
    path.write_bytes(raw)
    return str(path)


def _run(fn, **kw):
    """Calls a core command with argparse-like arguments; returns (result, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        r = fn(argparse.Namespace(**kw))
    return r, out.getvalue()


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode() if Path(path).exists() else None


def _scale(req, pdf, page):
    if req.get('scale') is not None:
        return {'scale': float(req['scale']), 'source': 'request'}
    _, p = core.open_page(pdf, page)
    clip = req.get('scaleClip', req.get('clip'))
    votes, best = core.scale_votes(p, core.parse_clip(_clip(clip, 'scaleClip')) if clip else None)
    if not best:
        raise BadRequestError('no scale could be calibrated from the printed dimensions; pass scale (n of 1:n)'
                         ' or a scaleClip that includes dimension lines')
    return {'scale': float(best['scale']), 'source': 'calibrated', 'agree': best['agree'], 'of': best['of'],
            'votes': votes}


def _render_regions(req, work):
    pdf = _pdf(req, work)
    page = int(req.get('page', 1))
    sc = _scale(req, pdf, page)
    job = str(Path(work) / 'job')
    _run(core.cmd_render, pdf=pdf, page=page, clip=_clip(req.get('clip')), scale=sc['scale'],
         pxmm=float(req.get('pxmm', 16)), job=job)
    _, table = _run(core.cmd_regions, job=job, ink=int(req.get('ink', 128)), min_area=float(req.get('minArea', 0.5)),
                    small=float(req.get('small', 2.0)), hatch_min=int(req.get('hatchMin', 6)))
    return job, sc, table


def handle(path, req):
    with tempfile.TemporaryDirectory(prefix='tracer-') as work:
        if path == '/pages':
            _, text = _run(core.cmd_pages, pdf=_pdf(req, work))
            return {'pages': text.strip().splitlines()}
        if path == '/text':
            pdf = _pdf(req, work)
            _, p = core.open_page(pdf, int(req.get('page', 1)))
            lines = core.text_lines(p)
            if req.get('clip'):
                c = core.parse_clip(_clip(req['clip']))
                lines = [ln for ln in lines if c[0] <= ln['bbox'][0] and ln['bbox'][2] <= c[2]
                         and c[1] <= ln['bbox'][1] and ln['bbox'][3] <= c[3]]
            if req.get('grep'):
                import re
                rx = re.compile(req['grep'])
                lines = [ln for ln in lines if rx.search(ln['text'])]
            return {'lines': lines}
        if path == '/scale':
            pdf = _pdf(req, work)
            _, p = core.open_page(pdf, int(req.get('page', 1)))
            votes, best = core.scale_votes(p, core.parse_clip(_clip(req['clip'])) if req.get('clip') else None)
            return {'votes': votes, 'best': best}
        if path == '/regions':
            job, sc, table = _render_regions(req, work)
            regions = json.loads((Path(job) / 'regions.json').read_text())
            min_area = float(req.get('minArea', 0.5))
            meta = json.loads((Path(job) / 'meta.json').read_text())
            meta.pop('pdf', None)
            return {'scale': sc, 'meta': meta, 'table': table,
                    'regions': [r for r in regions['regions'] if r['area_mm2'] >= min_area],
                    'smallRegionCount': sum(1 for r in regions['regions'] if r['area_mm2'] < min_area),
                    'hatchClusters': regions['hatch_clusters'],
                    'image': _b64(Path(job) / 'regions.png') if req.get('image', True) else None}
        if path == '/trace':
            if not isinstance(req.get('spec'), dict):
                raise BadRequestError('spec (the trace spec object) is required')
            job, sc, _ = _render_regions(req, work)
            spec_path = Path(work) / 'spec.json'
            spec_path.write_text(json.dumps(req['spec']))
            results, report = _run(core.cmd_trace, job=job, spec=str(spec_path))
            meta = json.loads((Path(job) / 'meta.json').read_text())
            meta.pop('pdf', None)
            return {'scale': sc, 'meta': meta, 'report': report, 'parts': results,
                    'ok': not any(p['problems'] for p in results.values()),
                    'image': _b64(Path(job) / 'overlay.png') if req.get('image', True) else None}
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = f'bim-tracer/{__version__}'

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == '/health':
            return self._send(200, {'ok': True, 'version': __version__})
        self._send(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(413 if length > MAX_BODY else 400, {'error': f'body must be 1..{MAX_BODY} bytes'})
        try:
            req = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {'error': 'body is not JSON'})
        if not isinstance(req, dict):
            return self._send(400, {'error': 'body must be a JSON object'})
        if not JOBS.acquire(timeout=float(os.environ.get('TRACER_QUEUE_TIMEOUT_S', 60))):
            return self._send(503, {'error': 'busy, retry later'})
        try:
            body = handle(self.path, req)
            if body is None:
                return self._send(404, {'error': 'not found'})
            self._send(200, body)
        except (BadRequestError, core.TracerError) as e:
            self._send(400, {'error': str(e)})
        except Exception as e:  # report, but never leak internals beyond the message
            print(f'tracer error on {self.path}: {e!r}', file=sys.stderr)
            self._send(500, {'error': 'internal error'})
        finally:
            JOBS.release()

    def log_message(self, fmt, *args):
        print(f'{self.address_string()} {fmt % args}', file=sys.stderr)


def main():
    host = os.environ.get('TRACER_HOST', '0.0.0.0')
    port = int(os.environ.get('TRACER_PORT', 8080))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
