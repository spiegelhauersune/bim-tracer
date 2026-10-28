# BIM-engine rules that matter for profile frames

Source: `packages/core/src/profile/validate.ts`, `packages/schemas/src/profile.ts` and the
MCP tool descriptions. Every value is in mm.

## Profile definition (`create_component_definition`, geometry kind `profile`)
- `outer`: 3-64 points; `holes`: at most 16 rings of 3-64 points each. Don't repeat the first point.
- Every edge is >= 0.5 mm. The script merges points closer than 0.6 mm.
- A ring may not touch or cross itself; only adjacent edges may share a corner.
- Each ring encloses >= 1 mm².
- Holes lie strictly inside the outer ring and don't touch each other.
- Every wall (outer-to-hole and hole-to-hole gap) is >= 0.5 mm. The script drops a hole that breaks
  this and warns.
- Each side of the extent is between 1 and 100000 mm.
- The points are shifted so the minimum x and y are 0. The script shifts them itself and records
  the offset, which is where the profile sits in the section.
- `length {min, max, default}` bounds the extrusion length (`h`).
- Re-creating a code with identical data returns `unchanged: true`. Different data makes a new
  revision, and parts already placed keep their old revision.

## Placing an extrusion (`place_component`)
- `dims {w, d}` must equal the profile extent of the newest revision exactly; `h` is the length.
- The profile lies in the local XY plane and the length runs along local Z. `position` is the local
  origin in the parent's frame, rounded to 0.1 mm.
- `rotation {quat}`: the columns are the images of the local X, Y and Z axes. The frame sides used by
  `plan` (window plane XZ, depth +Y, local Y pointing into the opening):

  | side   | local X | local Y | local Z | start corner        |
  |--------|---------|---------|---------|---------------------|
  | sill   | +Y      | +Z      | +X      | (x0, y0, z0)        |
  | head   | +Y      | -Z      | -X      | (x0+W, y0, z0+H)    |
  | jamb-l | +Y      | +X      | -Z      | (x0, y0, z0+H)      |
  | jamb-r | +Y      | -X      | +Z      | (x0+W, y0, z0)      |

  For a profile at section offset (bx, by): `position = corner + X*bx + Y*by`.
- 45° mitres, in part-local mm with p = (x, y, z) from the minimum corner, where `dot(n,p) <= offset`
  keeps the material: start `n = (0, .7071, -.7071)`, `offset = -by*.7071`; end
  `n = (0, .7071, .7071)`, `offset = (L - by)*.7071`. Every profile is then cut on the same corner
  diagonal.
- Panes are `sheet` definitions, with dims `{w: W - 2e, d: thickness, h: H - 2e}` where `e` is the
  glass edge distance from the frame's outer edge.
- Collisions: overlapping another part by more than 0.5 mm is rejected with `COLLISION`; touching is
  fine. Traced parts share stroke centrelines, so they touch without overlapping.

## MCP session
- Call `open_project {projectId}` first in every session.
- A tool that fails returns an error with JSON `{code, message, hint}`.
- Dry-run replies are prefixed `[dry run — nothing saved]`.
- `group_geometry {name, parent, members[]}` groups parts without moving them.
- `validate_model {scope}` lists conflicts.
