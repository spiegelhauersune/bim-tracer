---
name: bim-section-profiles
description: Turn a manufacturer's vector section drawing (PDF detail of a window, door, curtain wall or any extruded frame profile) into exact BIM-engine profile extrusions - aluminium, sash, timber frame, gaskets, spacers, glass - and place them as a mitred frame of a given size over MCP. Use when the user gives a section/detail PDF (e.g. VELFAC, Rationel, Schüco) and asks for a component "as detailed as the drawing".
---

# Section drawing -> detailed profile frame in the BIM engine

The script `scripts/sectrace.py` does the measuring, tracing, rule checking and the
40+ MCP calls. **You** decide which drawn region is which part, and you check the result.
Never estimate profile coordinates from a picture, never model frame members as
`block`/`linear` rectangles, and never type the place calls by hand - that is where
earlier attempts went wrong.

Paths below: `SK=/opt/data/skills/bim-section-profiles`, `PY=/opt/data/tools/sectrace-venv/bin/python`,
`T="$PY $SK/scripts/sectrace.py"`, a job folder per drawing, e.g. `J=/opt/data/scratch/sectrace/v200e-c`.

## 0. One-time setup
`sh $SK/scripts/setup.sh` (creates the venv with PyMuPDF, OpenCV, numpy, mcp; ~500 MB, 1-3 min).

## 1. Get the PDF and find the section
- A local file: use it. An uploaded drawing set: `get_drawing_set {id}` gives the file ids, then
  `$T fetch --set <setId> --file <fileId> --out $J/drawing.pdf`. Call `start_drawing_interpretation` first.
- `$T pages drawing.pdf` - must show vector paths. A scanned image cannot be traced this way: stop and say so.
- `$T text drawing.pdf --page 1` lists every text line with its box in **display pt** (rotation already
  applied). Find the section's label (e.g. `C: (3014.0818)`) and its printed dimensions (149, 114, 54, 48 ...).
  `$T overview drawing.pdf --out $J/overview.png` draws a 50 pt grid if you can look at images.
- Pick a clip `x0,y0,x1,y1` (pt) around the section only: include all of its profiles, leave out
  dimension lines and other details. A few mm of white margin is fine.

## 2. Scale
`$T scale drawing.pdf --page 1 --clip <clip widened to include its dimension lines>` pairs each
printed dimension with its dimension line and prints `SCALE 1:n`. Use it only when several dimensions
agree. Otherwise use the title block scale and say so. The drawing's own scale is what counts, not
"what looks right".

## 3. Render and number the regions
```
$T render drawing.pdf --page 1 --clip <clip> --scale <n> --job $J
$T regions $J
```
Every white area enclosed by lines becomes a numbered region. You get a table (id, area mm², bbox in
crop mm with y up, `E` = touches the clip edge, neighbours with shared length) and `$J/regions.png`
with the numbers drawn in. The region ids are specific to this render.

## 4. Decide which regions make each part (the judgement step)
A drawn solid is usually an outline with white inside. That white inside is the part's region, and
its enclosed chambers are separate regions that become holes **automatically: do not select them**.
- Outside air: the big `E` region, plus white gaps between parts. Never select these.
- Timber frame: the large region with notches/grooves. Aluminium cladding: a long thin region
  wrapping the outside face, often with a rectangular chamber region inside it.
- Glass panes: tall thin regions (≈ pane thickness, e.g. 4 mm) touching the clip edge. Use one part per
  pane, or one part with `copies` (depth offsets in mm). Mark them `"role": "pane"` and give the
  printed `"thickness"`.
- Spacers: rectangles between the panes with a hole inside. Use one part plus `copies` for the others.
- Gaskets (EPDM): small irregular regions (5-60 mm²) at the glass edges and where parts meet. A gasket
  drawn as two or three regions gets all of them in `regions`.
- **Hatched parts** (composite, steel, hatched aluminium) show up as a cluster of many tiny regions,
  listed under "clusters of small regions". Select them with `"smallIn": [[x0,y0,x1,y1]]` (crop mm)
  and set `"hatched": true`. **Do not exclude small regions inside a hatched part.** They are gaps
  between hatch lines, and excluding them opens the part's chambers to the outside.
- Neighbour lists help when you cannot see the image: a chamber's only neighbour is its part's region,
  and outside air touches many parts and the clip edge.

Write `$J/trace-spec.json`. See `references/example-v200e/trace-spec.json` for a complete one:
```json
{"origin": "auto",
 "parts": {
   "alu":  {"regions": [11], "definition": {"code": "MAKER-ALU-C", "nameDa": "...", "nameEn": "...", "materialCode": "ALU-6063", "reportLabel": "...", "note": "Traced from <drawing>, section C"}},
   "sash": {"hatched": true, "smallIn": [[25.5, 28.5, 81, 40]], "definition": {...}},
   "spacer": {"regions": [22], "copies": [0, 22], "definition": {...}},
   "pane": {"regions": [2], "role": "pane", "thickness": 4, "copies": [0, 22, 44], "definition": {"code": "MAKER-GLAS-4", ...}}
 }}
```
Profile frame: x = depth (0 = the face that will sit at `origin.y` in the world, normally the
exterior), y = towards the opening centre (0 = the frame's outer edge). `"origin": "auto"` puts
(0,0) at the outermost face and frame edge over all frame parts. This fits a sill section drawn with
the exterior on the left and the glass above. For other drawings add `"flipX"`, `"flipY"` or
`"swapXY"`: a head section is drawn with the glass below, so it needs `flipY`.

## 5. Trace and check
`$T trace $J --spec $J/trace-spec.json` prints, for each part, OK/BAD, its extent in profile mm, the
point and hole counts, its area and any warnings, and writes `$J/overlay.png`.
Check every part before going on:
- **Sizes match the printed dimensions within ±0.5 mm** (e.g. overall depth 149, timber 114 × 50,
  aluminium 54 high). If a part is off, its region selection is wrong: fix the spec, don't fudge numbers.
- No `BAD`. Read every warning. "separate piece dropped" means a region is missing from that part.
  "hole dropped" means a wall was thinner than the engine's 0.5 mm minimum, which is acceptable for a
  thin web but should be mentioned to the user.
- If you can see images, look at overlay.png: each colour must cover exactly one drawn part.

## 6. Materials
`list_catalog` first and reuse what exists. Create any missing ones with `create_material`, or list
them under `"materials"` in frame.json. Typical: aluminium 2700 kg/m³, λ 160, A1, hatch `steel`;
EPDM 1200, λ 0.25, E; glass-fibre composite 1900, λ 0.3; PVC 1400, λ 0.17; timber C24. Put
`"meta": {"verify": "..."}` on values that are not from the manufacturer.

## 7. Plan the frame and apply it
`$J/frame.json`:
```json
{"width": 1220, "height": 450, "parent": "B1", "origin": {"x": 6200, "y": 0, "z": 1000},
 "slugPrefix": "v200e-122x45", "group": "Velfac 200 Energy vindue 122x45 cm"}
```
- `width`/`height` is the **outer frame size**.
- `origin` is the outer bottom-left corner on the exterior face, in the parent's frame. The window
  plane is the parent's XZ plane, and depth runs along +Y.
- Before choosing the origin, check the free space with `get_model_context` so the window doesn't
  collide with other parts.
- `parent` must already exist. A new project has no building, so create one first, e.g.
  `place_building {footprint: [{x:0,y:0},{x:10000,y:0},{x:10000,y:10000},{x:0,y:10000}], position: {x:0,y:0,z:0}, rotationDeg: 0, zMode: "explicit"}`,
  which gives `B1`. New buildings are locked by default, which is fine for placing parts.
- The script reads the key from `/opt/data/.env` (`MCP_BIM_API_KEY`). **Never put the key on a
  command line or in a file.** Use the script or your own MCP tools, never `curl` to the MCP URL.
  The script keeps to 10 calls per second; the server limit is 20 per second per key.

Then:
```
$T plan  $J --spec $J/frame.json
$T apply $J --project <projectId> --dry-run-only
$T apply $J --project <projectId>
```
`plan` writes every call into `$J/plan.json`: one definition per profile, the glass sheet, and 4 mitred
members per profile (sill, head and both jambs, 45° cuts at the corners), plus the panes and a group.
`apply` does the following in order:
1. dry-runs every material and definition;
2. saves them;
3. dry-runs every placement;
4. places the parts;
5. groups them;
6. runs `validate_model`.

It stops at the first error and prints it. `--replace` first deletes parts left by an earlier run with
the same slugs. Re-running with unchanged definitions creates no new revisions.

## 8. Show and report
- Render `render_camera_view` of the group (iso). Also make a section cut:
  `create_section_plane {name, origin: middle of the sill, normal: {x:1,y:0,z:0}, depthMm: 5}`, then
  render with `sectionPlane` and a close perspective camera. Compare it with the drawing.
- Tell the user:
  - what was modelled, and how many parts;
  - which values are traced, with the tolerance from the warnings;
  - which materials are assumptions;
  - what was not modelled (dropped holes, hardware, thin webs under 0.5 mm).
- If the work came from a drawing set, call `submit_drawing_interpretation` with one detail row per
  part. Each row gives the printed value with `drawingValueSource: pdf_text`, the model value, and
  `status: approximated` for traced outlines.

## Changing the window later (colour, material, profile)
Placed parts stay on the catalogue revision they were built with. **A new revision changes nothing
in the model until you upgrade the uses.**
1. Don't repaint a shared material such as `C24` for one window: every definition that uses it gets a
   new revision. Instead, make a new material (e.g. `C24-RAL9010`) and a new revision of the frame
   definition that uses it (`VF200E-KARM-C` with `materialCode: "C24-RAL9010"`).
2. Resend the **whole** record, copied from `get_catalog_element`, and change only what was asked.
   Don't change other fields such as fireClass or lambdaWmK as a side effect.
3. Then, in each project (`open_project` first), run
   `upgrade_catalog_revision {kind: "definition", code: "VF200E-KARM-C", dryRun: true}`, then again
   without `dryRun`. `kind` is required. Check `upgraded.parts` and `skipped`: structures in locked
   buildings are skipped.
4. `get_catalog_element` shows which projects still use older revisions (`olderRevisionsInUse`).
   Tell the user "done" only when that list is empty.

## Engine facts that caused earlier failures
See `references/engine-rules.md`. In short:
- **Blocks:** `block` parts cannot be resized.
- **Profiles:** at most 64 points per ring and 16 holes; every edge at least 0.5 mm; walls between rings
  at least 0.5 mm; no self-touching. The engine shifts points so the minimum is at (0,0).
- **Extrusions:** they run along local Z. Their dims are `{w, d}` = the profile size and `h` = the
  length, and `position` is the local origin, not the minimum corner.
- **Corners:** use mitre cuts in part-local mm, and keep `dot(n,p) <= offset`.
- **Collisions:** parts that overlap by more than 0.5 mm are rejected.
- **Dry runs:** their reply text starts with `[dry run — nothing saved]`.
- **Rotated PDF pages:** text and vector coordinates come in unrotated space. The script converts both
  to display space, so always use the script's coordinates.
