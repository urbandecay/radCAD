# Drawing Bug Log

---

## Bug: Vertex dots invisible in POST_VIEW draw callback

**Date:** 2026-03-18
**Affected tool:** Arc 2-Point (stage 1 diameter drag), but affects all tools using `draw_points`

### What was wrong

`draw_points` was rewritten to draw flat pixel squares on screen (using `UNIFORM_COLOR` shader with 2D pixel coordinates) to work around a Blender 5.0 EEVEE-Next bug with the old 3D cube approach.

The problem: the main draw callback (`draw_cb_3d`) is registered as `POST_VIEW`, which means it runs in **3D world space**. When `draw_points` projected a 3D point to screen coordinates (e.g. pixel 500, 300) and then drew at those coordinates, Blender placed the squares 500 units to the right and 300 units up in the actual 3D scene — completely off screen, invisible.

The diameter **line** still worked because `draw_line` uses the `POLYLINE` shader with actual 3D world-space coordinates, which is correct for a POST_VIEW context.

### Why this was hard to debug

The dots were being drawn "somewhere" (you could see them in other tools), but not in the POST_VIEW callback for this specific tool. It looked like `draw_points` was broken, so naturally you'd rewrite the drawing function. But the real issue wasn't the function — it was the coordinate system mismatch. The dots were being drawn at (500, 300, 0) in 3D world space instead of pixel (500, 300) on screen.

### What was fixed

Rewrote `draw_points` to use the `POLYLINE` shader in 3D world space (same as `draw_line` and `draw_crosshair`). Each dot is drawn as a small 3-axis cross centered on the point, sized using `world_radius_for_pixel_size` to stay consistent across zoom levels.

### Rule of thumb

In `draw_cb_3d` (POST_VIEW), **all geometry must use 3D world-space coordinates**. The `POLYLINE` shader handles this correctly. The `UNIFORM_COLOR` shader with 2D screen coordinates only works in `draw_hud_2d` (POST_PIXEL).

| Callback | Context | Use |
|---|---|---|
| `draw_cb_3d` → POST_VIEW | 3D world space | `POLYLINE` shader, `apply_view_bias` for lift |
| `draw_hud_2d` → POST_PIXEL | Screen pixels | `UNIFORM_COLOR` shader, pixel coordinates |

---

## Bug: Rectangle tools collapse to a line when dragging near an axis

**Date:** 2026-03-18
**Affected tools:** RectangleTool_CenterCorner, RectangleTool_CornerCorner

### What was wrong

When dragging the rectangle's second corner, if you dragged mostly along one direction (e.g., mostly left-right), the rectangle would collapse into a flat line instead of staying a proper 2D rectangle. The shape would lose one dimension.

### Why it happened

I'd added axis snapping to the rectangle tools (copied from the polygon tools, which use it correctly). The axis snapping works like this: when you drag mostly along the X direction, it "snaps" your target point to lie exactly on the world X axis. This gives:
- X component (width) = some value ✓
- Y component (height) = **zero** ✗

For a polygon that's fine — radius = distance from center to that point, which is non-zero. But for a rectangle, you need BOTH width AND height. When height becomes zero, the rectangle flattens to a line.

**The root issue:** Axis snapping was designed for tools that only need one point to define their shape (polygons, circles, arcs). For rectangles that need two dimensions defined in a single drag, locking to a world axis breaks the shape.

### How I found it

At first I thought the problem was self-snapping (the rectangle snapping back to its starting point). I added guards to prevent that. But the behavior didn't change. Then I dug deeper and traced through the code:

1. When you drag along an axis, `get_axis_snapped_location` returns a point on that axis
2. That point gets projected onto the plane to get the rectangle dimensions
3. When the point is on the X axis, `dy = d_vec.dot(Yp) = 0`
4. Rectangle width exists but height is zero → line

The "self snap" label stuck because it FELT like the rectangle was snapping to itself (collapsing), but the actual culprit was the axis snap locking one dimension to zero.

### What was fixed

Removed axis snapping from `RectangleTool_CenterCorner` stage 1 and `RectangleTool_CornerCorner` stage 1. These tools now use vertex snapping only (snap to mesh geometry), which doesn't have this problem.

**Kept** axis snapping in `RectangleTool_3Point` stages 1 and 2, because:
- Stage 1: you're intentionally defining just one edge direction (snapping to an axis makes sense)
- Stage 2: you're defining height perpendicular to that edge (snapping to an axis makes sense)

### Rule of thumb

**Axis snapping is for single-point tools; avoid it for multi-dimensional tools.** Axis snapping works for tools that define their shape with a single snapped point (polygons, circles, arcs) because the "snap to axis" is just locking one parameter. For tools that need multiple independent dimensions defined at once (like a 2D rectangle in one drag), locking to a world axis forces one dimension to zero. Use vertex snapping instead — it preserves both dimensions.

---

## Feature: Added snapping visual preferences to ellipse tools

**Date:** 2026-03-18
**Commits:** 194431a, dea643d, 735a8b2
**Affected tools:** Ellipse from Radius, Ellipse from Endpoints, Ellipse from Foci

### What was added

Added axis color and overlay color preferences to three ellipse tools, matching the pattern used by arc and circle tools:
- **Ellipse from Radius:** `ellipse_radius_use_axis_colors` + `color_ellipse_radius_overlay`
- **Ellipse from Endpoints:** `ellipse_endpoints_use_axis_colors` + `color_ellipse_endpoints_overlay`
- **Ellipse from Foci:** `ellipse_foci_use_axis_colors` + `color_ellipse_foci_overlay` (foci lines still use foci line color)

### Why

Users wanted visual feedback matching the existing arc/circle tools. When axis colors are ON, guide lines turn red/green/blue when snapped to X/Y/Z. When OFF, they use a dark grey overlay color.

### Note on Ellipse from Foci

The foci guide lines (connecting the two focal points to the cursor) still use the dedicated foci line color (`ellipse_foci_col_foci_lines`), NOT the axis colors. The axis color preferences apply only to the preliminary guide lines shown before placing the foci.

---

## Feature: Darkened overlay colors for axis snapping tools

**Date:** 2026-03-18
**Commit:** 201655f
**Affected tools:** Arc 2PT/3PT, Circle 2PT/3PT, Ellipse Radius/Endpoints/Foci

### What changed

When axis colors are disabled, the fallback overlay color is now darker:
- **Arc/Circle tools:** (0.5, 0.5, 0.5, 0.5) → (0.3, 0.3, 0.3, 0.5) — darker semi-transparent grey
- **Ellipse tools:** (0.2, 0.2, 0.2, 1.0) → (0.1, 0.1, 0.1, 1.0) — darker opaque grey

Darker colors provide better visual distinction from the bright axis colors (red/green/blue).

---

## Feature: Added snapping visual preferences to polygon and rectangle tools

**Date:** 2026-03-18
**Commit:** 792bff9
**Affected tools:** All polygon variants, All rectangle variants

### What was added

Each polygon and rectangle tool now has individual axis color and overlay color preferences:

**Polygon tools:**
- Polygon (Center/Corner)
- Polygon (Center/Tangent)
- Polygon (Corner/Corner)
- Polygon (Edge)

**Rectangle tools:**
- Rectangle (Center/Corner)
- Rectangle (Corner/Corner)
- Rectangle (3 Points)

Each tool has its own `use_axis_colors` toggle and `overlay color` setting, following the established pattern from arc/circle/ellipse tools.

---

## Bug fix: Polygon Edge tool Z-axis snap (vertical hysteresis)

**Date:** 2026-03-18
**Commit:** 3c4bcb4
**Affected tool:** Polygon (Edge)

### What was wrong

The Polygon Edge tool had the vertical/Z-axis detection and plane reorientation logic implemented (lines 605-642), but it wasn't working. When you tried to snap to the Z direction, nothing happened.

### Why it wasn't working

The plane projection at lines 600-603 was stripping the Z component from the snapped target **before** the vertical detection logic ran. So `is_vertical` would always be false because the Z had already been zeroed out.

### Why this was hard to debug

The vertical detection logic was RIGHT THERE in the code (lines 605-642), fully implemented and correct. But it never triggered because it was checking a stripped target. So you'd look at the logic and think "this should work" — and it SHOULD work, just not in this order. It only became obvious when tracing through frame-by-frame and seeing that the Z component was gone by the time the check ran.

### What was fixed

Moved the vertical detection logic to run **before** the plane projection:
1. Get raw snapped target (may have Z component)
2. Run vertical detection and basis recomputation on raw target
3. NOW project onto the (possibly updated) drawing plane

This matches the Circle 2-Point tool behavior and enables proper Z-axis snapping with hysteresis-based plane reorientation.

### Rule of thumb

**For vertical/Z-axis detection, check the raw direction BEFORE projecting to plane.** Once you project onto a horizontal plane (stripping Z), you lose the information about whether the drag was vertical. Always detect vertical from the original snapped direction, then update your basis, then project.

---

## Bug: Polygon tools P key didn't flip normal

**Date:** 2026-03-18
**Affected tools:** Polygon (Center/Corner), Polygon (Center/Tangent), Polygon (Corner/Corner)

### What was wrong

When you pressed P to flip the polygon to perpendicular/upright mode, nothing happened. The P key handler toggled the `is_perpendicular` flag, but the `update()` method never read that flag — so Zp (the drawing plane normal) never changed.

### Why it happened

The P key toggle was added (`self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)`) but there was no corresponding logic in `update()` stage 1 to actually use that flag and recompute the plane.

Meanwhile, **PolygonTool_Edge** already had the full perpendicular+vertical hysteresis logic in its `update()` method, and the circle tools also had this. The other polygon tools were missing it.

### Why this was hard to debug

The P key handler was RIGHT THERE and functional. You could toggle `is_perpendicular` to your heart's content, but nothing in `update()` was reading it. So you'd press P, the flag would change, but visually nothing would happen — it FELT like the P key wasn't working at all. The fix wasn't adding a P key handler (it existed), it was adding the whole perpendicular basis recomputation logic to the update loop.

### What was fixed

Added the full perpendicular plane computation block to `CenterCorner`, `CenterTangent`, and `CornerCorner` `update()` methods:

1. When `is_perpendicular = True`: Compute `new_Zp = drag_dir × ref_normal` (upright plane perpendicular to drag direction)
2. Stabilize the normal relative to view direction (flip if pointing toward camera)
3. Rebuild orthonormal basis: `Zp = new_Zp`, `Xp/Yp` from `Zp`
4. When `is_perpendicular = False`: Restore `Zp = ref_normal` (flat plane)

Also added vertical hysteresis (auto Z-snap) matching circle/edge tools: when drag is nearly vertical (dot > 0.98/0.995), automatically snap the plane perpendicular to the Z axis.

### Rule of thumb

**State flags don't change behavior by themselves — the update loop has to READ them.** Just toggling a flag (like `is_perpendicular`) doesn't do anything if `update()` never checks it. When adding a new mode toggle, make sure `update()` has logic that actually USES that flag to change computations (like recomputing Zp/Xp/Yp).

---

## Bug: Polygon Corner/Corner center jumps when plane flips vertical

**Date:** 2026-03-18
**Affected tool:** Polygon (Corner/Corner)

### What was wrong

When you defined an edge and then pressed P to flip the polygon vertical, the center position would jump around erratically instead of staying anchored to the edge midpoint. The polygon went "off the rails."

### Why it happened

The perpendicular direction from the edge to the polygon center was computed using 2D XY rotation:
```python
perp_dir = Vector((-edge_dir.y, edge_dir.x, 0)).normalized()
```

This assumes the edge is in the XY plane. When the plane flips vertical (Zp points along X or Y axis), the edge is no longer in XY, but the code still computed the perpendicular as if it were. This gave a completely wrong direction.

### Why this was hard to debug

The code looked innocent — it was just a 90-degree rotation. The perpendicular direction is correct for any edge that lies in the XY plane (the floor case). It only breaks when the plane changes orientation, which is a somewhat rare edge case. You had to specifically press P during the edge definition AND watch the center jump erratically to notice something was wrong.

### What was fixed

Changed to compute perpendicular in the actual drawing plane using cross product:
```python
perp_dir = edge_dir.cross(self.Zp).normalized()
```

This is perpendicular to both the edge and the plane normal, ensuring it's always in the plane regardless of plane orientation. Applied to both `update()` and `refresh_preview()`.

### Rule of thumb

**Never hardcode plane assumptions like XY. Use the current basis (Xp, Yp, Zp) for all perpendicular/direction computations.** If you hardcode `Vector((x, y, 0))` thinking "we're always on the floor", you'll break when the basis changes. Always use `vec.cross(self.Zp)` or decompose in the `self.Xp/self.Yp` plane.

---

## Bug: Rectangle tools P key flip collapses to a line

**Date:** 2026-03-18
**Affected tools:** Rectangle (Center/Corner), Rectangle (Corner/Corner)

### What was wrong

When you pressed P to flip a rectangle from flat to perpendicular (vertical), the rectangle would immediately collapse to a thin line. The height dimension became zero.

### Why it happened

**Root cause:** Orientation logic had to run AFTER target resolution, creating a fundamental ordering problem. When P flipped `is_perp = True`:
1. Target was intersected with the OLD plane (floor, Zp = world Z)
2. Result: target was on the floor with z≈0
3. NEW orientation computed Zp as a horizontal axis (vertical plane normal)
4. But target was already a floor point with z=0
5. When dimensions were extracted: `dy = d_vec.dot(Yp_vertical) = 0` → collapse

**Additional layer of the problem:** If a vertex snap point (floor mesh vertex) was available, it bypassed the raycast entirely. Using that snap point directly guaranteed z=0 no matter what.

**Why this was hard to debug:** The collapse looked like "the rectangle is losing its height dimension" but the real issue was that the target point itself had no Z component. Even though the new basis (Yp = world Z) was correct for measuring height, the input point was fundamentally 2D (floor point). No amount of basis changes fix a 2D point.

### What was fixed

**Restructured update order:**
1. Compute orientation FIRST (set Zp, Xp, Yp for the current mode)
2. THEN resolve target using `intersect_line_plane(ray, center, Zp)` — now hits the correct plane
3. Target from a vertical plane intersection has proper Z component

**Skip snap points in perp mode:**
When `is_perpendicular = True`, always raycast to the vertical plane instead of using `snap_point` directly. Floor vertices always have z=0 and would collapse the height.

Result: when P flips the rectangle perpendicular, the target is determined by where the camera ray hits the vertical plane, which depends on camera angle — giving proper Z extent for the height dimension.

### Rule of thumb

**Plane-sensitive operations must establish the plane before projecting targets onto it.** If your target point is resolved using one plane basis but then you flip to a different basis, the target doesn't magically gain new components — it stays 2D. Always:
1. Compute the final basis first (set Zp/Xp/Yp for the mode you're in)
2. THEN resolve the target using `intersect_line_plane(ray, anchor, Zp)` with that basis
3. The resulting point will lie on your intended plane with the correct dimensionality

---

## Bug: X-crossing weld only catches first intersection per edge

**Date:** 2026-03-25
**Affected code:** `radCAD/weld_utils.py` — `perform_x_weld()` and `perform_self_x_weld()`

### What was wrong

When a drawn line crossed two (or more) existing edges, only one intersection would get welded. The other crossing point was silently skipped. Direction-dependent — sometimes the near one welded, sometimes the far one, depending on edge vert order.

### Why it happened

The old code split edges **immediately** when it found an intersection, inside the detection loop. After `bmesh.utils.edge_split()`:

1. Arc edge at `s=0.7` is found crossing target edge — split happens
2. `ae` (the arc edge reference) now points to the **shortened** portion `[0, 0.7]`
3. The next crossing at `s=0.3` (relative to the original full edge) is on the **other** half `[0.7, 1.0]` — but that half is a new edge object the loop never visits
4. Second crossing is silently lost

Same problem in `perform_self_x_weld` — split-as-you-go means each split invalidates the edge references for subsequent crossings.

### Why this was hard to debug

The weld *partially* worked — you'd get one clean crossing out of two. It looked like a tolerance or detection issue ("maybe the second intersection is too far away?"). But the detection math was fine. The real problem was that `edge_split` changes the topology mid-loop, and the loop had no idea the edge it was iterating over just got shorter.

The failed "split rescaling" attempt (rescale `s` parameters after each split) was mathematically sound on paper, but broke because it didn't track which *new edge object* to continue splitting — it kept trying to split the original (now-shortened) edge.

### What was fixed

Ported rCAD's **collect-then-split** approach:

1. **Phase 1 — Detect ALL intersections** without modifying anything. Store `(edge, param, world_pos)` tuples.
2. **Phase 2 — Create crossing verts** at each unique intersection point (one BMVert per crossing).
3. **Phase 3 — Group cuts by edge**, sort by parameter ascending.
4. **Phase 4 — Split in order with renormalization**: `fac_local = (t_abs - prev_t) / (1.0 - prev_t)`. After each split, find the continuation edge via `edge_between(new_vert, curr_right)` and advance along it.
5. **Phase 5 — Weld** all split verts to their crossing verts via `bmesh.ops.weld_verts(bm, targetmap=...)`.

Key helpers added:
- `edge_between(v1, v2)` — find the edge connecting two verts
- `_find_next_edge(new_vert, curr_left, curr_right)` — find continuation edge after split (with dot-product fallback)
- `_split_edge_at_cuts(bm, edge, cuts, ...)` — shared splitting logic for both functions

### Rule of thumb

**Never modify topology inside a detection loop.** `edge_split` changes the mesh graph — the edge you just split is now a different object with different endpoints. Collect all the information you need first, THEN do all the splits in a second pass. When splitting an edge at multiple points, sort by parameter and renormalize: `fac = (next_t - prev_t) / (1.0 - prev_t)`. Track the continuation edge after each split with `edge_between()`.

---

## Bug: Line draw on face doesn't cut the face (edges intersect but face stays intact)

### What was wrong
Drawing a line on a face with the polyline tool would weld into the face's edges correctly (Phase 1 x-weld worked), but the face itself never got split. The line just sat there crossing the face boundary edges without actually cutting the polygon.

### Why it happened
Two compounding issues:

1. **Selection loss during edge splits:** `_split_edge_at_cuts` uses `bmesh.utils.edge_split`, which does NOT propagate selection to new edges. After x-weld splits an arc edge at face boundary crossings, only the original (shortened) fragment stays selected. The inside-the-face fragments are new, unselected edges — so the knife cutter misses them.

2. **Knife project is the wrong tool for coplanar cuts:** Even with selection fixed, Phase 2's `knife_project` approach is fundamentally fragile for this case. After Phase 1 x-weld creates junction vertices on the face boundary and arc edges connect them through the face interior, the geometry is ALREADY there — we just need to tell bmesh to split the face. Knife project depends on view alignment, redraw timing, and projecting a cutter that's coplanar with the target — all unreliable.

### Why this was hard to debug
Phase 1 x-weld visually succeeds — vertices snap, edges split at intersections, everything looks connected. The knife project runs without errors. The face just... doesn't split. You'd assume the knife worked and look for issues elsewhere, when the real problem is that the knife approach itself is ill-suited for geometry that's already topologically connected.

### What was fixed
1. **`weld_utils.py`** `_split_edge_at_cuts`: propagate `was_selected = edge.select` to continuation edges after each split, so all arc fragments stay selected.

2. **`arc_weld_manager.py`** new Phase 1.5: after x-weld and remove_doubles, directly split faces using `bmesh.utils.face_split`. For each selected edge, find faces that contain both its endpoint verts in their boundary but don't contain the edge itself — those faces need to be cut along that edge. This is deterministic and doesn't depend on view state.

### Rule of thumb
**If the topology is already there (edges connecting face boundary verts), use `face_split` — don't project a knife.** Knife project is for cutting faces where no edges exist yet. When edges already connect boundary verts through the face interior, `bmesh.utils.face_split(face, v1, v2, use_exist=True)` is the direct, reliable solution.
