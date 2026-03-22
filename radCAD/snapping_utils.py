# snapping_utils.py  —  zero-alloc hot path, no raycasts

import bmesh
from mathutils import Vector
from bpy_extras import view3d_utils

ELEMENT_SNAP_RADIUS_PX = 15.0

# Grid cache — keyed on mesh identity + topology counts + grid_size
_snap_cache = {}


def _build_grid(obj, bm, mw, gs):
    """Build or return cached spatial grid.  Three separate sub-grids
    (verts, edge-centers, face-centers) so the inner loop never does
    a string comparison.  Elements stored as raw-float tuples so the
    inner loop never allocates a Vector.
    Format per element: (index, wx, wy, wz, nx, ny, nz)
    """
    key = (id(obj.data), len(bm.verts), len(bm.edges), len(bm.faces), gs)
    cached = _snap_cache.get(key)
    if cached is not None:
        return cached

    rot = mw.to_3x3().normalized()
    vg, eg, fg = {}, {}, {}

    for v in bm.verts:
        if v.hide:
            continue
        wco = mw @ v.co
        wnm = (rot @ v.normal).normalized()
        cell = (int(wco.x // gs), int(wco.y // gs), int(wco.z // gs))
        entry = (v.index, wco.x, wco.y, wco.z, wnm.x, wnm.y, wnm.z)
        try:
            vg[cell].append(entry)
        except KeyError:
            vg[cell] = [entry]

    for e in bm.edges:
        if e.hide:
            continue
        wco = mw @ ((e.verts[0].co + e.verts[1].co) * 0.5)
        avg_n = (e.verts[0].normal + e.verts[1].normal) * 0.5
        wnm = (rot @ avg_n).normalized()
        cell = (int(wco.x // gs), int(wco.y // gs), int(wco.z // gs))
        entry = (e.index, wco.x, wco.y, wco.z, wnm.x, wnm.y, wnm.z)
        try:
            eg[cell].append(entry)
        except KeyError:
            eg[cell] = [entry]

    for f in bm.faces:
        if f.hide:
            continue
        wco = mw @ f.calc_center_median()
        wnm = (rot @ f.normal).normalized()
        cell = (int(wco.x // gs), int(wco.y // gs), int(wco.z // gs))
        entry = (f.index, wco.x, wco.y, wco.z, wnm.x, wnm.y, wnm.z)
        try:
            fg[cell].append(entry)
        except KeyError:
            fg[cell] = [entry]

    result = (vg, eg, fg)
    _snap_cache[key] = result
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
    # With grid_size=10, a dense mesh puts 300K+ verts in 9 cells.
    # We need cells small enough that each one has ~50-100 verts max.
    n_verts = len(bm.verts)
    if n_verts > 50000:
        gs = 0.5
    elif n_verts > 10000:
        gs = 1.0
    elif n_verts > 1000:
        gs = 3.0
    else:
        gs = 10.0

    _tc = _time.perf_counter()
    vg, eg, fg = _build_grid(obj, bm, mw, gs)
    _td = _time.perf_counter()

    print(f"  [SNAP DETAIL] bmesh={(_tb-_ta)*1000:.2f}ms  setup={(_tc-_tb)*1000:.2f}ms  grid={(_td-_tc)*1000:.2f}ms  cache={'HIT' if (_td-_tc)<1 else 'MISS'}")

    # ── query point along camera ray (raw floats) ──
    ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    vc = rv3d.view_location
    t = (vc.x - ro.x) * rd.x + (vc.y - ro.y) * rd.y + (vc.z - ro.z) * rd.z
    if t < 0.01:
        t = 0.01
    qx = ro.x + rd.x * t
    qy = ro.y + rd.y * t
    qz = ro.z + rd.z * t

    ccx = int(qx // gs)
    ccy = int(qy // gs)
    ccz = int(qz // gs)
    # Scale search radius inversely with grid density
    # gs=10 → R=3 (70 units), gs=1 → R=5 (11 units), gs=0.5 → R=7 (7.5 units)
    R = max(3, int(5.0 / gs))
    if R > 10:
        R = 10
    cull_sq = (gs * float(R)) ** 2

    search_cells = [
        (ccx + dx, ccy + dy, ccz + dz)
        for dx in range(-R, R + 1)
        for dy in range(-R, R + 1)
        for dz in range(-R, R + 1)
    ]

    def _nearby(subgrid):
        return [c for c in search_cells if c in subgrid]

    _te = _time.perf_counter()

    # ── VERT SNAP (highest priority, early-return) ────────────────
    if do_verts and vg:
        best_d2 = limit_sq
        bwx = bwy = bwz = bnx = bny = bnz = 0.0
        found = False
        _nearby_v = _nearby(vg)
        _n_cells = len(_nearby_v)
        _n_elems = sum(len(vg[c]) for c in _nearby_v)
        for cell in _nearby_v:
            for elem in vg[cell]:
                _, wx, wy, wz, nx, ny, nz = elem
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
        _tf = _time.perf_counter()
        if found:
            print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  cells={_n_cells}  elems={_n_elems}  VERT_HIT")
            return (Vector((bwx, bwy, bwz)), Vector((bnx, bny, bnz)))
        print(f"  [SNAP DETAIL2] ray+cells={(_te-_td)*1000:.2f}ms  verts={(_tf-_te)*1000:.2f}ms  cells={_n_cells}  elems={_n_elems}")
    else:
        _tf = _te

    # ── EDGE-CENTER + FACE-CENTER SNAP (combined scan) ────────────
    best_d2 = limit_sq
    bwx = bwy = bwz = bnx = bny = bnz = 0.0
    found = False
    for subgrid, enabled in ((eg, do_edge_center), (fg, do_face_center)):
        if not enabled or not subgrid:
            continue
        for cell in _nearby(subgrid):
            for elem in subgrid[cell]:
                _, wx, wy, wz, nx, ny, nz = elem
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
    if do_edges and eg:
        _th0 = _time.perf_counter()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        _th1 = _time.perf_counter()
        best_d2 = limit_sq
        best_edge = None
        for cell in _nearby(eg):
            for elem in eg[cell]:
                idx, ewx, ewy, ewz, enx, eny, enz = elem
                dx = ewx - qx; dy = ewy - qy; dz = ewz - qz
                if dx * dx + dy * dy + dz * dz > cull_sq:
                    continue
                e = bm.edges[idx]
                if e.hide:
                    continue
                v1w = mw @ e.verts[0].co
                v2w = mw @ e.verts[1].co
                v1x, v1y, v1z = v1w.x, v1w.y, v1w.z
                v2x, v2y, v2z = v2w.x, v2w.y, v2w.z
                # project endpoint 1
                w1 = p30 * v1x + p31 * v1y + p32 * v1z + p33
                if w1 <= 0.0:
                    continue
                iw1 = 1.0 / w1
                s1x = W * (1.0 + (p00 * v1x + p01 * v1y + p02 * v1z + p03) * iw1) * 0.5
                s1y = H * (1.0 + (p10 * v1x + p11 * v1y + p12 * v1z + p13) * iw1) * 0.5
                # project endpoint 2
                w2 = p30 * v2x + p31 * v2y + p32 * v2z + p33
                if w2 <= 0.0:
                    continue
                iw2 = 1.0 / w2
                s2x = W * (1.0 + (p00 * v2x + p01 * v2y + p02 * v2z + p03) * iw2) * 0.5
                s2y = H * (1.0 + (p10 * v2x + p11 * v2y + p12 * v2z + p13) * iw2) * 0.5
                # closest point on segment (inline, no Vector allocs)
                ex = s2x - s1x; ey = s2y - s1y
                elen_sq = ex * ex + ey * ey
                if elen_sq < 0.001:
                    continue
                t_edge = ((mx - s1x) * ex + (my - s1y) * ey) / elen_sq
                if t_edge < 0.0:
                    t_edge = 0.0
                elif t_edge > 1.0:
                    t_edge = 1.0
                cx = s1x + t_edge * ex
                cy = s1y + t_edge * ey
                sdx = mx - cx; sdy = my - cy
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
