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
