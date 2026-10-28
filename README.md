# bim-tracer

Turns a manufacturer's **vector section drawing** (a PDF detail of a window, door, curtain wall or any
extruded frame profile) into **profile polygons in millimetres**: aluminium cladding, sash, timber
frame, gaskets, spacers, glass. These are ready to become profile extrusions in the BIM engine.

It is the companion to the BIM engine's drawing import. The engine keeps drawing sets, validation,
the catalogue and placement; this repository holds only the PDF and raster work. It is a separate
repository because it uses [PyMuPDF](https://pymupdf.readthedocs.io), which is AGPL-3.0, so this
code is AGPL-3.0 too and public.

## Status

| Part | State |
|---|---|
| `skill/bim-section-profiles/` | **Working.** An agent skill (Hermes Agent) with `sectrace.py`. It calibrates the scale from the printed dimensions, numbers the regions, traces the profiles and checks them against the engine's profile rules. It then plans a mitred frame (optionally turned into a wall with `yawDeg`) and applies it over MCP. |
| HTTP service (`POST /regions`, `POST /trace`) | **Planned**, the BIM engine's tasks M24-02/03/13. It will be built from `sectrace.py` and published as a container image on GHCR for each tag. |

## The skill in short

```sh
sh skill/bim-section-profiles/scripts/setup.sh          # venv with PyMuPDF, OpenCV, numpy, mcp
T="python skill/bim-section-profiles/scripts/sectrace.py"
$T text drawing.pdf --grep '^\d+$'                      # find the section and its dimensions
$T scale drawing.pdf --clip x0,y0,x1,y1                 # SCALE 1:n from dimensions vs. dimension lines
$T render drawing.pdf --clip … --scale n --job job      # crop at a known scale
$T regions job                                          # numbered regions: table + PNG
$T trace job --spec trace-spec.json                     # regions -> profiles (mm), checked
$T plan job --spec frame.json                           # mitred frame: definitions + placements
$T apply job --project <id> --dry-run-only              # over MCP, dry run first
```

See [`skill/bim-section-profiles/SKILL.md`](skill/bim-section-profiles/SKILL.md) for the full
procedure, and [`references/engine-rules.md`](skill/bim-section-profiles/references/engine-rules.md)
for the engine's profile rules.

The skill was developed on a VELFAC 200 Energy detail sheet; `references/example-v200e/` has the
clip, the region numbers and the expected results for it.

## Drawings and copyright

Manufacturers' drawings are usually copyrighted: the VELFAC sheet, for example, may not be
reproduced without DOVISTA's written consent. **Do not commit drawings, renders or crops of them to
this repository.** The `.gitignore` blocks PDF and PNG files. Tests will use synthetic drawings
made for the purpose.

## Licence

[GNU Affero General Public License v3.0](LICENSE) (`AGPL-3.0-only`). If you run a modified version
as a network service, you must offer its source to the users of that service.
