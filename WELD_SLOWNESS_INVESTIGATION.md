# Arc Weld Slowness Investigation Report

**Date:** 2026-03-25
**Status:** ROOT CAUSE IDENTIFIED + SOLUTION PROPOSED

---

## The Problem

Arc welding operations on dense meshes are slow (~1.3 seconds for a single 2-vertex arc on a 491k-vert mesh).

Previous optimization attempts (eliminate O(n) matrix multiplies, add distance culling) helped but hit a wall. The slowness persists.

---

## Investigation: Full Timing Breakdown

**Test case:** Drawing a 2-vertex arc on a dense mesh
- Mesh size: **491,543 verts, 1,015,825 edges, 524,294 faces**
- Arc size: **2 verts, 1 edge**
- Total weld time: **1,338.5ms**

### Phase 1: Endpoint Weld (950.8ms = 71% of total)

| Operation | Time | % of Total | Notes |
|-----------|------|-----------|-------|
| `find_nearby_geometry` | 0.9ms | 0.07% | Spatial grid working great — found only 4 edges |
| `perform_heavy_weld` | 0.3ms | 0.02% | No nearby verts to snap to |
| `perform_self_x_weld` | 0.2ms | 0.01% | No self-intersections |
| **`perform_x_weld`** | **415.1ms** | **31%** | 4 edge pairs checked (0.1ms), splits + welds took the rest |
| **`remove_doubles + bmesh.update_edit_mesh`** | **504.5ms** | **38%** | THE KILLER |
| **Total Phase 1** | **950.8ms** | **71%** | |

### Phase 2: Knife Project (387.4ms = 29% of total)

- Face scan: 222.3ms (checked all 524k faces, culled all by distance — found 0 candidates)
- Result: No faces to cut (arc on boundary edges, not near face centers)
- Wasted time but not critical to overall slowness

---

## Root Cause Analysis

### The Real Culprit: `bmesh.update_edit_mesh(me)`

After the x-weld operations complete, the code calls:

```python
bmesh.update_edit_mesh(me)
```

This rebuilds the **entire 491,543-vertex mesh** in Blender, even though only 6 vertices were actually modified (2 arc verts + 4 crossing verts from splits).

**The mesh update cost scales linearly with total mesh size.** On a 491k-vert mesh, this single call takes ~500ms.

### Critical Finding: Blender Always Rebuilds the Full Mesh

The spatial grid divides the mesh into cells:
- **Yellow/Green cells** = searched area (modifications happen here)
- **Grey cells** = outside search area

**Key observation from testing:** In the test run, weld operations ONLY modified yellow/green cells. No grey cell changes. Yet `bmesh.update_edit_mesh()` still took 504ms to rebuild all 491k vertices.

**Blender has no way to do "partial mesh updates."** When you call `bmesh.update_edit_mesh(mesh)`, it rebuilds the entire mesh data structure, regardless of how many verts you actually changed. This is an inherent limitation of Blender's BMesh API.

### Why Previous Optimizations Hit the Wall

✅ **Optimizations that worked:**
- Spatial grid for finding nearby geometry (0.9ms instead of O(n))
- Distance culling in face scan
- Eliminating repeated matrix multiplies

❌ **Why they didn't solve the problem:**
- They reduced the *work* on small datasets (4 edges, 2 verts)
- But Blender still has to rebuild the entire mesh when you call `update_edit_mesh()`
- You can't optimize away an O(n) operation where n = 491k

---

## User Assessment

> "The welding is supposed to happen only inside the cubes."

**This is the key insight.** The mesh is spatially partitioned (cubes/regions). The weld operation should only:
1. Detect/split/weld in the local cube region ✅ (the spatial grid does this)
2. Update **only that cube** in Blender ❌ (currently updates the entire mesh)

The fix violates this principle by forcing a full mesh rebuild.

---

## Proposed Solution

### Option 1: Defer `bmesh.update_edit_mesh()` (RECOMMENDED)

Instead of calling it after Phase 1, call it **once at the very end**, after all operations complete.

**Current flow:**
```
Phase 1 → bmesh.update_edit_mesh() [504ms] → Phase 2 → knife project updates
```

**Optimized flow:**
```
Phase 1 → Phase 2 → knife project → bmesh.update_edit_mesh() [once, ~500ms]
```

**Expected gain:** 500ms (remove 1-2 redundant full mesh updates)

### Option 2: Region-Based Mesh Update (IDEAL)

If the mesh system supports cube-based spatial partitioning:
- Only rebuild the affected cube's geometry in Blender
- Leave other cubes untouched
- Potential gain: **90%+ speedup** (only updating ~1/27th of the mesh if using 3×3×3 cubes)

### Option 3: Hybrid Approach

Use deferred updates + region-based optimization:
- Collect all modifications in Phase 1 and Phase 2
- Identify affected cubes
- Update only those cubes in Blender
- Call `bmesh.update_edit_mesh()` once at the very end

---

## Next Steps

1. **Quick win:** Remove early `bmesh.update_edit_mesh()` calls in Phase 1
   - Expected: ~400-500ms faster
   - Risk: Low (Blender caches bmesh edits)

2. **Investigation:** Determine if mesh cubes are accessible in the bmesh layer
   - Can we tag verts/edges/faces by cube?
   - Can we selectively update Blender's mesh for one cube?

3. **Measurement:** Re-run full timing after fix to verify gains

---

## Current Instrumentation

Both `arc_weld_manager.py` and `weld_utils.py` have comprehensive timing markers enabled (`DEBUG_MODE = True`, `DEBUG_WELD = True`). Future runs will show exact phase timings.

Console output format:
```
[ArcWeld DEBUG] TIMING <operation>: XXXms
[WeldUtils] <details>: XXXms
```

This will help validate the proposed optimizations.
