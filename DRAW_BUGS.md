# Drawing Bug Log

---

## Bug: Snapping glacially slow with large geometry (million+ verts)

**Date:** 2026-03-21
**Affected tool:** All tools with snapping enabled (Line, Arc, Circle, etc.) when scene contains dense geometry

### What was wrong

When a scene had a million verts/edges/faces bunched up in one corner and you were working on a simple cube elsewhere, snapping would be painfully slow — taking half a second or more per frame. But if you deleted the dense geometry bundle, snapping was instant again.

### Why it happened

The snapping code was doing a **Python loop through every single vertex, edge, and face** to check if it was near the cursor. Even with a fast per-element check (world-space distance culling), looping through 1 million elements in Python each frame is inherently slow. Python can iterate ~1-2 million times per second, so 1M elements = 0.5-1 second per frame. The `near_ray()` pre-filter helped when geometry was clustered far away, but it still had to iterate through everything to reject it.

### Why this was hard to debug

The snapping *worked* — it just felt sluggish. You'd initially think "weird, it's slow when snap is on" but the real issue wasn't snap logic, it was the algorithmic approach of checking all geometry every frame. The pre-filter optimization (`near_ray()`) helped but couldn't solve the fundamental bottleneck: **Python looping over millions of elements**.

### What was fixed

Replaced the Python loops with **cached KD-trees** (C-level spatial data structures):
1. Build KD-trees for verts, edge centers, and face centers when the tool starts
2. Cache them aggressively — only rebuild when mesh topology changes (vert/edge/face count)
3. Every frame, query the KD-tree instead of looping: "give me geometry near my cursor" returns only ~5-10 candidates instead of 1M
4. Run expensive 2D projection and pixel checks only on those candidates

For edge snapping (nearest point on edge), query the edge center KD-tree to find candidate edges, then do the full intersection math only on those.

**Result:** Snapping stays blazing fast even with a million verts because we're doing spatial queries (O(log n)) instead of loops (O(n)).

### Rule of thumb

**Never iterate through potentially millions of elements in Python per frame.** Use spatial data structures (KD-trees, BVH trees, voxel grids) to cull to relevant candidates first, then iterate only on those. For Blender: `mathutils.kdtree.KDTree` builds in C and queries in O(log n) — use it aggressively for performance-critical loops.

The trade-off: KD-tree builds have upfront cost (~100-500ms for 1M elements), but that's one-time. Queries are instant. Cache the trees and only rebuild when the mesh changes.

---

## Bug: Shift lock completely ignored snap points

**Date:** 2026-03-21
**Affected tool:** LineTool_Poly (polyline tool) when shift lock is held

### What was wrong

When holding shift to lock the line direction (constraining to a locked axis), any geometry snaps nearby would be completely ignored. The cursor would just follow the locked axis, bypassing snapping entirely.

### Why it happened

In `LineTool_Poly.update()`, the shift lock logic calculated the target position using `geometry.intersect_line_line()` to find where the mouse ray intersected the locked axis line. But it never checked the `snap_point` parameter that was already passed in! So even if you were hovering directly over a snappable vertex, shift lock would override it and project to the raw axis instead.

The code was:
```python
if self.shift_lock_vec:
    # Calculate target from mouse ray ∩ locked axis
    res = geometry.intersect_line_line(ray_o, ray_o + ray_v, ref, ref + self.shift_lock_vec)
    if res:
        target = res[1]  # <-- snap_point completely ignored!
```

### Why this was hard to debug

The behavior seemed correct at first — shift lock was constraining the direction. But the snap behavior felt "off" when you expected to snap to geometry while shift-locked. You'd naturally think "shift lock is working, snapping should still work" but didn't realize the code was completely discarding the snap point.

### What was fixed

After calculating the shift-locked target position, check if a geometry snap point exists. If it does, project that snap point onto the locked axis instead of using the raw ray intersection:

```python
if self.state.get("geometry_snap") and snap_point is not None:
    # Project snap_point onto the locked axis
    diff = snap_point - ref
    target = ref + self.shift_lock_vec * diff.dot(self.shift_lock_vec)
```

Now shift lock **and** snapping work together: you get axis-constrained snapping, not axis-constrained mouse position.

### Rule of thumb

When you have multiple input sources (snapped geometry, user input constraints, etc.), don't silently override one with another. Instead, **apply constraints to the snapped result**. Project/transform the snap point through the constraint rather than discarding it.

---

## Bug: Line tool edge snapping didn't work

**Date:** 2026-03-21
**Affected tool:** LineTool_Poly (polyline tool)

### What was wrong

When drawing polylines, you could snap to vertices (the endpoints of previous line segments) but NOT to the edges between them (the line segments themselves). Edge snapping only worked for the CURVE_INTERPOLATE tool.

### Why it happened

The edge snapping code in `modal_core.py` (lines 268-293) was gated by a condition that ONLY ran for CURVE_INTERPOLATE:

```python
if state.get("tool_mode") == "CURVE_INTERPOLATE":
    # ...check edges and edge centers...
```

LINE_POLY was building preview geometry (like CURVE_INTERPOLATE) but wasn't included in this condition. So while LINE_POLY had self-snapping to previous vertices, it was missing the more sophisticated edge/edge-center snapping that would let you snap to points along the preview line segments.

### Why this was hard to debug

The snapping appeared to "work" — you could snap to vertex endpoints. It only became obvious there was a bug when you tried snapping to the middle of a line segment and nothing happened. But then you'd check the edge snapping code and see it was only enabled for CURVE_INTERPOLATE, making it look intentional rather than an oversight.

### What was fixed

Changed the condition from:
```python
if state.get("tool_mode") == "CURVE_INTERPOLATE":
```

To:
```python
if state.get("tool_mode") in ["LINE_POLY", "CURVE_INTERPOLATE"]:
```

Now both tools get edge center and edge snapping for their preview geometry.

### Rule of thumb

When you add a feature to one tool class (like preview edge snapping), check if other tools with similar structure would benefit. Tools that build multi-segment previews (`preview_pts`) and have self-snapping enabled should probably share the same snapping logic — don't leave features isolated to a single tool unless there's a specific reason.

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
