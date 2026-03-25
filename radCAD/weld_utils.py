import bmesh
import math
from mathutils import Vector, geometry
from mathutils.kdtree import KDTree

EPS = 1e-6
DEBUG_WELD = False  # <--- Shhh. Quiet mode.

def dbg(msg):
    if DEBUG_WELD:
        print(f"[WeldUtils] {msg}")

# --- GEOMETRY HELPERS ---

def closest_point_on_segment(a: Vector, b: Vector, p: Vector):
    ab = b - a
    ab_len2 = ab.length_squared
    if ab_len2 <= EPS:
        return a.copy(), 0.0
    t = (p - a).dot(ab) / ab_len2
    if t < 0.0: t = 0.0
    elif t > 1.0: t = 1.0
    c = a + t * ab
    return c, float(t)

def closest_points_on_segments(p1, q1, p2, q2):
    # Returns c1, c2, s, t
    res = geometry.intersect_line_line(p1, q1, p2, q2)
    if res is None:
        return p1, p2, 0.0, 0.0
    
    c1, c2 = res
    
    vec1 = q1 - p1
    len1 = vec1.length_squared
    s = (c1 - p1).dot(vec1) / len1 if len1 > EPS else 0.0
    
    vec2 = q2 - p2
    len2 = vec2.length_squared
    t = (c2 - p2).dot(vec2) / len2 if len2 > EPS else 0.0
    
    s = max(0.0, min(1.0, s))
    t = max(0.0, min(1.0, t))
    
    c1 = p1 + s * vec1
    c2 = p2 + t * vec2
    
    return c1, c2, s, t

def safe_edge_split_vert_only(bm, edge, split_from_vert, fac):
    fac = float(fac)
    try:
        res = bmesh.utils.edge_split(edge, split_from_vert, fac)
        if isinstance(res, tuple) and len(res) == 2:
            a, b = res
            if isinstance(a, bmesh.types.BMVert):
                a.select = True
                return a
            if isinstance(b, bmesh.types.BMVert):
                b.select = True
                return b
    except Exception:
        pass
    return None

def edge_between(v1, v2):
    """Find the edge connecting two verts, or None."""
    for e in v1.link_edges:
        if v2 in e.verts:
            return e
    return None

def _find_next_edge(new_vert, curr_left, curr_right):
    """After a split, find the edge from new_vert toward curr_right.
    Falls back to dot-product search if edge_between fails."""
    nxt = edge_between(new_vert, curr_right)
    if nxt is not None:
        return nxt
    # Fallback: find the edge from new_vert pointing most toward curr_right
    best_e = None
    best_dot = -1e18
    tgt = curr_right.co - new_vert.co
    tgt_n = tgt.normalized() if tgt.length > 1e-12 else Vector((1, 0, 0))
    for e2 in new_vert.link_edges:
        other = e2.other_vert(new_vert)
        d = other.co - new_vert.co
        if d.length <= 1e-12:
            continue
        sc = d.normalized().dot(tgt_n)
        if sc > best_dot:
            best_dot = sc
            best_e = e2
    return best_e

def _split_edge_at_cuts(bm, edge, cuts, mw_inv, crossing_verts, weld_targetmap):
    """Split a single edge at multiple cut points (sorted, renormalized).

    cuts: list of (param, world_pos, rounded_key) — must be pre-sorted by param.
    crossing_verts: dict of rounded_key -> BMVert (crossing vert to weld to).
    weld_targetmap: dict to append new_vert -> crossing_vert entries to.
    Returns number of successful splits.
    """
    if not edge.is_valid:
        return 0

    # Dedup by key and by very close parameters
    seen_keys = set()
    uniq = []
    for param, w_pos, rk in cuts:
        if rk in seen_keys:
            continue
        seen_keys.add(rk)
        if uniq and (param - uniq[-1][0]) < EPS * 100:
            continue
        uniq.append((param, w_pos, rk))

    was_selected = edge.select
    base_v0 = edge.verts[0]
    curr_edge = edge
    curr_left = base_v0
    prev_t = 0.0
    split_count = 0

    for (t_abs, w_pos, rk) in uniq:
        if not curr_edge or not curr_edge.is_valid:
            break

        # Skip cuts too close to endpoints
        if t_abs <= 1e-6 or t_abs >= 1.0 - 1e-6:
            continue

        # Renormalize parameter for remaining edge portion
        denom = max(1e-16, (1.0 - prev_t))
        fac_local = (t_abs - prev_t) / denom
        fac_local = max(1e-12, min(1.0 - 1e-12, fac_local))

        # Identify curr_right BEFORE splitting
        v0c, v1c = curr_edge.verts
        curr_right = v1c if v0c is curr_left else v0c

        # Split
        new_vert = safe_edge_split_vert_only(bm, curr_edge, curr_left, float(fac_local))
        if new_vert is None:
            break

        # Snap to exact intersection position
        new_vert.co = mw_inv @ w_pos

        # Schedule weld to crossing vert (if one exists for this key)
        cv = crossing_verts.get(rk)
        if cv and cv.is_valid and new_vert is not cv:
            weld_targetmap[new_vert] = cv

        # Find the continuation edge
        nxt_edge = _find_next_edge(new_vert, curr_left, curr_right)
        if nxt_edge is None:
            break

        # Propagate selection: if the original edge was selected (arc edge),
        # keep all split fragments selected so Phase 2 knife cutter sees them
        if was_selected and nxt_edge.is_valid:
            nxt_edge.select = True

        curr_left = new_vert
        curr_edge = nxt_edge
        prev_t = t_abs
        split_count += 1

    return split_count

# --- SEARCH HELPERS ---

def find_nearby_geometry(bm, arc_verts, radius, mw, obj=None):
    arc_vert_set = set(arc_verts)

    # Build arc bounding box in world space
    min_v = Vector((float('inf'),)*3)
    max_v = Vector((float('-inf'),)*3)
    valid_arc = False
    arc_world_pts = []
    for av in arc_verts:
        if not av.is_valid: continue
        valid_arc = True
        p = mw @ av.co
        arc_world_pts.append(p)
        min_v.x = min(min_v.x, p.x); min_v.y = min(min_v.y, p.y); min_v.z = min(min_v.z, p.z)
        max_v.x = max(max_v.x, p.x); max_v.y = max(max_v.y, p.y); max_v.z = max(max_v.z, p.z)

    if not valid_arc:
        return [], []

    margin = max(radius * 2.0, 0.01)
    search_min = min_v - Vector((margin, margin, margin))
    search_max = max_v + Vector((margin, margin, margin))

    # Local-space AABB for fallback paths (avoids per-element matrix multiply)
    local_min = Vector((float('inf'),)*3)
    local_max = Vector((float('-inf'),)*3)
    for av in arc_verts:
        if not av.is_valid: continue
        co = av.co
        local_min.x = min(local_min.x, co.x); local_min.y = min(local_min.y, co.y); local_min.z = min(local_min.z, co.z)
        local_max.x = max(local_max.x, co.x); local_max.y = max(local_max.y, co.y); local_max.z = max(local_max.z, co.z)
    local_smin = local_min - Vector((margin, margin, margin))
    local_smax = local_max + Vector((margin, margin, margin))

    # Try spatial grid for O(nearby) instead of O(all edges)
    from . import snapping_utils
    grid = snapping_utils.get_spatial_grid()
    if not grid.cells and obj is not None:
        grid.build(obj, bm)
    use_grid = bool(grid.cells)

    # 1. Verts
    target_verts = []
    r2 = (radius * 2.0) ** 2

    if use_grid:
        nearby_cells = grid.get_cells_in_bounds(search_min, search_max)
        candidate_vidxs = set()
        for ck in nearby_cells:
            candidate_vidxs.update(grid.cells[ck].get("vert_idxs", []))

        bm.verts.ensure_lookup_table()
        for idx in candidate_vidxs:
            if idx >= len(bm.verts): continue
            v = bm.verts[idx]
            if v.hide or v in arc_vert_set: continue
            v_w = mw @ v.co
            for ap in arc_world_pts:
                if (v_w - ap).length_squared <= r2:
                    target_verts.append(v)
                    break
    else:
        bg_verts = [v for v in bm.verts if v not in arc_vert_set and not v.hide]
        if bg_verts:
            # Local-space KDTree — no matrix multiply per vert
            kd = KDTree(len(bg_verts))
            for i, v in enumerate(bg_verts):
                kd.insert(v.co, i)
            kd.balance()
            found_v_idxs = set()
            for av in arc_verts:
                if not av.is_valid: continue
                for (co, index, dist) in kd.find_range(av.co, radius * 2.0):
                    found_v_idxs.add(index)
            target_verts = [bg_verts[i] for i in found_v_idxs]

    # 2. Edges
    target_edges = set()

    if use_grid:
        candidate_eidxs = set()
        for ck in nearby_cells:
            candidate_eidxs.update(grid.cells[ck].get("edge_idxs", []))

        bm.edges.ensure_lookup_table()
        for idx in candidate_eidxs:
            if idx >= len(bm.edges): continue
            e = bm.edges[idx]
            if e.hide: continue
            if e.verts[0] in arc_vert_set and e.verts[1] in arc_vert_set: continue

            p1 = mw @ e.verts[0].co
            p2 = mw @ e.verts[1].co

            e_min_x = min(p1.x, p2.x); e_max_x = max(p1.x, p2.x)
            if e_max_x < search_min.x or e_min_x > search_max.x: continue
            e_min_y = min(p1.y, p2.y); e_max_y = max(p1.y, p2.y)
            if e_max_y < search_min.y or e_min_y > search_max.y: continue
            e_min_z = min(p1.z, p2.z); e_max_z = max(p1.z, p2.z)
            if e_max_z < search_min.z or e_min_z > search_max.z: continue

            target_edges.add(e)

        dbg(f"Grid search: {len(candidate_eidxs)} edge candidates from {len(nearby_cells)} cells -> {len(target_edges)} hits")
    else:
        # Local-space AABB check — no matrix multiply per edge
        for e in bm.edges:
            if e.hide: continue
            if e.verts[0] in arc_vert_set and e.verts[1] in arc_vert_set: continue

            p1 = e.verts[0].co
            p2 = e.verts[1].co

            e_min_x = min(p1.x, p2.x); e_max_x = max(p1.x, p2.x)
            if e_max_x < local_smin.x or e_min_x > local_smax.x: continue
            e_min_y = min(p1.y, p2.y); e_max_y = max(p1.y, p2.y)
            if e_max_y < local_smin.y or e_min_y > local_smax.y: continue
            e_min_z = min(p1.z, p2.z); e_max_z = max(p1.z, p2.z)
            if e_max_z < local_smin.z or e_min_z > local_smax.z: continue

            target_edges.add(e)

    return list(set(target_verts)), list(target_edges)


def perform_heavy_weld(bm, arc_verts, target_geom, radius, mw):
    moves = 0
    tgt_verts, tgt_edges = target_geom
    mw_inv = mw.inverted()
    
    for av in arc_verts:
        if not av.is_valid: continue
        p_w = mw @ av.co
        
        best_dist = radius
        best_pos = None
        
        # Check Verts
        for tv in tgt_verts:
            d = ((mw @ tv.co) - p_w).length
            if d <= best_dist:
                best_dist = d
                best_pos = tv.co
                
        if best_pos:
            av.co = best_pos
            moves += 1
            continue
            
        # Check Edges (Snap to line)
        for te in tgt_edges:
            a_w = mw @ te.verts[0].co
            b_w = mw @ te.verts[1].co
            c_w, t = closest_point_on_segment(a_w, b_w, p_w)
            d = (p_w - c_w).length
            
            if d <= best_dist:
                if 0.01 < t < 0.99:
                    best_dist = d
                    av.co = mw_inv @ c_w
                    moves += 1
                
    return moves

def perform_x_weld(bm, arc_edges, target_edges, radius, mw):
    """Detect and split at ALL x-crossings between arc and target edges.

    Ported from rCAD's collect-then-split approach:
    1. Find ALL intersections first (no modifications)
    2. Create crossing verts for X-crossings
    3. Group cuts by edge, sort by parameter
    4. Split in order with renormalization
    5. Weld split verts to crossing verts
    """
    r2 = radius * radius
    mw_inv = mw.inverted()
    ROUND = 6

    # ---- PHASE 1: Collect ALL intersections (no modifications yet) ----
    hits = []
    arc_edges_safe = [e for e in arc_edges if e.is_valid]

    for ae in arc_edges_safe:
        if not ae.is_valid:
            continue
        p1a = mw @ ae.verts[0].co
        p1b = mw @ ae.verts[1].co

        for te in target_edges:
            if not te.is_valid:
                continue
            if len(set(ae.verts) & set(te.verts)) > 0:
                continue

            p2a = mw @ te.verts[0].co
            p2b = mw @ te.verts[1].co

            c1, c2, s, t = closest_points_on_segments(p1a, p1b, p2a, p2b)
            if (c1 - c2).length_squared >= r2:
                continue

            is_arc_int = (EPS < s < (1.0 - EPS))
            is_tgt_int = (EPS < t < (1.0 - EPS))

            if not is_tgt_int:
                continue

            intersect_w = (c1 + c2) * 0.5
            rk = (round(intersect_w.x, ROUND), round(intersect_w.y, ROUND), round(intersect_w.z, ROUND))
            hits.append({
                'ae': ae, 'te': te, 's': s, 't': t,
                'w_pos': intersect_w, 'rk': rk,
                'arc_internal': is_arc_int,
            })

    if not hits:
        return 0

    dbg(f"X-weld: collected {len(hits)} intersection(s)")

    # ---- PHASE 2: Create crossing verts for X-crossings ----
    crossing_verts = {}  # rounded_key -> BMVert
    for h in hits:
        if h['arc_internal'] and h['rk'] not in crossing_verts:
            cv = bm.verts.new(mw_inv @ h['w_pos'])
            cv.select = True
            crossing_verts[h['rk']] = cv

    if crossing_verts:
        bm.verts.index_update()
        bm.verts.ensure_lookup_table()

    # ---- PHASE 3: Group cuts by edge ----
    edge_cuts = {}   # edge -> [(param, world_pos, key), ...]
    t_junctions = [] # T-junction hits (arc endpoint near target middle)

    for h in hits:
        # Target edge always gets a cut
        te = h['te']
        edge_cuts.setdefault(te, []).append((h['t'], h['w_pos'], h['rk']))

        if h['arc_internal']:
            # X-crossing: arc edge also gets a cut
            ae = h['ae']
            edge_cuts.setdefault(ae, []).append((h['s'], h['w_pos'], h['rk']))
        else:
            # T-junction: arc endpoint moves, no split needed on arc
            t_junctions.append(h)

    # ---- PHASE 4: Split each edge (sorted, renormalized) ----
    total_cuts = 0
    weld_targetmap = {}

    for edge, cuts in edge_cuts.items():
        cuts.sort(key=lambda x: x[0])
        total_cuts += _split_edge_at_cuts(bm, edge, cuts, mw_inv, crossing_verts, weld_targetmap)

    # ---- PHASE 5: Handle T-junctions (move arc endpoints) ----
    for h in t_junctions:
        ae = h['ae']
        if not ae.is_valid:
            continue
        vert_idx = 0 if h['s'] < 0.5 else 1
        ae.verts[vert_idx].co = mw_inv @ h['w_pos']

    # ---- PHASE 6: Weld split verts to crossing verts ----
    if weld_targetmap:
        valid_map = {k: v for k, v in weld_targetmap.items()
                     if k.is_valid and v.is_valid}
        if valid_map:
            bmesh.ops.weld_verts(bm, targetmap=valid_map)
            dbg(f"X-weld: welded {len(valid_map)} vert(s)")

    dbg(f"X-weld: {total_cuts} split(s) total")
    return total_cuts

def perform_self_x_weld(bm, edges, radius, mw):
    """Detects and cuts segments within the SAME drawing that cross each other.
    Uses collect-then-split for correct multi-crossing handling."""
    r2 = radius * radius
    mw_inv = mw.inverted()
    ROUND = 6

    edges_list = [e for e in edges if e.is_valid]

    # ---- PHASE 1: Collect ALL self-intersections ----
    edge_cuts = {}      # edge -> [(param, world_pos, key), ...]
    crossing_keys = {}  # key -> world_pos

    for i in range(len(edges_list)):
        e1 = edges_list[i]
        if not e1.is_valid:
            continue
        p1a = mw @ e1.verts[0].co
        p1b = mw @ e1.verts[1].co

        for j in range(i + 1, len(edges_list)):
            e2 = edges_list[j]
            if not e2.is_valid:
                continue
            if set(e1.verts) & set(e2.verts):
                continue

            p2a = mw @ e2.verts[0].co
            p2b = mw @ e2.verts[1].co

            c1, c2, s, t = closest_points_on_segments(p1a, p1b, p2a, p2b)
            if (c1 - c2).length_squared >= r2:
                continue

            intersect_w = (c1 + c2) * 0.5
            rk = (round(intersect_w.x, ROUND), round(intersect_w.y, ROUND), round(intersect_w.z, ROUND))
            crossing_keys[rk] = intersect_w

            if 0.001 < s < 0.999:
                edge_cuts.setdefault(e1, []).append((s, intersect_w, rk))

            if 0.001 < t < 0.999:
                edge_cuts.setdefault(e2, []).append((t, intersect_w, rk))

    if not edge_cuts:
        return 0

    # ---- PHASE 2: Create crossing verts ----
    crossing_verts = {}
    for rk, w_pos in crossing_keys.items():
        cv = bm.verts.new(mw_inv @ w_pos)
        cv.select = True
        crossing_verts[rk] = cv

    bm.verts.index_update()
    bm.verts.ensure_lookup_table()

    # ---- PHASE 3: Split edges (sorted, renormalized) ----
    total_cuts = 0
    weld_targetmap = {}

    for edge, cuts in edge_cuts.items():
        cuts.sort(key=lambda x: x[0])
        total_cuts += _split_edge_at_cuts(bm, edge, cuts, mw_inv, crossing_verts, weld_targetmap)

    # ---- PHASE 4: Weld ----
    if weld_targetmap:
        valid_map = {k: v for k, v in weld_targetmap.items()
                     if k.is_valid and v.is_valid}
        if valid_map:
            bmesh.ops.weld_verts(bm, targetmap=valid_map)

    return total_cuts
