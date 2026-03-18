# Drawing Bug Log

---

## Bug: Vertex dots invisible in POST_VIEW draw callback

**Date:** 2026-03-18
**Affected tool:** Arc 2-Point (stage 1 diameter drag), but affects all tools using `draw_points`

### What was wrong

`draw_points` was rewritten to draw flat pixel squares on screen (using `UNIFORM_COLOR` shader with 2D pixel coordinates) to work around a Blender 5.0 EEVEE-Next bug with the old 3D cube approach.

The problem: the main draw callback (`draw_cb_3d`) is registered as `POST_VIEW`, which means it runs in **3D world space**. When `draw_points` projected a 3D point to screen coordinates (e.g. pixel 500, 300) and then drew at those coordinates, Blender placed the squares 500 units to the right and 300 units up in the actual 3D scene — completely off screen, invisible.

The diameter **line** still worked because `draw_line` uses the `POLYLINE` shader with actual 3D world-space coordinates, which is correct for a POST_VIEW context.

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

Axis snapping works for tools that define their shape with a single snapped point (polygons, circles, arcs). For tools that need multiple independent dimensions defined at once (like a 2D rectangle in one drag), axis snapping can collapse a dimension. Use vertex snapping instead.

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

### What was fixed

Moved the vertical detection logic to run **before** the plane projection:
1. Get raw snapped target (may have Z component)
2. Run vertical detection and basis recomputation on raw target
3. NOW project onto the (possibly updated) drawing plane

This matches the Circle 2-Point tool behavior and enables proper Z-axis snapping with hysteresis-based plane reorientation.

---

## Bug: Polygon tools P key didn't flip normal

**Date:** 2026-03-18
**Affected tools:** Polygon (Center/Corner), Polygon (Center/Tangent), Polygon (Corner/Corner)

### What was wrong

When you pressed P to flip the polygon to perpendicular/upright mode, nothing happened. The P key handler toggled the `is_perpendicular` flag, but the `update()` method never read that flag — so Zp (the drawing plane normal) never changed.

### Why it happened

The P key toggle was added (`self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)`) but there was no corresponding logic in `update()` stage 1 to actually use that flag and recompute the plane.

Meanwhile, **PolygonTool_Edge** already had the full perpendicular+vertical hysteresis logic in its `update()` method, and the circle tools also had this. The other polygon tools were missing it.

### What was fixed

Added the full perpendicular plane computation block to `CenterCorner`, `CenterTangent`, and `CornerCorner` `update()` methods:

1. When `is_perpendicular = True`: Compute `new_Zp = drag_dir × ref_normal` (upright plane perpendicular to drag direction)
2. Stabilize the normal relative to view direction (flip if pointing toward camera)
3. Rebuild orthonormal basis: `Zp = new_Zp`, `Xp/Yp` from `Zp`
4. When `is_perpendicular = False`: Restore `Zp = ref_normal` (flat plane)

Also added vertical hysteresis (auto Z-snap) matching circle/edge tools: when drag is nearly vertical (dot > 0.98/0.995), automatically snap the plane perpendicular to the Z axis.

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

### What was fixed

Changed to compute perpendicular in the actual drawing plane using cross product:
```python
perp_dir = edge_dir.cross(self.Zp).normalized()
```

This is perpendicular to both the edge and the plane normal, ensuring it's always in the plane regardless of plane orientation. Applied to both `update()` and `refresh_preview()`.
