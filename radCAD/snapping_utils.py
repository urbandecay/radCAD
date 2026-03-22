# snapping_utils.py  —  numpy-accelerated grid build, zero-alloc snap hot path

import bmesh
import numpy as np
from mathutils import Vector
from bpy_extras import view3d_utils

ELEMENT_SNAP_RADIUS_PX = 15.0

# Grid cache — keyed on mesh identity + topology counts + grid_size
_snap_cache = {}

# Debug visualization state (updated every snap call)
_debug = {
    'nearby_cells': [],   # list of (cx, cy, cz) cell coords
    'query_cell': None,   # (cx, cy, cz) of the cursor's cell
    'gs': 1.0,            # current grid size
    'all_cells': set(),   # all occupied cells (union of vg+eg+fg)
}

# Empty grid constant
_EMPTY_GRID = {'wco': np.empty((0, 3)), 'wnm': np.empty((0, 3)),
               'idx': np.empty(0, dtype=np.int64), 'cells': {}}


def _bin_sorted(indices, wco, wnm, gs):
    """Bin elements into cells using numpy sort. No Python loop over elements.
    Returns {'wco': Nx3, 'wnm': Nx3, 'idx': N, 'cells': {(cx,cy,cz): (start,end)}}
    """
    if len(indices) == 0:
        return dict(_EMPTY_GRID)

    # Compute cell coords (pure numpy)
    cx = np.floor(wco[:, 0] / gs).astype(np.int64)
    cy = np.floor(wco[:, 1] / gs).astype(np.int64)
    cz = np.floor(wco[:, 2] / gs).astype(np.int64)

    # Hash cells to single int for sorting (use large primes to avoid collisions)
    cell_hash = cx * np.int64(73856093) ^ cy * np.int64(19349663) ^ cz * np.int64(83492791)

    # Sort everything by cell hash
    order = np.argsort(cell_hash)
    sorted_wco = wco[order]
    sorted_wnm = wnm[order]
    sorted_idx = indices[order]
    sorted_cx = cx[order]
    sorted_cy = cy[order]
    sorted_cz = cz[order]
    sorted_hash = cell_hash[order]

    # Find cell boundaries (where hash changes)
    n = len(sorted_hash)
    if n == 1:
        cells = {(int(sorted_cx[0]), int(sorted_cy[0]), int(sorted_cz[0])): (0, 1)}
    else:
        diff = np.diff(sorted_hash)
        breaks = np.where(diff != 0)[0] + 1
        starts = np.concatenate(([0], breaks))
        ends = np.concatenate((breaks, [n]))

        # Build tiny cell dict (one entry per occupied cell, not per element)
        cells = {}
        for i in range(len(starts)):
            s = int(starts[i])
            cell = (int(sorted_cx[s]), int(sorted_cy[s]), int(sorted_cz[s]))
            cells[cell] = (s, int(ends[i]))

    return {'wco': sorted_wco, 'wnm': sorted_wnm, 'idx': sorted_idx, 'cells': cells}


def _build_grid(obj, bm, mw, gs):
    """Build or return cached spatial grid using foreach_get + numpy.
    No Python loops over elements — all heavy work is C/numpy.
    """
    import time as _t
    key = (id(obj.data), len(bm.verts), len(bm.edges), len(bm.faces), gs)

    # ALWAYS clean stale entries BEFORE cache check (handles undo)
    mesh_id = id(obj.data)
    stale = [k for k in _snap_cache if k[0] == mesh_id and k != key]
    for k in stale:
        del _snap_cache[k]

    cached = _snap_cache.get(key)
    if cached is not None:
        return cached

    _t0 = _t.perf_counter()
    obj.update_from_editmode()
    _t1 = _t.perf_counter()
    mesh = obj.data

    mw_np = np.array(mw, dtype=np.float64).reshape(4, 4).T
    rot = mw.to_3x3().normalized()
    rot_np = np.array(rot, dtype=np.float64).reshape(3, 3).T

    n_verts = len(mesh.vertices)
    n_edges = len(mesh.edges)
    n_faces = len(mesh.polygons)

    # ── VERTS ──
    _t2 = _t.perf_counter()
    if n_verts > 0:
        co_flat = np.empty(n_verts * 3, dtype=np.float64)
        nm_flat = np.empty(n_verts * 3, dtype=np.float64)
        hide_v = np.empty(n_verts, dtype=bool)
        mesh.vertices.foreach_get('co', co_flat)
        mesh.vertices.foreach_get('normal', nm_flat)
        mesh.vertices.foreach_get('hide', hide_v)

        co = co_flat.reshape(n_verts, 3)
        nm = nm_flat.reshape(n_verts, 3)

        full_wco = co @ mw_np[:3, :3].T + mw_np[3, :3]
        full_wnm = nm @ rot_np.T
        lengths = np.linalg.norm(full_wnm, axis=1, keepdims=True)
        lengths[lengths < 1e-8] = 1.0
        full_wnm /= lengths

        vis = ~hide_v
        if not vis.all():
            v_wco = full_wco[vis]; v_wnm = full_wnm[vis]
            v_idx = np.where(vis)[0].astype(np.int64)
        else:
            v_wco = full_wco; v_wnm = full_wnm
            v_idx = np.arange(n_verts, dtype=np.int64)

        vg = _bin_sorted(v_idx, v_wco, v_wnm, gs)
    else:
        full_wco = np.empty((0, 3)); full_wnm = np.empty((0, 3))
        vg = dict(_EMPTY_GRID)

    # ── EDGES (midpoints) ──
    if n_edges > 0 and n_verts > 0:
        ev = np.empty(n_edges * 2, dtype=np.int32)
        hide_e = np.empty(n_edges, dtype=bool)
        mesh.edges.foreach_get('vertices', ev)
        mesh.edges.foreach_get('hide', hide_e)
        ev = ev.reshape(n_edges, 2)

        mid_wco = (full_wco[ev[:, 0]] + full_wco[ev[:, 1]]) * 0.5
        mid_wnm = (full_wnm[ev[:, 0]] + full_wnm[ev[:, 1]]) * 0.5
        ml = np.linalg.norm(mid_wnm, axis=1, keepdims=True)
        ml[ml < 1e-8] = 1.0
        mid_wnm /= ml

        vis_e = ~hide_e
        if not vis_e.all():
            mid_wco = mid_wco[vis_e]; mid_wnm = mid_wnm[vis_e]
            e_idx = np.where(vis_e)[0].astype(np.int64)
        else:
            e_idx = np.arange(n_edges, dtype=np.int64)

        eg = _bin_sorted(e_idx, mid_wco, mid_wnm, gs)
    else:
        eg = dict(_EMPTY_GRID)

    # ── FACES ──
    if n_faces > 0:
        fc_flat = np.empty(n_faces * 3, dtype=np.float64)
        fn_flat = np.empty(n_faces * 3, dtype=np.float64)
        hide_f = np.empty(n_faces, dtype=bool)
        mesh.polygons.foreach_get('center', fc_flat)
        mesh.polygons.foreach_get('normal', fn_flat)
        mesh.polygons.foreach_get('hide', hide_f)

        fc = fc_flat.reshape(n_faces, 3)
        fn = fn_flat.reshape(n_faces, 3)
        wfc = fc @ mw_np[:3, :3].T + mw_np[3, :3]
        wfn = fn @ rot_np.T
        fl = np.linalg.norm(wfn, axis=1, keepdims=True)
        fl[fl < 1e-8] = 1.0
        wfn /= fl

        vis_f = ~hide_f
        if not vis_f.all():
            wfc = wfc[vis_f]; wfn = wfn[vis_f]
            f_idx = np.where(vis_f)[0].astype(np.int64)
        else:
            f_idx = np.arange(n_faces, dtype=np.int64)

        fg = _bin_sorted(f_idx, wfc, wfn, gs)
    else:
        fg = dict(_EMPTY_GRID)

    _t3 = _t.perf_counter()
    result = (vg, eg, fg)
    _snap_cache[key] = result
    print(f"  [GRID BUILD] update_from_editmode={(_t1-_t0)*1000:.0f}ms  foreach+numpy+sort={(_t3-_t2)*1000:.0f}ms  total={(_t3-_t0)*1000:.0f}ms  verts={n_verts} edges={n_edges} faces={n_faces}")
    return result


def snap_to_mesh_components(ctx, obj, x, y, max_px=ELEMENT_SNAP_RADIUS_PX,
                            do_verts=True, do_edges=True,
                            do_edge_center=True, do_face_center=True,
                            **kwargs):
    """Returns (world_co, world_normal) or None.
    Pure-math hot path: no scene raycasts, no Vector allocations in
    the inner loops, no string comparisons, adaptive cell search.
    """
    if obj is None or obj.type != 'MESH':
        return None

    import time as _time
    _ta = _time.perf_counter()

    region, rv3d = ctx.region, ctx.region_data
    mx, my = float(x), float(y)
    bm = bmesh.from_edit_mesh(obj.data)

    _tb = _time.perf_counter()

    mw = obj.matrix_world

    # ── perspective matrix → local floats (one-time extraction) ──
    pm = rv3d.perspective_matrix
    p00, p01, p02, p03 = pm[0][0], pm[0][1], pm[0][2], pm[0][3]
    p10, p11, p12, p13 = pm[1][0], pm[1][1], pm[1][2], pm[1][3]
    p30, p31, p32, p33 = pm[3][0], pm[3][1], pm[3][2], pm[3][3]
    W, H = float(region.width), float(region.height)
    limit_sq = max_px * max_px

    # ── adaptive grid size based on mesh density ──
    n_verts = len(bm.verts)
    if n_verts > 50000:
        gs = 0.5
    elif n_verts > 10000:
        gs = 1.0
    elif n_verts > 1000:
        gs = 3.0
    else:
        gs = 10.0

    # Cap grid size to half the object's largest WORLD-SPACE dimension
    bb = obj.bound_box
    bb_world = [mw @ Vector(c) for c in bb]
    wx = [v.x for v in bb_world]
    wy = [v.y for v in bb_world]
    wz = [v.z for v in bb_world]
    max_dim = max(max(wx) - min(wx), max(wy) - min(wy), max(wz) - min(wz), 0.2)
    gs = min(gs, max(max_dim * 0.5, 0.1))
    print(f"  [SNAP GRID] verts={n_verts} max_dim={max_dim:.2f} gs={gs:.2f}")

    _tc = _time.perf_counter()
    vg, eg, fg = _build_grid(obj, bm, mw, gs)
    _td = _time.perf_counter()

    print(f"  [SNAP DETAIL] bmesh={(_tb-_ta)*1000:.2f}ms  setup={(_tc-_tb)*1000:.2f}ms  grid={(_td-_tc)*1000:.2f}ms  cache={'HIT' if (_td-_tc)<1 else 'MISS'}")

    # ── screen-space cell culling ──
    ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))

    half_gs = gs * 0.5
    cell_px_limit = 50.0 * 50.0  # 50px radius

    cull_sq = 1e18

    def _nearby(cell_dict):
        result = []
        for cell in cell_dict:
            cx, cy, cz = cell
            wcx = cx * gs + half_gs
            wcy = cy * gs + half_gs
            wcz = cz * gs + half_gs
            vw = p30 * wcx + p31 * wcy + p32 * wcz + p33
            if vw <= 0.0:
                continue
            inv_w = 1.0 / vw
            px = W * (1.0 + (p00 * wcx + p01 * wcy + p02 * wcz + p03) * inv_w) * 0.5
            py = H * (1.0 + (p10 * wcx + p11 * wcy + p12 * wcz + p13) * inv_w) * 0.5
            sdx = mx - px; sdy = my - py
            if sdx * sdx + sdy * sdy < cell_px_limit:
                result.append(cell)
        return result

    # Query cell for debug vis
    bb_world = [mw @ Vector(c) for c in obj.bound_box]
    bb_cx = sum(v.x for v in bb_world) / 8.0
    bb_cy = sum(v.y for v in bb_world) / 8.0
    bb_cz = sum(v.z for v in bb_world) / 8.0
    t = ((bb_cx - ro.x) * rd.x + (bb_cy - ro.y) * rd.y + (bb_cz - ro.z) * rd.z)
    if t < 0.01:
        t = 0.01
    qx = ro.x + rd.x * t
    qy = ro.y + rd.y * t
    qz = ro.z + rd.z * t
    ccx = int(qx // gs)
    ccy = int(qy // gs)
    ccz = int(qz // gs)

    _debug['query_cell'] = (ccx, ccy, ccz)
    _debug['gs'] = gs
    _debug['nearby_cells'] = list(set(_nearby(vg['cells'])) | set(_nearby(eg['cells'])) | set(_nearby(fg['cells'])))
    _debug['all_cells'] = set(vg['cells'].keys()) | set(eg['cells'].keys()) | set(fg['cells'].keys())

    _te = _time.perf_counter()

    # ── VERT SNAP (highest priority, early-return) ────────────────
    if do_verts and vg['cells']:
        best_d2 = limit_sq
        bwx = bwy = bwz = bnx = bny = bnz = 0.0
        found = False
        _nearby_v = _nearby(vg['cells'])
        _n_cells = len(_nearby_v)
        _n_elems = sum(vg['cells'][c][1] - vg['cells'][c][0] for c in _nearby_v)
        _n_cull = 0; _n_behind = 0; _n_offscreen = 0; _n_checked = 0; _best_px_dist = 1e6
        g_wco = vg['wco']; g_wnm = vg['wnm']
        for cell in _nearby_v:
            s, e = vg['cells'][cell]
            # Convert cell slice to Python lists for fast inner loop
            wco_s = g_wco[s:e].tolist()
            wnm_s = g_wnm[s:e].tolist()
            for i in range(len(wco_s)):
                wx, wy, wz = wco_s[i]
                nx, ny, nz = wnm_s[i]
                dx = wx - qx; dy = wy - qy; dz = wz - qz
                if dx * dx + dy * dy + dz * dz > cull_sq:
                    _n_cull += 1
                    continue
                vw = p30 * wx + p31 * wy + p32 * wz + p33
                if vw <= 0.0:
                    _n_behind += 1
                    continue
                inv_w = 1.0 / vw
                px = W * (1.0 + (p00 * wx + p01 * wy + p02 * wz + p03) * inv_w) * 0.5
                py = H * (1.0 + (p10 * wx + p11 * wy + p12 * wz + p13) * inv_w) * 0.5
                if px < -50.0 or px > W + 50.0 or py < -50.0 or py > H + 50.0:
                    _n_offscreen += 1
                    continue
                _n_checked += 1
                sdx = mx - px; sdy = my - py
                d2 = sdx * sdx + sdy * sdy
                if d2 ** 0.5 < _best_px_dist:
                    _best_px_dist = d2 ** 0.5
                if d2 < best_d2:
                    best_d2 = d2
                    bwx, bwy, bwz = wx, wy, wz
                    bnx, bny, bnz = nx, ny, nz
                    found = True
        _tf = _time.perf_counter()
        if found:
            print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  cells={_n_cells}  elems={_n_elems}  VERT_HIT  best_px={_best_px_dist:.1f}")
            return (Vector((bwx, bwy, bwz)), Vector((bnx, bny, bnz)))
        print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  cells={_n_cells}  elems={_n_elems}  culled={_n_cull}  behind={_n_behind}  offscreen={_n_offscreen}  checked={_n_checked}  best_px={_best_px_dist:.1f}")
    else:
        _tf = _te

    # ── EDGE-CENTER + FACE-CENTER SNAP (combined scan) ────────────
    best_d2 = limit_sq
    bwx = bwy = bwz = bnx = bny = bnz = 0.0
    found = False
    for subgrid, enabled in ((eg, do_edge_center), (fg, do_face_center)):
        if not enabled or not subgrid['cells']:
            continue
        g_wco = subgrid['wco']; g_wnm = subgrid['wnm']
        for cell in _nearby(subgrid['cells']):
            s, e = subgrid['cells'][cell]
            wco_s = g_wco[s:e].tolist()
            wnm_s = g_wnm[s:e].tolist()
            for i in range(len(wco_s)):
                wx, wy, wz = wco_s[i]
                nx, ny, nz = wnm_s[i]
                dx = wx - qx; dy = wy - qy; dz = wz - qz
                if dx * dx + dy * dy + dz * dz > cull_sq:
                    continue
                vw = p30 * wx + p31 * wy + p32 * wz + p33
                if vw <= 0.0:
                    continue
                inv_w = 1.0 / vw
                px = W * (1.0 + (p00 * wx + p01 * wy + p02 * wz + p03) * inv_w) * 0.5
                py = H * (1.0 + (p10 * wx + p11 * wy + p12 * wz + p13) * inv_w) * 0.5
                if px < -50.0 or px > W + 50.0 or py < -50.0 or py > H + 50.0:
                    continue
                sdx = mx - px; sdy = my - py
                d2 = sdx * sdx + sdy * sdy
                if d2 < best_d2:
                    best_d2 = d2
                    bwx, bwy, bwz = wx, wy, wz
                    bnx, bny, bnz = nx, ny, nz
                    found = True
    _tg = _time.perf_counter()
    if found:
        print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  centers={(_tg-_tf)*1000:.2f}ms  CENTER_HIT")
        return (Vector((bwx, bwy, bwz)), Vector((bnx, bny, bnz)))

    # ── EDGE CLOSEST-POINT SNAP ──────────────────────────────────
    if do_edges and eg['cells']:
        _th0 = _time.perf_counter()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        _th1 = _time.perf_counter()
        best_d2 = limit_sq
        best_edge = None
        g_wco = eg['wco']; g_wnm = eg['wnm']; g_idx = eg['idx']
        for cell in _nearby(eg['cells']):
            s, e = eg['cells'][cell]
            idx_s = g_idx[s:e].tolist()
            wco_s = g_wco[s:e].tolist()
            wnm_s = g_wnm[s:e].tolist()
            for i in range(len(idx_s)):
                ewx, ewy, ewz = wco_s[i]
                enx, eny, enz = wnm_s[i]
                dx = ewx - qx; dy = ewy - qy; dz = ewz - qz
                if dx * dx + dy * dy + dz * dz > cull_sq:
                    continue
                edge = bm.edges[idx_s[i]]
                if edge.hide:
                    continue
                v1w = mw @ edge.verts[0].co
                v2w = mw @ edge.verts[1].co
                v1x, v1y, v1z = v1w.x, v1w.y, v1w.z
                v2x, v2y, v2z = v2w.x, v2w.y, v2w.z
                w1 = p30 * v1x + p31 * v1y + p32 * v1z + p33
                if w1 <= 0.0:
                    continue
                iw1 = 1.0 / w1
                s1x = W * (1.0 + (p00 * v1x + p01 * v1y + p02 * v1z + p03) * iw1) * 0.5
                s1y = H * (1.0 + (p10 * v1x + p11 * v1y + p12 * v1z + p13) * iw1) * 0.5
                w2 = p30 * v2x + p31 * v2y + p32 * v2z + p33
                if w2 <= 0.0:
                    continue
                iw2 = 1.0 / w2
                s2x = W * (1.0 + (p00 * v2x + p01 * v2y + p02 * v2z + p03) * iw2) * 0.5
                s2y = H * (1.0 + (p10 * v2x + p11 * v2y + p12 * v2z + p13) * iw2) * 0.5
                ex = s2x - s1x; ey = s2y - s1y
                elen_sq = ex * ex + ey * ey
                if elen_sq < 0.001:
                    continue
                t_edge = ((mx - s1x) * ex + (my - s1y) * ey) / elen_sq
                if t_edge < 0.0:
                    t_edge = 0.0
                elif t_edge > 1.0:
                    t_edge = 1.0
                cpx = s1x + t_edge * ex
                cpy = s1y + t_edge * ey
                sdx = mx - cpx; sdy = my - cpy
                d2 = sdx * sdx + sdy * sdy
                if d2 < best_d2:
                    best_d2 = d2
                    best_edge = (v1w, v2w, t_edge, enx, eny, enz)
        _th2 = _time.perf_counter()
        print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  centers={(_tg-_tf)*1000:.2f}ms  ensure_lut={(_th1-_th0)*1000:.2f}ms  edge_scan={(_th2-_th1)*1000:.2f}ms")
        if best_edge is not None:
            v1w, v2w, t_edge, enx, eny, enz = best_edge
            pt_3d = v1w + (v2w - v1w) * t_edge
            return (pt_3d, Vector((enx, eny, enz)))
    else:
        print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  centers={(_tg-_tf)*1000:.2f}ms  edges=SKIPPED")

    return None
