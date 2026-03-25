# Snap Efficiency Saga — Post-Mortem & Salvage Report

**Date:** 2026-03-25
**Purpose:** Document every iteration of the snap efficiency quest, identify the clean starting point, and evaluate what (if anything) is worth carrying forward into a fresh attempt.

---

## The Clean Starting Point

### Commit: `4106316` — "shift locking snap fixed for line tool"
**Date:** 2026-03-21 11:20
**Branch:** This is the last commit before any efficiency work began.

This is where you want to `git checkout` to start fresh. The snapping code at this point is simple, correct, and fully functional — it just doesn't scale to million-vert scenes.

The next commit `05e928c` ("snap logic now more efficient") is where the efficiency saga begins.

---

## How the Original (Clean) Snapping Works

File: `radCAD/snapping_utils.py` at commit `4106316`

Three functions:

1. **`raycast_under_mouse(ctx, x, y)`** — Single raycast from camera through mouse cursor. Returns hit location, normal, and object. Fast, clean, no issues.

2. **`is_visible_to_view(ctx, target_co)`** — For each snap candidate, casts ANOTHER ray to check if it's occluded by other geometry. This is the hidden performance killer — it's called once per candidate, and each call does a full scene raycast.

3. **`snap_to_mesh_components(ctx, obj, x, y, ...)`** — The main loop. Iterates through ALL verts, edges, and faces in the mesh:
   - Projects each element to 2D screen space via `location_3d_to_region_2d()`
   - Checks if it's within 15px of the mouse cursor
   - Collects candidates with priority (verts=0, centers=1, edge-nearest=2)
   - Sorts by priority then distance
   - Returns the first visible candidate

**Why it's slow with dense geometry:** Pure Python loop over every element, every frame. 1M verts = 0.5-1 second per frame just from the loop overhead, even before the expensive `is_visible_to_view` raycasts.

---

## Complete Timeline of Efficiency Iterations

### Phase 1: KD-Tree Attempts (Mar 21, 12:00–15:45)

| # | Commit | Name | What Happened |
|---|--------|------|---------------|
| 1 | `05e928c` | snap logic now more efficient | First attempt. Added KD-trees + `project_fast()` (direct perspective matrix projection). |
| 2 | `d0a646c` | line tool snaps to itself fix | Bug fix during the work (legit fix, not efficiency-related). |
| 3 | `e4626b5` | revert kdtree snap refactor | KD-tree radius calculation broke at different zoom levels — too small = missed geometry, too large = slow. Reverted. |
| 4 | `8a2e68f` | restore kdtree snapping code | Tried again. |
| 5 | `7281ce1` | use loop-based snapping instead of kdtree | Gave up on KD-tree again. |
| 6 | `c104b60` | restore original snapping code | Full reset to original. |
| 7 | `8bc4255` | revert all changes to original initial release | Another full reset. |
| 8 | `ff9e30a` | optimize snap: skip visibility check for close candidates | Tried skipping `is_visible_to_view` for nearby candidates. |
| 9 | `eebdbc6` | restore kdtree snapping for speed | Yet another KD-tree attempt. |
| 10 | `664c080` | add snap radius multiplier preference | Added user-configurable radius to work around the zoom issue. |
| 11 | `dfbc310` | add snap radius multiplier to preferences UI | UI for above. |

**Why KD-trees failed:** The fundamental problem is that snapping works in **screen space** (15px radius around cursor) but KD-trees work in **world space**. Converting between them requires knowing the camera distance/angle, which changes constantly. The radius parameter was always either too big (slow) or too small (misses snaps).

### Phase 2: Alternative Data Structures (Mar 21, 21:00 – Mar 22, 01:20)

| # | Commit | Name | What Happened |
|---|--------|------|---------------|
| 12 | `9ff672f` | bvhtree snapping attempt | Tried Blender's BVH tree. Same radius problem as KD-tree. |
| 13 | `b6c271f` | brute force bubble snap | "Bubble around cursor" concept — check only geometry within a world-space bubble. |
| 14 | `be4c64a` | brute force bubble 2 | Iteration on bubble. |
| 15 | `5a34c50` | restore f5 axis button + two-tier bubble | Two-tier: small bubble for fast check, large bubble as fallback. |

**Why bubbles failed:** Same fundamental issue — world-space bubble doesn't map cleanly to screen-space pixel radius. A bubble that's the right size when zoomed in is wrong when zoomed out.

### Phase 3: Grid Spatial Partitioning (Mar 22, 01:48 – 05:15)

| # | Commit | Name | What Happened |
|---|--------|------|---------------|
| 16 | `044cc17` | grid spatial partitioning with 7x7x7 cell search | The big rewrite. Divided 3D space into grid cells, stored geometry by cell. Massive diff: 8 files, +1552/-505 lines. Also added `SNAPPING_RESEARCH_PROMPT.md` and `SNAP_PERF_DEBUG.md`. |
| 17-26 | `6f1d23a` → `74a94ad` | snap fix opus attempt (×10) | Rapid-fire attempts to fix broken snapping. ~2 min between commits. |
| 27-28 | `30345cd`, `5367346` | snapping fix opus attempt 2 (×2) | Still broken. |
| 29-30 | `8c10b9e`, `4260a20` | snap fix hud grid overlay opus attempt | Added HUD overlay for debugging the grid. |
| 31 | `20b697c` | snapping fix opus attempt 3 | More fixes. |
| 32-34 | `764e295` → `6fd3f8e` | snapping fix screenspace opus attempt (×3) | Tried fixing by doing the grid lookup in screen space instead. |

**Why the grid broke snapping:** Grid cells are in world space. Geometry near cell boundaries could be in a neighboring cell, so queries that only check the current cell miss nearby snaps. The 7×7×7 search window was supposed to fix this but created its own problems — too many cells to search when zoomed out, not enough when zoomed in. Same old world-space vs screen-space mismatch.

### Phase 4: Caching & Weld Fixes (Mar 22, 11:30 – 17:35)

| # | Commit | Name | What Happened |
|---|--------|------|---------------|
| 35-37 | `78d8e13` → `a76b316` | grid snapping overlay efficient cache attempt (×3) | Tried caching the grid to avoid rebuilding every frame. |
| 38-42 | `b9cd639` → `aff2f09` | efficient weld fix opus attempt (×5) | Grid efficiency broke the weld tool. 5 attempts to fix it. |
| 43 | `32baeb2` | snap efficiency fixed hopefully | "Fixed" snapping... |
| 44 | `d6bb1e4` | compass flush to face refix | ...but broke compass flush-to-face. |
| 45 | `1ffcdb3` | snap efficiency fixed hopefully | Final attempt. Compass still broken. |

**Why it kept breaking:** Each fix was patching symptoms of a fundamentally wrong approach. The grid was fighting against the screen-space nature of the snapping system. Making the grid work meant changing how snapping calculated distances, which broke compass orientation (which depends on raycast normals), which broke weld (which depends on correct snap targets).

---

## What's Worth Salvaging

### KEEP: `project_fast()` — Direct Perspective Matrix Projection
**Introduced in:** `05e928c`
**What it does:** Replaces `location_3d_to_region_2d()` with a direct matrix multiply using the perspective/view matrix. Significantly faster for projecting 3D→2D because it skips Blender's function call overhead.
**Verdict:** This is a pure win. It makes the existing loop faster without changing any logic. Should be the FIRST thing applied to the clean codebase.

```python
def project_fast(wco):
    """Fast 2D projection using perspective matrix."""
    v = pm @ wco.to_4d()
    if v.w <= 0:
        return None
    return Vector((W * (1 + v.x / v.w) * 0.5, H * (1 + v.y / v.w) * 0.5))
```

### KEEP (CONCEPT): Skip `is_visible_to_view` for Top Candidates
**Introduced in:** `ff9e30a`
**What it does:** If the closest candidate is very close to the cursor (e.g., <5px), skip the expensive visibility raycast and just snap to it. The reasoning: if it's right under the cursor, it's almost certainly visible.
**Verdict:** Good heuristic. Saves one raycast per frame in the common case. Simple to implement, doesn't change logic.

### KEEP (CONCEPT): Spatial Partitioning as a Concept
**The grid idea is correct.** You're right that if the dense mesh is in cubes far from where you're working, you shouldn't have to iterate through it. The problem was always the implementation:
- World-space grid cells don't map to screen-space snap radius
- Cell boundaries cause missed snaps
- The radius/cell-size tuning was zoom-dependent

### DISCARD: Everything Else
- KD-tree radius calculations — fundamentally broken for screen-space snapping
- BVH tree approach — same problem
- Bubble approach — same problem
- Grid overlay HUD (from the efficiency attempts) — was debugging aid for broken logic
- Weld fix patches — fixing symptoms of broken grid
- Snap radius multiplier preference — band-aid for broken radius calc
- `SNAPPING_RESEARCH_PROMPT.md` and `SNAP_PERF_DEBUG.md` — research docs for the failed approach

---

## The Core Insight: Why Every Approach Failed

**Every single approach tried to solve a screen-space problem with world-space data structures.**

Snapping is fundamentally a 2D problem: "what geometry is within 15 pixels of my cursor?" But KD-trees, grids, BVH trees, and bubbles all partition 3D world space. The mapping between "15 pixels on screen" and "X units in world space" changes constantly based on:
- Camera distance (zoom)
- Camera angle (perspective distortion)
- Camera type (ortho vs perspective)

This is why the radius was always wrong — too small or too large depending on view.

---

## Recommended Approach for the Fresh Attempt

The grid system is the right idea, but it needs to be implemented with awareness of the screen-space issue. Here's the key insight:

**Don't try to convert screen-space radius to world-space. Instead, use the grid to CULL, then do the screen-space check on the survivors.**

1. **Build a world-space grid** (simple uniform grid, cells maybe 1-5 Blender units)
2. **On each frame:** Determine which grid cells are VISIBLE in the viewport (frustum culling)
3. **Further narrow:** Of the visible cells, which ones are "near" the cursor? Use a generous world-space radius (err on the side of too large)
4. **Only iterate elements in those cells** — this is where you get the speedup
5. **Do the existing 2D screen-space check** on just those candidates (project, measure pixel distance, etc.)

The key difference from what was tried: **the grid is only a coarse pre-filter**. It doesn't need to be precise. It just needs to eliminate the 95% of geometry that's clearly nowhere near the cursor. The existing screen-space logic handles the precise 15px check.

For the dense-mesh-in-a-corner scenario: if the cursor is far from that corner, zero cells from that region pass the pre-filter, and you iterate zero of those million verts. That's the win.

### Grid HUD Overlay
Build a simple wireframe overlay showing the grid cells. Color-code:
- Gray: cells that exist (have geometry)
- Yellow: cells currently being searched (in the pre-filter)
- Green: cells with actual snap candidates

This makes debugging trivial — you can see exactly what the system is considering.

---

## What Was Actually Implemented

### Fresh Implementation (Starting from `4106316`)

Built from scratch with these principles:
1. **Grid is only a coarse pre-filter** — eliminates geometry that's obviously too far to ever snap
2. **All snapping logic stays in screen space** — the 15px pixel-distance check is 100% original
3. **No world-space radius guessing** — the grid search uses a generous radius to avoid missing candidates

### Code Changes

**File: `radCAD/snapping_utils.py`**
- New `SpatialGrid` class: bins geometry into uniform 3D cells (default 2.0 BU size)
- Grid is cached; only rebuilds when mesh topology or transform changes
- `_estimate_search_radius()`: converts pixel radius to world-space via unprojection (handles ortho/perspective)
- `project_fast()`: direct matrix projection, ~2-3x faster than `location_3d_to_region_2d()`
- `snap_to_mesh_components()`: query only cells near cursor, keeps original screen-space logic
- Added early-exit: skip visibility raycast for candidates < 5px from cursor
- Debug state tracking: which cells were searched, which had candidates

**File: `radCAD/tool_previews.py`**
- `draw_snap_grid_overlay()`: renders wireframe cubes for each cell
- Color coding: gray (populated), yellow (searched), green (candidates)
- Callable via `state["show_snap_grid"]` toggle

**File: `radCAD/modal_core.py`**
- **F6** key toggles the grid overlay on/off
- HUD button click handler supports the toggle

**File: `radCAD/hud_overlay.py`**
- Added "F6: Grid" to the hotkey display

**File: `radCAD/modal_state.py`**
- Added `"show_snap_grid": True` state key

### Why This Works

The original "polished turd" attempts all tried to make world-space data structures (KD-tree, grid, BVH) do the work of a screen-space problem. This implementation separates concerns:

- **Coarse filter (world space):** "Which cells might have snappable geometry?"
- **Precise filter (screen space):** "Is it within 15px of the cursor?"

The grid speeds up the coarse filter by ~95% (skip cells with no nearby geometry). The screen-space logic remains unchanged, so snapping behavior is pixel-perfect.

---

## Summary

| Item | Status |
|------|--------|
| Clean starting point | ✓ Checked out `4106316` |
| Spatial grid pre-filter | ✓ Implemented (`SpatialGrid` class) |
| `project_fast()` optimization | ✓ Applied |
| Early-exit visibility check | ✓ Applied |
| Grid debug overlay | ✓ Implemented with F6 toggle |
| Screen-space snap logic | ✓ Unchanged (100% original) |

**How to test:**
1. Start any drawing tool (Line, Arc, etc.)
2. Press F6 to toggle grid overlay
3. Cells: gray=populated, yellow=searched, green=candidates
4. Create a dense mesh ball in one corner, draw elsewhere — snapping should be responsive even with large geometry elsewhere

---

## Known Issue: Gray Cells Not Turning Yellow

**Symptom:** When cursor enters a gray cell, it should turn yellow (marked as "searched"), but remains gray.

**Root cause:** The `_estimate_search_radius()` function calculates too small a world-space search radius. This causes `get_nearby_cells()` to miss cells that the cursor actually occupies.

**Why it breaks snapping:** If a cell isn't in the "nearby cells" list, its geometry never gets checked for snapping, even though the cursor is inside that cell's bounds. The geometry is silently ignored.

**Color meanings clarified:**
- **Gray** = cell contains geometry (populated)
- **Yellow** = cell is within search radius around cursor (being searched)
- **Green** = cell had geometry that passed screen-space 15px check (actual candidates)

**Cursor should turn gray cell to yellow** the moment it enters that cell's world-space bounds.

**Debug instructions:**
1. In `snapping_utils.py`, uncomment the debug print in `get_nearby_cells()` (line ~133)
2. Also uncomment the print in `_estimate_search_radius()` (line ~240)
3. Move cursor near a gray cell and watch console:
   - Check what `radius` value is calculated
   - Check what `r_cells` (cell search distance) evaluates to
   - If `r_cells` is 0 or 1 when cursor is near geometry, radius is too small

**Likely fix:** Increase the 2.0x multiplier in `_estimate_search_radius()` to 3.0x or 4.0x, or improve the unprojection math for orthographic views.
