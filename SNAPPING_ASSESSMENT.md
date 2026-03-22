# Snapping Performance Assessment & Problem Statement

## Problem Summary
The snapping system in radCAD is **extremely slow** even when snapping to very simple geometry (single box with ~8 vertices). The issue is NOT geometry density—it persists regardless of mesh complexity or size.

## What Was Tried (All Failed)

### Approach 1: Scene Bounding Box Calculation
- **Idea**: Calculate scene bounds on addon load, divide into N cubes
- **Why it failed**:
  - Cannot access `bpy.context.scene` during addon registration (RestrictContext error)
  - Bounding box calculation itself was slow and confusing
  - Added unnecessary complexity without fixing the actual bottleneck

### Approach 2: Spatial Grid with World-Space Size
- **Idea**: Simple grid with configurable cell size (e.g., 10.0 world units)
- **Status**: Partially implemented, but performance issue persists
- **Key Discovery**: Removed `_snap_cache.clear()` on line 48—this was destroying the cache every frame and forcing full grid rebuild with all 1M verts. After removal, cache actually worked, but snapping is STILL slow even on simple geometry.

### Approach 3: World-Space Distance Culling
- **Idea**: Skip projecting verts that are far away in world space before expensive screen-space calc
- **Why it failed**: Added `world_cull_dist = grid_size * 3.0` check, but didn't improve performance noticeably

### Approach 4: Grid Visualization for Debugging
- **Idea**: Draw grid cells as 3D wireframes to see what's being searched
- **Why it failed**:
  - GPU drawing code is complex and may itself be causing lag
  - Visualization doesn't render (debug info not printing)
  - Likely became another bottleneck rather than helping diagnose

### Approach 5: Multiple Parameter Tuning
- Tried `num_cubes` parameter (1-100 range)
- Tried varying search radius (7x7x7 cells, then adaptive)
- Tried different world-space cull distances
- **Result**: None of these addressed the root cause

## Key Findings

### The Real Bottleneck (Unidentified)
The snapping is slow for a **simple box with 8 vertices**. This proves:
- It's NOT a geometry density problem
- It's NOT the grid search mechanism (grid is working, cache removed)
- It's NOT vertex iteration (only 8 verts!)
- **The bottleneck must be in:**
  1. Matrix multiplications (`mw @ v.co` or perspective projection)
  2. The `project_fast()` function itself
  3. The screen-space distance calculation
  4. The call to `geometry.intersect_point_line()` for edge snapping
  5. Something else in the core snap_to_mesh_components loop

### What Works
- Grid caching (after removing the `.clear()`)
- Basic grid spatial partitioning
- Screen-space bubble (15px radius)
- Sparse mesh snaps fast when dense mesh is deleted

### What Doesn't Work
- Fast snapping on small geometries
- Performance is consistent slowness, not occasional hiccups
- Visualization/debugging infrastructure

## Current Code State

**File: `radCAD/snapping_utils.py`**
- Line 48: `_snap_cache.clear()` was REMOVED (good fix)
- Grid building is cached based on mesh topology (vert/edge/face count + grid_size)
- `snap_to_mesh_components()` does:
  1. Read grid_size from preferences (default 10.0)
  2. Get cached grid or build it
  3. Calculate query_pt from camera ray
  4. Find nearby cells (7x7x7 search)
  5. For each element in nearby cells:
     - Call `project_fast()` (perspective matrix mult)
     - Check screen distance (15px bubble)
     - Track best candidates
  6. Edge snapping with `geometry.intersect_point_line()`

**File: `radCAD/preferences.py`**
- Added `snap_grid_size` FloatProperty (0.5-100.0, default 10.0)
- Displayed in UI under "Geometry Snaps" section

**File: `radCAD/hud_overlay.py`**
- Debug grid visualization added but disabled (likely was slowing things down further)

**File: `radCAD/__init__.py`**
- Imports snapping_utils
- Calls register_handlers() on addon load (currently disabled)

## Desired Outcome

**GOAL**: Snapping should be **snappy and responsive** (< 1ms per snap call) even with:
- Simple geometry (8 vert box)
- Dense geometry in one corner
- Large scene with 1M+ total verts

**Acceptance Criteria**:
1. Snapping to a box with 8 verts is instant/responsive (no noticeable lag)
2. Snapping on sparse mesh is fast even when dense mesh exists elsewhere
3. User can set grid_size preference to tune performance (higher = faster but less detail)
4. No jank, stuttering, or angle-dependent failures
5. Visualization (if re-enabled) doesn't add lag

## Recommendations for Next AI

1. **Profile the actual bottleneck** - add timing instrumentation to identify which operation is slow:
   - Time grid building
   - Time each projection call
   - Time distance calculations
   - Time edge snapping

2. **Consider completely different approach**:
   - Maybe snapping shouldn't use screen-space projection at all?
   - Maybe use Blender's built-in snap system instead of custom implementation?
   - Maybe the perspective matrix multiplication is just inherently expensive and unavoidable?

3. **Disable features**: If snapping is slow, maybe:
   - Disable edge snapping (`do_edges=False`)
   - Disable face center snapping
   - Only snap to verts

4. **Check if issue is in bmesh operations**:
   - `bmesh.from_edit_mesh()` every frame?
   - `.ensure_lookup_table()` every frame?

5. **Never re-enable visualization** unless you've confirmed it's not the bottleneck

## Files Modified This Session
- `radCAD/snapping_utils.py` - grid system, caching, culling attempts
- `radCAD/preferences.py` - snap_grid_size property
- `radCAD/hud_overlay.py` - debug visualization (mostly disabled)
- `radCAD/__init__.py` - imports and handler registration
- `radCAD/panel.py` - connected clear button to handlers

## Historical Context
- Previous version used KD-tree snapping (commit eebdbc6)
- User complained about "snap skip" issues with KD-tree
- Switched to brute force (commit b6c271f)
- User said brute force was too slow
- Grid-based approach was attempted to fix both issues
- Grid caching fix worked (cache.clear() removed) but core slowness remains
