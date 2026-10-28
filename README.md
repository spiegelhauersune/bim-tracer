# bim-tracer

A small HTTP service in a container. It turns a manufacturer's **vector section drawing** (a PDF
detail of a window, door, curtain wall or any extruded frame profile) into **profile polygons in
millimetres**: aluminium cladding, sash, timber frame, gaskets, spacers, glass.

It is the tracer behind the BIM engine's drawing import (tasks M24-02/03/13). The engine keeps the
drawing sets, profile validation, the catalogue, placement and the MCP tools; this service does only
the PDF and raster work. It lives in its own public repository because it uses
[PyMuPDF](https://pymupdf.readthedocs.io), which is AGPL-3.0. This code is therefore AGPL-3.0 too.

## Run

```sh
docker build -t bim-tracer .
docker run --rm -p 127.0.0.1:8080:8080 --read-only --tmpfs /tmp --cap-drop ALL bim-tracer
curl http://127.0.0.1:8080/health     # {"ok": true, "version": "0.1.0"}
```

Tagged releases are published as `ghcr.io/spiegelhauersune/bim-tracer:<version>`.

The service needs no network, no database and no keys: run it with `--network none` or on an
internal network only. It is **stateless**: every request carries the PDF, and nothing is stored.

## API

All requests are `POST` with a JSON body. `pdf` is the file as base64 (up to about 25 MB). `clip`
and `scaleClip` are `[x0, y0, x1, y1]` in **display pt** (the page as shown, rotation applied).
`scale` is `n` of the drawing scale 1:n.

| Endpoint | Body | Returns |
|---|---|---|
| `GET /health` | | `{ok, version}` |
| `POST /pages` | `{pdf}` | pages with size, rotation, text and vector counts |
| `POST /text` | `{pdf, page?, grep?, clip?}` | text lines with their boxes, to find the section and its printed dimensions |
| `POST /scale` | `{pdf, page?, clip?}` | each printed dimension paired with its dimension line, and the agreed scale |
| `POST /regions` | `{pdf, page?, clip, scale? or scaleClip?, pxmm?, ink?, minArea?, image?}` | every enclosed white region of the section: id, area, bbox in mm, edge flag, neighbours; hatch clusters; a text table; a labelled PNG (base64) |
| `POST /trace` | the same as `/regions`, plus `spec` | per part: profile rings in mm (origin at the outer face and frame edge), section offset, size, warnings, problems against the BIM engine's profile rules; an overlay PNG |

`/regions` and `/trace` with the same `pdf`, `page`, `clip`, `scale`, `pxmm` and `ink` give the
**same region ids**. A client (usually an AI agent) looks at the `/regions` table, decides which
regions make which part, and sends that as the `spec`:

```json
{"origin": "auto",
 "parts": {
   "alu":    {"regions": [11]},
   "sash":   {"hatched": true, "smallIn": [[25.5, 28.5, 81, 40]]},
   "karm":   {"regions": [21]},
   "spacer": {"regions": [22], "copies": [0, 22]},
   "pane":   {"regions": [2], "role": "pane", "thickness": 4, "copies": [0, 22, 44]}
 }}
```

- **How regions become parts.** A drawn part is an outline with white inside. Its region is that
  white inside; enclosed chambers become holes automatically. Ink is shared out to the nearest
  region, so neighbouring parts meet on the stroke centre and never overlap.
- **Hatched parts.** Parts drawn with hatching (composites, hatched aluminium) show up as clusters of
  tiny regions. Select them with `smallIn` and `hatched: true`.
- **Re-orienting.** `flipX`, `flipY` and `swapXY` turn a head or jamb section into the profile frame.
- **Error codes.** Bad input gives `400` with a message saying what to fix; a busy service gives
  `503`.

There is also a CLI for the same steps: `python -m bimtracer.core <pages|text|scale|overview|render|regions|trace> --help`.

## Development

```sh
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

The tests draw their own section drawing (`tests/conftest.py`). Dependencies are pinned in
`requirements.txt`. PyMuPDF is pinned to 1.26: PyMuPDF 2.x pulls in onnxruntime and AI layout
modules, which this service doesn't need and which add telemetry.

## Drawings and copyright

Manufacturers' drawings are usually copyrighted: the VELFAC sheet this was developed on, for
example, may not be reproduced without DOVISTA's written consent. **Do not commit drawings, renders
or crops of them to this repository.** The `.gitignore` blocks PDF and PNG files. Tests use
synthetic drawings only.

## Licence

[GNU Affero General Public License v3.0](LICENSE) (`AGPL-3.0-only`). If you run a modified version
as a network service, you must offer its source to the users of that service.
