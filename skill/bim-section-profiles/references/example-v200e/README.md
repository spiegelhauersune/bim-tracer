# Worked example: VELFAC 200 Energy, section C (3014.0818), window 1220 x 450 mm

Drawing: `V200E CDO integrated sidelight ver01.pdf` (one A3 page, rotation 270, vector).

```
$T text drawing.pdf --grep '^C:|^\d+$'          # C: (3014.0818) at 779.9,410.6; dims 149 114 54 50 48
$T scale drawing.pdf --clip 740,440,1030,640    # SCALE 1:2 (4 of 4 dimensions agree)
$T render drawing.pdf --clip 764,482,984,572 --scale 2 --job $J   # 2485x1017 px, 16 px/mm
$T regions $J                                   # 212 regions
$T trace $J --spec trace-spec.json
$T plan  $J --spec frame.json                   # 10 definitions, 43 placements
$T apply $J --project <id> --dry-run-only
```

Region ids for this render (they change with another clip or --pxmm):

| part | regions | how to recognise it |
|---|---|---|
| alu cladding | 11 | long thin region on the outside face; chamber 68 becomes a hole |
| composite sash | smallIn [25.5,28.5,81,40], hatched | ~150 hatch slivers; chambers 69 and 70 become holes |
| glazing bead | 12 + 10 | region with chambers 15, 26; 10 is its thin top lip |
| timber frame | 21 | 4017 mm², notches and a groove underneath |
| outer glazing gasket | 7 | at the outer pane edge, small holes 18, 20 |
| inner glazing gasket | 8 + 9 | at the inner pane edge |
| sash-to-frame seal | 211 | under the sash, hole 212 |
| bead-to-frame seal | 29 + 35 | between bead and timber rebate |
| spacer | 22, copies [0, 22] | rectangle between panes 1 and 2, hole 24 |
| panes | 2, copies [0, 22, 44], thickness 4 | 4 mm wide, touching the clip top |
| air (never select) | 1, 28, 37, 34, 3, 5, ... | `E`, or regions between parts |

Expected trace (profile mm): alu x 0-35.8, y 0-53.9; karm x 35.3-149.2, y 0-49.9 (114 x 50 ✓);
sash x 23.2-76.2, y 25.8-35.4 with 2 holes; overall depth 149.2 (printed 149 ✓); panes at
x 8.3 / 30.3 / 52.3, glass edge 39.0 mm from the frame's outer edge.

The first attempt excluded some small regions from the sash. They were hatch gaps, so the chambers
opened to the outside (0 holes). The fix was to select everything in the box.
