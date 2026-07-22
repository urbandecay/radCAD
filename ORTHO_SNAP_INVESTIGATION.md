# Ortho Mode Snapping Investigation Report

**Status:** UNRESOLVED — problem persists after multiple approaches
**Date Started:** 2026-03-26
**User:** molotovgirl

## Problem Statement

In **ortho mode only**, tools do not snap to mesh geometry (vertices, edges, face centers). The tools draw correctly (using void drawing fallback), but snapping to existing geometry fails completely.

**Behavior:**
- ✅ Perspective mode: snapping works perfectly (verts, edges, centers all snap)
- ❌ Ortho mode: no snapping, tools draw on fallback void plane aligned to view
- ✅ Compass positioning: works correctly in both modes (positions flush on surfaces)

This is view-mode-specific — same mesh, same tools, same settings.

---

## Investigation Summary

### 1. Initial Theory: Spatial Grid Ray March
**Finding:** In ortho mode, `region_2d_to_origin_3d()` places the ray origin on the near clip plane (potentially far behind geometry). The spatial grid's `get_cells_along_ray()` with `max_depth=100.0` wasn't marching far enough.

**Attempted Fix:** Increased `max_depth` to 2000 for ortho mode.

**Result:** ❌ No change. Snapping still doesn't work.

---

### 2. Second Theory: All Cells Approach
**Finding:** In ortho, depth is meaningless for snapping (z-coordinate doesn't affect screen position). Changed cell lookup to use ALL populated grid cells instead of ray marching.

**Code location:** `/radCAD/snapping_utils.py:369-374`
```python
if rv3d.view_perspective == 'ORTHO':
    nearby_cells = list(_spatial_grid.cells.keys())
else:
    # Ray march for perspective
```

**Result:** ❌ No change. Candidates still not found.

---

### 3. Third Theory: Bypass Grid Entirely (CURRENT FIX)
**Hypothesis:** The spatial grid has subtle bugs in ortho mode (stale cache, incorrect cell lookups, or something else). Rather than patch it, bypass it completely for ortho.

**Approach:**
- Created new function `_snap_ortho_direct()` that:
  - Iterates ALL BMesh verts/edges/faces directly (no grid)
  - Uses Blender's own `location_3d_to_region_2d()` instead of custom `project_fast()`
  - Skips `is_visible_to_view()` visibility check (ortho ray origin placement can cause false negatives)
  - Maintains same priority system (verts > edges > face/edge centers)

**Code location:** `/radCAD/snapping_utils.py:325-398`

**File changes:**
- Moved snap logic into `_snap_ortho_direct()` helper function
- Early return from `snap_to_mesh_components()` if `rv3d.view_perspective == 'ORTHO'`
- Perspective mode path (spatial grid) left completely untouched

**Result:** ❌ Still doesn't work.

---

## Key Findings (Verified)

### A. Compass Logic Works in Both Modes ✅
**File:** `/radCAD/modal_core.py:334-338`
- Uses `ctx.scene.ray_cast(depsgraph, ray_origin, view_vec)` to find surfaces
- Ray origin is the same `region_2d_to_origin_3d()` in ortho
- Gets surface normal correctly and orients compass
- Compass positioning uses `orthonormal_basis_from_normal()` then `location_3d_to_region_2d()` for screen projection

**Implication:**
- `region_2d_to_origin_3d()` in ortho works fine
- `location_3d_to_region_2d()` in ortho works fine
- `scene.ray_cast()` in ortho works fine
- View-dependent projection math is NOT the issue

### B. Snap Calls Same Ray Functions
**File:** `/radCAD/modal_core.py:331-339`
- Fallback surface raycast (when no mesh snap found) uses same ray functions as compass
- Gets surface normal and stores in `state["last_surface_normal"]`

**Implication:**
- Ray origin/direction computation is consistent
- If fallback raycast works (which it must, since compass works), the ray math is sound

### C. Current Snap Data Flow
1. `on_move()` calls `get_snap_data(context, event.mouse_region_x, event.mouse_region_y)` (line 379)
2. `get_snap_data()` calls `snap_to_mesh_components()` (line 233)
3. If snap returns None, falls back to surface raycast (line 331-339)
4. Returns (snap_point, snap_normal) to tool's `update()` method

**Problem is in step 2-3:** `snap_to_mesh_components()` returns None in ortho, triggering fallback.

---

## Critical Unknowns

### What's Actually Being Called?
- Is `snap_to_mesh_components()` even being invoked in ortho mode?
- Is `ctx.edit_object` valid in ortho?
- Is the BMesh data being read correctly?

### Why Does `location_3d_to_region_2d()` Work for Compass but Not Snapping?
- Compass: projects points to screen to calculate scaling
- Snapping: projects points to screen to check pixel distance
- Same function, same data... why different results?

### Is It Downstream?
- Snap data IS being calculated (fallback raycast works)
- But vertex snap specifically returns None
- Could the tool be IGNORING the snap data even if it's correct?

---

## What Needs Investigation Next

### Priority 1: Verify `_snap_ortho_direct()` is Actually Running
Add temporary debug output (not permanent code):
- Print when function is entered
- Print ortho_scale, view_perspective value
- Print how many verts/edges are found
- Print what candidates are created
- Print final return value

This will reveal if the function runs at all, and where it breaks.

### Priority 2: Check BMesh Integrity
- Verify `bm.verts.ensure_lookup_table()` works in ortho
- Check if hidden verts are being filtered correctly
- Confirm matrix_world is valid

### Priority 3: Projection Sanity Check
- For one known vertex, trace the full projection pipeline:
  - World position: `mw @ v.co`
  - Screen position: `location_3d_to_region_2d(region, rv3d, wco)`
  - Distance to cursor: `(mouse - p2d).length_squared`
- Manually verify the math is correct in ortho view

### Priority 4: Visibility Check Theory
- Try REMOVING the visibility check entirely (not just for close candidates)
- See if that unblocks snapping
- File: `/radCAD/snapping_utils.py:468`

### Priority 5: Tool Side
- Verify tools actually USE the snap_point passed from `get_snap_data()`
- Check if there's ortho-specific logic in tool update methods that ignores snap data

---

## Code Locations (Quick Reference)

| What | File | Lines |
|------|------|-------|
| Main snap entry point | `modal_core.py` | 219-364 |
| Snap function calls | `modal_core.py` | 233 |
| Mouse move handler | `modal_core.py` | 366-382 |
| Snapping logic | `snapping_utils.py` | 325-470 |
| Ortho snap (new) | `snapping_utils.py` | 325-398 |
| Compass flush logic | `modal_core.py` | 341-352 (void drawing ortho logic) |
| Compass orientation | `orientation_utils.py` | 5-12 |
| Surface raycast | `modal_core.py` | 334-339 |

---

## Hypothesis for Next Chat

**Most likely issue:** `_snap_ortho_direct()` is running and iterating verts correctly, but **candidates list is empty because `location_3d_to_region_2d()` returns None for all verts** due to ortho clipping plane behavior.

**Test:** Print `p2d` value for first few verts in ortho snap function. If all None, the clipping theory is correct.

**Second hypothesis:** Something in tool's `update()` method ignores snap_point in ortho mode.

---

## Files Modified

- `/radCAD/snapping_utils.py` — Added `_snap_ortho_direct()` helper, modified `snap_to_mesh_components()` to use it for ortho

No other files changed. Perspective mode code path untouched.

---

## Next Steps

1. Add debug output to `_snap_ortho_direct()` to verify execution and return values
2. Trace one vertex through the full projection pipeline
3. Test with visibility check completely disabled
4. Check tool's update method for ortho-specific handling
5. Verify BMesh data integrity

Good luck! — Claude
