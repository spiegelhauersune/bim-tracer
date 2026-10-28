# SPDX-License-Identifier: AGPL-3.0-only
import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from bimtracer import server


def test_scale_is_calibrated_from_the_printed_dimensions(pdf_b64, scale_clip):
    r = server.handle('/scale', {'pdf': pdf_b64, 'clip': scale_clip})
    assert r['best'] is not None and r['best']['scale'] == 2
    assert {v['text'] for v in r['votes']} >= {'100'}


def test_text_lines_are_in_display_coordinates(pdf_b64):
    r = server.handle('/text', {'pdf': pdf_b64, 'grep': r'^\d+$'})
    assert {ln['text'] for ln in r['lines']} >= {'100', '50'}


def _material_region(regions):
    inner = [x for x in regions if not x['edge']]
    return max(inner, key=lambda x: x['area_mm2'])


def test_regions_find_the_profile_and_its_chamber(pdf_b64, profile_clip):
    r = server.handle('/regions', {'pdf': pdf_b64, 'clip': profile_clip, 'scale': 2})
    material = _material_region(r['regions'])
    # 100 x 50 minus the 40 x 20 chamber, shrunk by half a stroke on each outline
    assert 3000 < material['area_mm2'] < 4200
    assert r['image'] and base64.b64decode(r['image'])[:4] == b'\x89PNG'
    assert 'id   area' in r['table']


def test_trace_gives_the_profile_in_mm_with_its_hole(pdf_b64, profile_clip):
    reg = server.handle('/regions', {'pdf': pdf_b64, 'clip': profile_clip, 'scale': 2, 'image': False})
    rid = _material_region(reg['regions'])['id']
    pane = min((x for x in reg['regions'] if not x['edge'] and 50 < x['area_mm2'] < 200), key=lambda x: x['bbox_mm'][0])
    spec = {'origin': 'auto', 'parts': {
        'frame': {'regions': [rid], 'definition': {'code': 'TEST-FRAME'}},
        'pane': {'regions': [pane['id']], 'role': 'pane', 'thickness': 4, 'definition': {'code': 'TEST-GLASS'}}}}
    r = server.handle('/trace', {'pdf': pdf_b64, 'clip': profile_clip, 'scale': 2, 'spec': spec, 'image': False})
    assert r['ok'], r['report']
    frame = r['parts']['frame']
    assert abs(frame['size']['w'] - 100) <= 0.6 and abs(frame['size']['d'] - 50) <= 0.6
    assert len(frame['rings']) == 2                      # outer + the chamber
    assert frame['offset'] == {'x': 0.0, 'y': 0.0}       # auto origin: outer face, frame edge
    assert abs(r['parts']['pane']['size']['w'] - 4) <= 0.6


def test_scale_is_calibrated_when_not_given(pdf_b64, profile_clip, scale_clip):
    r = server.handle('/regions', {'pdf': pdf_b64, 'clip': profile_clip, 'scaleClip': scale_clip, 'image': False})
    assert r['scale']['source'] == 'calibrated' and r['scale']['scale'] == 2


def test_bad_input_is_a_400_not_a_crash(pdf_b64):
    with pytest.raises(server.BadRequestError):
        server.handle('/regions', {'pdf': 'not base64!!', 'clip': [0, 0, 1, 1], 'scale': 2})
    with pytest.raises(server.BadRequestError):
        server.handle('/regions', {'pdf': pdf_b64, 'clip': 'x', 'scale': 2})


@pytest.fixture()
def http_url():
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_health_and_errors(http_url, pdf_b64, profile_clip):
    with urllib.request.urlopen(http_url + '/health', timeout=5) as r:
        assert r.status == 200 and json.loads(r.read())['ok'] is True
    assert _post(http_url + '/nope', {'pdf': pdf_b64})[0] == 404
    assert _post(http_url + '/regions', {'pdf': pdf_b64})[0] == 400
    code, body = _post(http_url + '/regions', {'pdf': pdf_b64, 'clip': profile_clip, 'scale': 2, 'image': False})
    assert code == 200 and body['regions']
