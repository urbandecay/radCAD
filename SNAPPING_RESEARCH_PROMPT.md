# Snapping Performance Research Prompt

## The Core Problem
I have a Blender addon that snaps the mouse cursor to geometry (vertices, edges, face centers) while drawing. The snapping **works perfectly** — it finds the right geometry and highlights it correctly — but the **entire interface freezes/slows down dramatically** when snapping is active. The slowdown happens regardless of how I optimize the search algorithm.

## What the Snapping System Does
1. **Every frame:** User moves mouse in the 3D viewport
2. **Raycast:** Cast a ray from camera through the mouse cursor to find what geometry is under the cursor
3. **Search nearby:** Find all snappable geometry within a region near the hit point
4. **Project to 2D:** Convert each candidate from 3D world space to 2D screen space
5. **Filter:** Keep only candidates within 15 pixels of the mouse cursor on screen
6. **Return:** Snap the cursor to the closest valid candidate

## Implementations Attempted

### Approach 1: KD-Tree Spatial Partitioning
```
- Build KD-trees once per mesh topology change
- Query: find_range(query_point, radius) to get nearby candidates
- Problem: Radius calculation breaks at different zoom levels and viewing angles
- Radius too small = misses snappable geometry
- Radius too large = searches too much, gets slow
```

### Approach 2: Spatial Chunking (Grid-Based)
```
- Divide 3D space into fixed 5-unit grid cells
- Store geometry positions organized by which cell they're in
- Query: Get all cells within radius, search only those cells
- Problem: Inconsistent snapping (some geometry snaps, some doesn't)
- Suspected issue: Chunk boundary artifacts or chunk size/radius mismatch
```

### Approach 3: Camera-Centric Search
```
- Search all geometry within (camera_distance * 2.0) of the camera
- No radius calculation, no complexity
- Problem: Searches too much geometry, interface slows down
```

### Approach 4: Raycast + Chunking (Current, Most Accurate but Slowest)
```
- Raycast to find what's under the cursor
- Search chunks around the hit point
- Works perfectly but causes noticeable slowdown
- Suspects: Raycast cost, 2D projection cost, or visibility checks
```

## Known Performance Bottlenecks (Likely Culprits)

### Candidate 1: Raycast Overhead
- `ctx.scene.ray_cast(depsgraph, ray_origin, ray_vector)`
- Called once per frame per snap query
- Blender's raycast is not lightweight — it checks ALL scene geometry

### Candidate 2: 2D Screen Projection
- `location_3d_to_region_2d(region, rv3d, world_coordinate)`
- Called for every candidate in every frame
- If there are 100+ candidates per frame × 60 FPS = expensive

### Candidate 3: Visibility Raycast
- `is_visible_to_view(ctx, candidate_pos)`
- Does another raycast per candidate to check if it's occluded
- Same issue as Candidate 1, multiplied by number of candidates

### Candidate 4: Chunk Calculation
- `_get_chunks_in_radius(center, radius)` generates chunk IDs in a sphere
- Might be generating too many chunks to search
- Nested loop structure (chunks → positions → 2D projection) might be inefficient

### Candidate 5: The Loop Structure Itself
- Nested loops through chunks, then positions, then distance checks
- If any of the inner operations is expensive, it gets multiplied

## The Paradox
- **With KD-tree:** Unreliable snapping (misses geometry) but fast when it works
- **With chunking:** Works perfectly but slow
- **With raycast:** Perfect accuracy and perfect snapping behavior, but interface freezes

This suggests the issue is **not** the search algorithm — the issue is something in the **common code path** that all three approaches share: the projection, visibility checking, or candidate processing loop.

## What I Need Help With

### Specific Questions
1. Is there a way to do **partial raycasts** or **approximate raycasts** that are cheaper than `ctx.scene.ray_cast()`?
2. Is `location_3d_to_region_2d()` known to be slow? Are there faster alternatives?
3. Should visibility checking be done at all during snapping, or is it optional?
4. What's the typical overhead of calling Blender API functions like this 100+ times per frame?

### General Approaches
1. **Lazy evaluation:** Can we raycast only when the mouse moves significantly, then reuse the result?
2. **Approximation:** Instead of exact raycasts, use bounding boxes or sphere tests (faster)?
3. **Batching:** Can multiple raycasts be batched in Blender?
4. **Caching:** What can be safely cached between frames?
5. **Data structure:** Is there a better spatial partitioning scheme than KD-trees or chunks?

### Research Topics
- Blender addon performance optimization
- Fast 3D-to-2D projection in Blender (alternatives to `location_3d_to_region_2d`)
- Efficient raycasting in Blender (is there a faster raycast mode?)
- Spatial partitioning for dynamic scene queries
- GPU-accelerated viewport operations in Blender

## Constraints & Context
- This is a Blender addon (Python code)
- The snapping happens during an interactive modal operator (real-time, every frame)
- The user is drawing live, so latency matters (60+ FPS expected)
- The addon modifies geometry (extrudes, creates faces) as the user draws
- Sometimes there's a million-vertex mesh in the scene (stress test scenario)

## Success Criteria
- Snapping works at any viewing angle and zoom level
- Snapping works consistently (same behavior every time)
- No noticeable slowdown when snapping is active
- Ideally <5ms per snap query (16ms per frame @ 60 FPS, snap is ~25% of frame budget)

## Relevant Code Files
- `radCAD/snapping_utils.py` — main snapping logic
- `radCAD/modal_core.py` — calls snapping every frame
- Blender API: `bpy.context.scene.ray_cast()`, `location_3d_to_region_2d()`, `bmesh`

## The Real Question
**Why does making snapping more reliable (raycast + checking actual geometry) make it slower, while making it less reliable (faster spatial queries) speeds it up? Where is the performance actually being lost in the common code path?**
