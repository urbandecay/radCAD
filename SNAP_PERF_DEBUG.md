# Snapping Performance Investigation

## The Problem
Snapping works (clicks register, verts/edges highlight correctly) but the whole drawing interface freezes/slows down when snapping is active. This happens even with the raycast approach, which should be "just find the geometry under the cursor and search nearby."

## What We've Tried

### 1. KD-Tree Spatial Partitioning
- **What:** Built KD-trees once per mesh, then did `find_range()` queries
- **Result:** Fast when it worked, but unreliable. Radius calculation broke at different zoom/angle combinations
- **Status:** Abandoned — couldn't get reliable snapping at all angles

### 2. Spatial Chunking (Grid-based)
- **What:** Divided world into 5-unit chunks, stored positions by chunk
- **Result:** Consistent but some things didn't snap, others did
- **Status:** Abandoned — inconsistent results

### 3. Camera-Centric Search
- **What:** Just search everything within 2x view_distance of the camera
- **Result:** Worked but slow (searching too much)
- **Status:** Abandoned — not selective enough

### 4. Raycast + Chunking (Current)
- **What:** Raycast to find what's under cursor, search chunks around that point
- **Result:** **Snaps perfectly but slow as hell**
- **Status:** Active — but needs perf investigation

## Where the Slowdown Likely Is

### Candidates
1. **The raycast itself** — `ctx.scene.ray_cast()` every frame might be expensive
2. **Chunk lookup** — `_get_chunks_in_radius()` generates a bunch of chunk IDs to search
3. **2D projection** — `location_3d_to_region_2d()` is called for every candidate in every frame
4. **Visibility checks** — `is_visible_to_view()` does another raycast per candidate
5. **The loop structure** — Something in the nested chunk → position → 2D check loop is hot

### Most Likely Culprit
Probably the **2D projection** (`location_3d_to_region_2d()`) or **visibility checks**. Both get called per candidate, per frame. If there are a million candidates or the functions are expensive, this adds up fast.

## Investigation Checklist

### Quick Wins to Try First
- [ ] Comment out the visibility check (`is_visible_to_view()`) — does it get faster?
- [ ] Comment out the 2D projection (`location_3d_to_region_2d()`) — does it get faster?
- [ ] Print how many candidates are being considered per frame (should be small!)
- [ ] Profile which function calls take the most time (use `import time; t0 = time.time()`)

### If It's Still Slow
- [ ] Check if the raycast is being called multiple times somehow
- [ ] Verify the chunk search radius is reasonable (shouldn't be huge)
- [ ] See if we can cache raycast results and reuse across frames

### Nuclear Option
If nothing else works, the raycast approach might just be too expensive. Consider:
- **Lazy raycast** — Only raycast when mouse moves significantly, reuse result for nearby frames
- **Approximate raycast** — Use a faster bounding-box based collision instead of exact ray-mesh
- **Hybrid approach** — Use chunks + spatial partitioning, but with a smarter radius calc

## Code Locations
- Main snap function: `radCAD/snapping_utils.py:snap_to_mesh_components()`
- Raycast call: Line ~183 (`hit_loc, _, _ = raycast_under_mouse(ctx, x, y)`)
- Chunk search: Line ~186 (`_get_chunks_in_radius()`)
- 2D projection: Multiple spots in the candidate loops (~210, ~225, ~240, ~260)
- Visibility check: Called per candidate (~270, ~275, ~285)

## What We Know Works
- Snapping logic itself is solid (priority system, distance filtering)
- Raycast approach gives correct snapping behavior
- Chunking data structure is fine
- The bottleneck is in the **performance**, not **correctness**

## Optimizations Attempted (From Research)

### 1. BVHTree Raycast (190–250× faster than scene.ray_cast)
- **What:** Replaced `scene.ray_cast()` with `BVHTree.FromBMesh()` for single-mesh raycasts
- **Result:** Massive speedup for finding geometry under cursor
- **Issue:** Only works for one mesh. User snaps to **multiple scene objects**, not a single edited mesh

### 2. Batched 2D Projection
- **What:** Replaced per-point `location_3d_to_region_2d()` calls with direct `perspective_matrix` math
- **Result:** Eliminated Python/C call overhead, faster projection
- **Status:** Working, helps but not the main bottleneck

### 3. Lazy Raycast (1–5 pixel threshold)
- **What:** Only raycast when mouse moves >5 pixels, reuse result otherwise
- **Result:** Some speedup, but doesn't solve far-geometry snapping
- **Issue:** Threshold is arbitrary. Too low = raycasts constantly. Too high = misses distant objects

### 4. Cache Persistence
- **What:** Removed `_snap_cache.clear()` that was nuking cache every frame
- **Result:** BVHTree built once instead of every frame
- **Status:** Working, solid improvement

## Current Blocker

**BVHTree optimization assumes snapping to a single mesh being edited. But the actual use case is snapping to multiple scene objects from a HUD overlay.**

The snap function receives different `obj` parameters (various scene meshes), so each one would need its own BVHTree. This defeats the purpose — we'd just be trading scene.ray_cast slowness for BVHTree building slowness for each object.

**Why far geometry doesn't snap well:** When BVHTree raycast misses a scene object (because raycast only checks the target mesh, not the scene), the fallback search center is wrong. The search_radius becomes too small or positioned incorrectly to find far away geometry.

## Next Steps
1. Determine actual snap target — is it one mesh or multiple scene objects?
2. If multiple objects: either accept scene.ray_cast cost or use a spatial index for the whole scene
3. If single mesh: current BVHTree approach is good, just need to tune lazy raycast threshold
4. Consider GPU-based picking (`glReadPixels` + unproject) as ultimate fallback
