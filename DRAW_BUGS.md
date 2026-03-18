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
