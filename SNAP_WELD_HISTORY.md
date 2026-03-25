# SNAP & WELD HISTORY LOG
**Permanent file — lives outside the git repo so it survives branch/checkout gymnastics.**
**Covers every iteration from `764e295` ("snapping fix screenspace opus attempt") through `1ffcdb3` ("snap efficiency fixed hopefully").**

---

## The Two Problems Being Solved

### Problem A — Compass not flushing to faces
When you snap to a vert or edge, the compass (arc 1pt tool) should orient itself flat against the nearest face. Instead it was using the smooth vertex normal — an averaged, blurry approximation — so the compass was tilted slightly wrong on curved/beveled surfaces.

### Problem B — Line tool weld only partially connecting
The line tool draws ONE long edge. When that edge crosses two or more target edges, only the first crossing got welded. The edge left over after the first split was abandoned and never processed. Arc tool was fine because it draws many tiny segments — each one crosses at most one target edge, so the bug never triggered.

---

## Iteration Log

---

### BASELINE — `764e295` — "snapping fix screenspace opus attempt"
**Date:** 2026-03-22 05:01
**File changed:** `radCAD/snapping_utils.py` (+40 / -21)

The starting gun. Opus rewrote the screen-space portion of the snapper — how 3D geometry gets projected into 2D screen coordinates for pixel-distance comparison. Before this, elements far from the cursor but close in screen-space were winning the snap contest unfairly. This commit straightened that out.

**Status after:** Snapping to verts/edges in screenspace works. Compass face-flush still broken.

---

### `a638fb9` — "snapping fix screenspace opus attempt 2"
**Date:** 2026-03-22 05:04
**File changed:** `radCAD/snapping_utils.py` (+3 / -3)

Six lines changed. Tiny cleanup pass — small numeric or condition tweaks from the first screenspace attempt. Like adjusting aim after the first shot.

---

### `6fd3f8e` — "snapping fix screenspace opus attempt 3"
**Date:** 2026-03-22 05:15
**File changed:** `radCAD/snapping_utils.py` (+3 / -1)

Four more lines. Another micro-adjustment. The screenspace snap logic settling into its final shape.

---

### `78d8e13` — "grid snapping overlay efficient cache attempt"
**Date:** 2026-03-22 11:34
**Author assist:** Claude Opus 4.6
**File changed:** `radCAD/snapping_utils.py` (+131 / -43)

Big one. The grid builder — `_build_grid()` — got a full numpy makeover.

**Before:** Pure Python loops. Every vert, edge, face processed one-by-one in Python. On a 327k-vert mesh this took hundreds of milliseconds per frame.

**After:** `foreach_get()` dumps all coordinates to a flat numpy array in one C call. Numpy does bulk matrix math to compute world coords. A new `_bin_to_grid()` helper converts the numpy arrays to Python lists in one shot (`.tolist()`), then does the grid-cell binning in pure Python — because numpy *element access* (`arr[i, 0]`) has huge per-call overhead, but bulk conversion is fast.

Think of it like: instead of individually hand-loading 327,000 boxes onto a truck one at a time, you use a forklift to dump the whole pallet at once, then sort them on the dock.

**Status after:** Grid builds fast. But face-flush still broken. Weld still broken for line tool.

---

### `a442998` — "grid snapping overlay efficient cache attempt 2"
**Date:** 2026-03-22 11:51
**Author assist:** Claude Opus 4.6
**Files changed:** `radCAD/snapping_utils.py` (+122 / -91), `radCAD/modal_core.py` (+10)

More numpy refactoring on the snap side. On the modal_core side: added timing instrumentation (`perf_counter` prints) inside `finish_modal()` and around `commit_arc_to_mesh()` so we could actually measure where time was going. Pure diagnostic scaffolding — no behavior change.

---

### `a76b316` — "grid snapping overlay efficient cache attempt 3"
**Date:** 2026-03-22 12:06
**Author assist:** Claude Haiku 4.5
**File changed:** `radCAD/modal_core.py` (+14)

**THIS IS WHERE THE COMPASS BUG GETS INTRODUCED (accidentally).**

To speed up `get_snap_data()`, added a raycast throttle cache: if `last_surface_hit` and `last_surface_normal` are already stored in state, skip the expensive `ray_cast()` call and just project the mouse ray onto the previously-hit plane.

Sounds smart. Is actually completely broken for compass use. The compass stopped responding to faces entirely — hovering over any surface did nothing, as if the geometry wasn't there. Broken from the moment you picked up the tool, on every surface, every time.

What happened: the throttle cache kicks in immediately on the first frame (since `last_surface_hit` gets populated fast), and from that point on `get_snap_data()` short-circuits before the geometry snap ever runs. The compass never gets a fresh normal. It's not stuck on the *wrong* face — it never reads any face at all.

The throttle saves ~300ms on dense meshes but completely kills compass face-flush.

**Status after:** Snapping fast. Compass totally dead.

---

### `b9cd639` — "efficient weld fix opus attempt"
**Date:** 2026-03-22 12:23
**Files changed:** `radCAD/arc_weld_manager.py` (+many), `radCAD/weld_utils.py` (+many)

First shot at the line tool weld bug. Opus rewrote `find_nearby_geometry()` with a KD-tree based approach and added timing instrumentation. Also rewrote `perform_x_weld()`. The deque idea starts forming here but isn't fully baked yet.

---

### `89e8f0e` — "efficient weld fix opus attempt 2"
**Date:** 2026-03-22 13:00
**Files changed:** `radCAD/arc_weld_manager.py` (+11 / -3), `radCAD/snapping_utils.py` (+7), `radCAD/weld_utils.py` (+21 / -6)

Iterating on the weld fix. Small adjustments to the weld logic and manager orchestration. Snapping_utils gets a minor change too (likely a side effect from a different investigation path).

---

### `60e1035` — "efficient weld fix opus attempt 3"
**Date:** 2026-03-22 13:06
**Files changed:** `radCAD/arc_weld_manager.py` (-large cleanup), `radCAD/weld_utils.py` (+/-large)

Major refactor pass. A lot of code removed from arc_weld_manager (-68 net), weld_utils reorganized. Getting closer to the correct mental model of the problem.

---

### `22b04b0` — "efficient weld fix opus attempt 4"
**Date:** 2026-03-22 13:34
**Files changed:** `radCAD/arc_weld_manager.py` (+100/-79), `radCAD/weld_utils.py` (+135/-79)

More iteration. Both files getting reshaped. The core insight (line tool creates one long edge that can cross multiple targets) is now understood, but the implementation is still being worked out.

---

### `aff2f09` — "efficient weld fix opus attempt 5"
**Date:** (between 22b04b0 and 32baeb2)
**File changed:** `radCAD/weld_utils.py`

Final pre-fix iteration. Getting very close. The structure is mostly right, execution details being nailed down.

---

### `32baeb2` — "snap efficiency fixed hopefully"
**Date:** 2026-03-22 16:53
**Author assist:** Claude Haiku 4.5
**File changed:** `radCAD/weld_utils.py` (+52 / -33)

**THE LINE TOOL WELD FIX LANDS.**

Two key changes:

**1. `safe_edge_split_vert_only` now returns a tuple `(new_vert, new_edge)`**

Before, it returned just the new vert. After splitting an arc edge at an intersection, the "remainder" of the original edge (the part after the split point) was silently abandoned. Nobody held a reference to it. It just... vanished.

```python
# BEFORE: new_vert only — remainder edge lost forever
new_vert = safe_edge_split_vert_only(bm, ae, ae.verts[0], s)

# AFTER: remainder tracked too
new_vert, new_arc_edge = safe_edge_split_vert_only(bm, ae, ae.verts[0], s)
```

**2. `perform_x_weld` uses a `deque` instead of a snapshot list**

```python
from collections import deque
arc_queue = deque(e for e in arc_edges if e.is_valid)
while arc_queue:
    ae = arc_queue.popleft()
    # ... find intersection, split ae at intersection point ...
    if new_arc_edge and new_arc_edge.is_valid:
        arc_queue.append(new_arc_edge)  # <-- remainder goes back in queue!
    break  # ae is now modified, grab next from queue
```

The deque is the conveyor belt. When a long edge gets cut in half, the second half gets put back on the belt. It'll get processed on the next loop iteration and can get cut again if it crosses another target edge. Now a line that crosses 10 target edges gets 10 intersection verts, not just 1.

**Why arc tool was fine:** Arc tool = many tiny segments. Each tiny segment crosses at most one target edge. The old code handled that fine. Line tool = one long edge across everything. The old code only ever processed the first crossing.

---

### `d6bb1e4` — "compass flush to face refix"
**Date:** 2026-03-22 17:32
**File changed:** `radCAD/modal_core.py` (-14 deletions)

**THE STALE RAYCAST CACHE GETS YANKED OUT.**

The throttle block added in `a76b316` gets removed entirely:

```python
# DELETED — this was the compass-breaking culprit
last_hit = state.get("last_surface_hit")
last_norm = state.get("last_surface_normal")
if last_hit is not None and last_norm is not None:
    hit_plane = intersect_line_plane(...)
    return hit_plane, last_norm  # <-- stale normal from previous face!
```

Performance optimization, yes. But it returned the normal from wherever you *were* rather than wherever you *are*. The compass was reading yesterday's weather.

Removing it means every frame does a fresh `ray_cast()`. Slower on dense meshes, but at least the compass points the right direction.

---

### `1ffcdb3` — "snap efficiency fixed hopefully"
**Date:** 2026-03-22 17:34
**File changed:** `radCAD/snapping_utils.py` (+42)

**Added `query_nearby_from_cache()` — a weld-side efficiency utility.**

This adds a new function that lets `find_nearby_geometry()` in the weld pipeline query "what verts and edges are near this arc?" by reusing the already-built snap grid cache (numpy arrays, AABB lookup) instead of doing a fresh KDTree scan every time a weld runs.

```python
def query_nearby_from_cache(obj, aabb_min, aabb_max, expand_cells=1):
    # Looks up the snap grid for obj, does numpy AABB mask,
    # returns (v_indices, e_indices) — no Python loops, no KDTree build
```

Snap normal for the compass comes from `wnm` in the grid — which for verts is the smooth vertex normal (`mesh.vertices.foreach_get('normal', ...)`), and for edges is the average of the two endpoint normals. The compass face-flush was already fixed by removing the stale cache in `d6bb1e4` — this commit is a weld efficiency win, not a compass fix.

**⚠️ NOTE:** The history file originally said this added a `link_faces` face-normal fix. That was wrong. The real face-normal the compass sees is the smooth vertex normal from the grid. The compass fix was entirely in `d6bb1e4`.

---

## Summary Table

| Commit | What Changed | Problem Targeted | Result |
|--------|-------------|-----------------|--------|
| 764e295 | snapping_utils: screenspace snap rewrite | snap accuracy | ✅ Snap improved |
| a638fb9 | snapping_utils: minor tweaks | snap accuracy | iterating |
| 6fd3f8e | snapping_utils: minor tweaks | snap accuracy | iterating |
| 78d8e13 | snapping_utils: numpy grid builder | performance | ✅ Grid fast |
| a442998 | snapping_utils + modal_core timing | performance | diagnostics only |
| a76b316 | modal_core: raycast throttle cache | performance | ⚠️ Breaks compass |
| b9cd639 | weld_utils + arc_weld_manager: weld rewrite | line weld bug | iterating |
| 89e8f0e | weld + manager + snapping: fixes | line weld bug | iterating |
| 60e1035 | weld + manager: refactor | line weld bug | iterating |
| 22b04b0 | weld + manager: more iteration | line weld bug | iterating |
| aff2f09 | weld: final pre-fix iteration | line weld bug | iterating |
| 32baeb2 | weld: **deque + tuple return** | line weld bug | ✅ FIXED |
| d6bb1e4 | modal_core: **remove stale cache** | compass face-flush | ✅ FIXED |
| 1ffcdb3 | snapping_utils: **query_nearby_from_cache** | weld efficiency | ✅ Weld faster |

---

## Architecture — What Each File Does

```
radCAD/
├── modal_core.py           — The event loop. Owns the state dict, calls get_snap_data()
│                             every mouse-move, passes (snap_pt, snap_normal) to active tool.
│
├── snapping_utils.py       — Geometry snapper. Builds a spatial grid (numpy) of all
│                             verts/edges/faces in worldspace. On each frame, finds the
│                             closest element to the mouse cursor in screenspace pixels.
│                             Returns (3D point, normal). Normal = smooth vertex normal
│                             from mesh.vertices.foreach_get('normal').
│
├── arc_weld_manager.py     — Weld orchestrator. Called after an arc/line is committed.
│                             Runs the weld pipeline in order:
│                               1. find_nearby_geometry()  — what's close enough to weld?
│                               2. perform_heavy_weld()    — snap arc endpoints to target verts
│                               3. perform_self_x_weld()   — arc crossing itself
│                               4. perform_x_weld()        — arc crossing target edges
│                               5. remove_doubles()        — merge overlapping verts
│                               6. knife_project()         — cut faces along arc path
│
├── weld_utils.py           — Weld math. All the BMesh operations.
│   ├── safe_edge_split_vert_only()  — splits an edge at a parameter t, returns (new_vert, new_edge)
│   ├── find_nearby_geometry()       — KDTree/grid search for target verts+edges near arc
│   ├── perform_heavy_weld()         — moves arc endpoints onto nearby target verts
│   ├── perform_x_weld()             — splits edges at intersection points (deque-based)
│   └── perform_self_x_weld()        — same but arc crossing itself
│
└── operators/
    ├── base_tool.py        — Base class for all drawing tools. Owns compass orientation:
    │                         update_initial_plane(snap_normal) → self.Zp = snap_normal
    │                         → orthonormal_basis_from_normal(Zp) → self.Xp, self.Yp
    │                         The compass plane IS Zp. Lock with L key.
    │
    ├── arc_tools.py        — Arc tool operators (1pt, 2pt, 3pt, etc.)
    └── line_tools.py       — Line tool operator
```

---

## The Snap Normal Data Flow (compass face-flush pipeline)

```
Mouse moves over mesh
        ↓
snapping_utils.snap_to_mesh_components()
  → finds closest vert or edge in screenspace
  → returns (3D world position, normal)
     • vert snap:  normal = smooth vertex normal (averaged from adjacent faces)
     • edge snap:  normal = average of the two endpoint normals
        ↓
modal_core.get_snap_data()
  → returns (snap_pt, snap_normal) to the active tool's update()
        ↓
base_tool.update_initial_plane(snap_point, snap_normal)
  → self.Zp = snap_normal
  → self.Xp, self.Yp = orthonormal_basis_from_normal(self.Zp)
        ↓
Compass renders using self.Zp as its "up" / face-normal axis
```

**The bug that broke this (a76b316):** `get_snap_data()` short-circuited with a cached `last_surface_normal` before running snap, so it returned a stale normal from the previous frame. The compass was stuck pointing at where you were, not where you are.

**The fix (d6bb1e4):** Delete the cache block. Every frame runs `ray_cast()` fresh.

---

## Wrong Turns — Don't Go Down These Roads Again

### ❌ Haiku's wrong diagnosis: `is_tgt_internal` blind spot
Haiku said the weld bug was caused by a missing `else` branch for `is_tgt_internal` — when a line endpoint lands near a target edge's endpoint (not its middle), nothing fires. This is a real edge case but was NOT the bug causing the visible symptom (middle of line not connecting). The actual bug was the remainder edge being abandoned.

### ❌ Haiku's wrong fix: extra `break` in the wrong place
Haiku tried adding `break` after `cuts += 1` and an `else` branch. Both were wrong — the break was in the wrong scope and the else handled a non-existent case.

### ❌ Opus's wrong compass fix: changing `do_faces`
Opus tried fixing the compass by changing `do_faces=False` to `do_faces=state.get("snap_faces", True)` in the `snap_to_mesh_components()` call inside `get_snap_data()`. This was wrong — `do_faces=False` is intentional (face snapping is separate from compass orientation). The real problem was upstream in the raycast throttle cache. This change was reverted.

### ❌ The raycast throttle cache (a76b316)
Seemed like a clever optimization: skip the expensive `ray_cast()` if we already have a recent surface hit. Broke compass face-flush completely — hovering over any face did absolutely nothing, as if the geometry wasn't there. Broken on every surface, from the first frame. The cache kicks in immediately and short-circuits before the geometry snap ever fires, so the compass never gets a normal at all. Deleted in `d6bb1e4`.

---

## BMesh Gotcha — `edge_split` tuple order is not guaranteed

`bmesh.utils.edge_split(edge, vert, fac)` returns a `(BMVert, BMEdge)` tuple, BUT depending on which vert you split from, the order can flip — sometimes you get `(BMEdge, BMVert)` instead.

The current code handles this defensively:

```python
res = bmesh.utils.edge_split(edge, split_from_vert, fac)
if isinstance(res, tuple) and len(res) == 2:
    new_vert, new_edge = res
    if isinstance(new_vert, bmesh.types.BMVert):
        new_vert.select = True
        return new_vert, new_edge
    if isinstance(new_edge, bmesh.types.BMVert):   # <-- handles flipped tuple
        new_edge.select = True
        return new_edge, new_vert
```

**Never assume the tuple order. Always isinstance-check which slot is the vert.**

1. **Splitting an edge gives you TWO edges, not one.** If you only keep the new vert and throw away the remainder edge, you've abandoned half the work. Always track both.

2. **Caching a raycast normal is dangerous.** Normals are directional — they describe *where* you are, not just *what* you hit. A cached normal from 3 frames ago is a lie about your current position.

3. **Arc tool ≠ line tool.** Arc tool = many small segments, each safely crosses at most one target. Line tool = one long segment that can cross many. Algorithms that work for the first will silently fail for the second.

4. **numpy element access is slow, numpy bulk ops are fast.** Don't do `arr[i, 0]` in a loop. Do `.tolist()` first, then loop over Python lists.
