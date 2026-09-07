# radCAD/operators/line_tools.py
import math
import bmesh
import bpy
from mathutils import Vector, kdtree, geometry, Matrix
from bpy_extras import view3d_utils
from ..inference_utils import get_axis_snapped_location
from ..plane_utils import world_to_plane, plane_to_world, world_radius_for_pixel_size
from ..units_utils import parse_length_input
from .base_tool import SurfaceDrawTool 

# --- CONFIGURATION ---
class CFG:
    SAMPLE_STEP = 0.05
    SNAP_SCAN_STRIDE = 10 
    BEAM_WIDTH = 10000.0 
    SNAP_PX = 30 
    STRICT_ERROR_TOLERANCE = 0.001

def to_3d(v2):
    return Vector((v2.x, v2.y, 0.0))

class Spline:
    def __init__(self, points):
        self.points = points
        self.is_cyclic = False
        self.samples = []
        self.kd = None
        
        if len(self.points) > 2:
            if (self.points[0] - self.points[-1]).length_squared < 1e-6:
                self.is_cyclic = True
                self.points.pop()
        
        if self.points:
            self.build_cache()

    def build_cache(self):
        self.samples = []
        total_segments = len(self.points) if self.is_cyclic else len(self.points) - 1
        if total_segments < 1: total_segments = 1
            
        estimated_samples = int(total_segments * (1.0 / CFG.SAMPLE_STEP)) + 10
        self.kd = kdtree.KDTree(estimated_samples)

        count = len(self.points)
        loop_range = count if self.is_cyclic else count - 1
        
        idx = 0
        for i in range(loop_range):
            t = 0.0
            # --- FIX: Ensure we hit the final endpoint by including t=1.0 on the last segment ---
            while t < 1.0 or (i == loop_range - 1 and abs(t - 1.0) < 1e-6):
                u = float(i) + t
                pos = self.evalCatmull(u)
                deriv = self.evalDeriv(u)
                if deriv.length_squared > 1e-8: tan = deriv.normalized()
                else: tan = Vector((1.0, 0.0))
                
                self.samples.append({'u': u, 'pos': pos, 'tan': tan})
                self.kd.insert(to_3d(pos), idx)
                idx += 1
                t += CFG.SAMPLE_STEP
                if i == loop_range - 1 and t > 1.0: break # Safety exit
        self.kd.balance()

    def getControlPoints(self, u):
        count = len(self.points)
        if count == 0: return Vector((0,0)), Vector((0,0)), Vector((0,0)), Vector((0,0)), 0.0

        if self.is_cyclic:
            u = u % count
            if u < 0: u += count
            i = math.floor(u)
            t = u - i
            p0 = self.points[(i - 1) % count]
            p1 = self.points[i % count]
            p2 = self.points[(i + 1) % count]
            p3 = self.points[(i + 2) % count]
        else:
            if u < 0: u = 0
            if u >= count - 1: u = count - 1.0001
            i = math.floor(u)
            t = u - i
            p0 = self.points[max(0, i-1)]
            p1 = self.points[i]
            p2 = self.points[min(count-1, i+1)]
            p3 = self.points[min(count-1, i+2)]
        return p0, p1, p2, p3, t

    def evalCatmull(self, u):
        p0, p1, p2, p3, t = self.getControlPoints(u)
        t2, t3 = t*t, t*t*t
        f0 = -0.5*t3 + t2 - 0.5*t
        f1 =  1.5*t3 - 2.5*t2 + 1.0
        f2 = -1.5*t3 + 2.0*t2 + 0.5*t
        f3 =  0.5*t3 - 0.5*t2
        return Vector((p0.x*f0 + p1.x*f1 + p2.x*f2 + p3.x*f3, p0.y*f0 + p1.y*f1 + p2.y*f2 + p3.y*f3))

    def evalDeriv(self, u):
        p0, p1, p2, p3, t = self.getControlPoints(u)
        t2 = t*t
        f0 = -1.5*t2 + 2*t - 0.5
        f1 =  4.5*t2 - 5*t
        f2 = -4.5*t2 + 4*t + 0.5
        f3 =  1.5*t2 - 1.0*t
        return Vector((p0.x*f0 + p1.x*f1 + p2.x*f2 + p3.x*f3, p0.y*f0 + p1.y*f1 + p2.y*f2 + p3.y*f3))

    def evalSecDeriv(self, u):
        p0, p1, p2, p3, t = self.getControlPoints(u)
        f0, f1, f2, f3 = -3.0*t + 2.0, 9.0*t - 5.0, -9.0*t + 4.0, 3.0*t - 1.0
        return Vector((p0.x*f0 + p1.x*f1 + p2.x*f2 + p3.x*f3, p0.y*f0 + p1.y*f1 + p2.y*f2 + p3.y*f3))

    # --- HYBRID SOLVER (Continuous + Trap Escape) ---
    def solve_robust(self, mousePos, seedU, minU, maxU):
        u = seedU
        stuck = False
        
        for _ in range(5):
            P = self.evalCatmull(u)
            d1 = self.evalDeriv(u) 
            d2 = self.evalSecDeriv(u)
            diff = P - mousePos
            f_val = diff.dot(d1)
            f_prime = d1.dot(d1) + diff.dot(d2)
            
            if abs(f_prime) < 1e-6: 
                stuck = True
                break 
            
            step = f_val / f_prime
            
            if not self.is_cyclic and (u < minU + 0.5 or u > maxU - 0.5):
                step *= 0.5

            u -= step
            if self.is_cyclic:
                if u < 0: u += maxU
                elif u > maxU: u -= maxU
            else:
                u = max(minU, min(maxU, u))
            if abs(step) < 1e-5: break 
        
        at_boundary = (not self.is_cyclic) and (abs(u - minU) < 0.001 or abs(u - maxU) < 0.001)
        if stuck or at_boundary:
            kd_u = self.find_nearest_u(mousePos)
            if abs(kd_u - u) > 1.0:
                 u = self.findLocalPerpendicularAnchor(mousePos, minU, maxU, kd_u)
        return u

    def findLocalPerpendicularAnchor(self, mousePos, minU, maxU, seedU):
        u = seedU
        for _ in range(5):
            P = self.evalCatmull(u)
            d1 = self.evalDeriv(u)
            d2 = self.evalSecDeriv(u)
            diff = P - mousePos
            f_val = diff.dot(d1)
            f_prime = d1.dot(d1) + diff.dot(d2)
            if abs(f_prime) < 1e-6: break
            step = f_val / f_prime
            u -= step
            if self.is_cyclic:
                 if u < 0: u += maxU
                 elif u > maxU: u -= maxU
            else:
                 u = max(minU, min(maxU, u))
            if abs(step) < 1e-5: break
        return u

    def find_nearest_u(self, point):
        if not self.kd: return 0.0
        co_3d, index, dist = self.kd.find(to_3d(point))
        return self.samples[index]['u']
    
    def find_tangent_param_from_point(self, origin, seedU, range_val):
        bestU = seedU
        bestScore = -1.0 
        step = 0.02
        start = max(0.0, seedU - range_val)
        max_limit = float(len(self.points) - 1) if not self.is_cyclic else float(len(self.points))
        end = min(max_limit, seedU + range_val)
        
        u = start
        while u <= end:
            p = self.evalCatmull(u)
            line_vec = p - origin
            dist = line_vec.length
            if dist < 0.1: 
                u += step
                continue
            dir_vec = line_vec / dist
            deriv = self.evalDeriv(u)
            tan = deriv.normalized() if deriv.length_squared > 1e-8 else Vector((1.0, 0.0))
            dot = abs(dir_vec.dot(tan))
            if dot > bestScore:
                bestScore = dot
                bestU = u
            u += step
            
        currU = bestU
        for k in range(5):
            p = self.evalCatmull(currU)
            line_vec = p - origin
            dist = line_vec.length
            if dist < 0.001: break
            dir_vec = line_vec / dist
            deriv = self.evalDeriv(currU)
            tan = deriv.normalized() if deriv.length_squared > 1e-8 else Vector((1,0))
            score = abs(dir_vec.dot(tan))
            nextU = currU + 0.005
            pNext = self.evalCatmull(nextU)
            lineNext = pNext - origin
            distNext = lineNext.length
            if distNext > 0.001:
                dirNext = lineNext / distNext
                derivNext = self.evalDeriv(nextU)
                tanNext = derivNext.normalized() if derivNext.length_squared > 1e-8 else Vector((1,0))
                scoreNext = abs(dirNext.dot(tanNext))
                if scoreNext > score:
                    currU = nextU
                else:
                    currU -= 0.005 
        return currU

    def find_perp_param_from_point(self, origin, seedU, range_val):
        bestU = seedU
        bestScore = 1.0 
        step = 0.02
        start = max(0.0, seedU - range_val)
        max_limit = float(len(self.points) - 1) if not self.is_cyclic else float(len(self.points))
        end = min(max_limit, seedU + range_val)
        
        u = start
        while u <= end:
            p = self.evalCatmull(u)
            line_vec = p - origin
            dist = line_vec.length
            if dist < 0.1: 
                u += step
                continue
            dir_vec = line_vec / dist
            deriv = self.evalDeriv(u)
            tan = deriv.normalized() if deriv.length_squared > 1e-8 else Vector((1.0, 0.0))
            dot = abs(dir_vec.dot(tan))
            if dot < bestScore: # Minimize
                bestScore = dot
                bestU = u
            u += step
            
        # --- NEW: High-Precision Refinement using Newton's Method ---
        currU = self.findLocalPerpendicularAnchor(origin, 0.0, float(len(self.points)), bestU)
        return currU

    # --- PORTED LOGIC FROM line_tangent_to_1_curve_optimized.html ---
    def getTangentError(self, u, target):
        if u < 0 or u > (len(self.points) - 1 if not self.is_cyclic else len(self.points)): 
            return float('inf')
        p = self.evalCatmull(u)
        deriv = self.evalDeriv(u)
        tan = deriv.normalized() if deriv.length_squared > 1e-8 else Vector((1,0))
        toTarget = target - p
        dist = toTarget.length
        if dist < 0.001: return float('inf')
        dir_vec = toTarget / dist
        # Cross product (2D): tan.x * dir.y - tan.y * dir.x
        return abs(tan.x * dir_vec.y - tan.y * dir_vec.x)

    def solveRollingContact(self, target, startU, seedU, constraintRadius):
        bestU = startU
        minError = float('inf')
        range_val = constraintRadius
        
        max_u = float(len(self.points) - 1) if not self.is_cyclic else float(len(self.points))
        start = seedU - range_val
        end = seedU + range_val
        
        # Coarse scan
        u = start
        while u <= end:
            # Handle wrapping for cyclic splines
            test_u = u
            if self.is_cyclic:
                test_u = test_u % max_u
            else:
                test_u = max(0.0, min(max_u, test_u))

            p = self.evalCatmull(test_u)
            deriv = self.evalDeriv(test_u)
            tan = deriv.normalized() if deriv.length_squared > 1e-8 else Vector((1,0))
            toMouse = target - p
            dist = toMouse.length
            if dist < 0.1: 
                u += 0.01
                continue
            dir_vec = toMouse / dist
            error = abs(tan.x * dir_vec.y - tan.y * dir_vec.x)
            if error < minError:
                minError = error
                bestU = test_u
            u += 0.01
            
        # Refinement
        currU = bestU
        step = 0.005
        for k in range(5):
            e0 = self.getTangentError(currU, target)
            e1 = self.getTangentError(currU + step, target)
            e2 = self.getTangentError(currU - step, target)
            
            if e1 < e0:
                currU += step
            elif e2 < e0:
                currU -= step
            else:
                step *= 0.5
            
            if self.is_cyclic: currU = currU % max_u
            else: currU = max(0.0, min(max_u, currU))

        return currU

    def scanForSnaps(self, anchorSpline, anchorU, minU, maxU):
        pA = anchorSpline.evalCatmull(anchorU)
        derivA = anchorSpline.evalDeriv(anchorU)
        tanA = derivA.normalized() if derivA.length_squared > 1e-8 else Vector((1,0))
        rayDir = Vector((-tanA.y, tanA.x))
        
        ppCandidate, ppBestAlign = None, 1.0
        ptCandidate, ptBestAlign = None, 0.0

        step = CFG.SNAP_SCAN_STRIDE
        
        for i in range(0, len(self.samples), step):
            s = self.samples[i]
            chord = s['pos'] - pA
            if abs(chord.dot(tanA)) > CFG.BEAM_WIDTH: continue 
            align = abs(s['tan'].dot(rayDir))
            if align < 0.3: 
                if align < ppBestAlign: ppBestAlign, ppCandidate = align, s['u']
            elif align > 0.7:
                if align > ptBestAlign: ptBestAlign, ptCandidate = align, s['u']
        
        snaps = []
        if ppCandidate is not None:
            opt = self.optimizeBiNormal(anchorSpline, anchorU, self, ppCandidate, minU, maxU)
            if opt['error'] < CFG.STRICT_ERROR_TOLERANCE:
                snaps.append({'targetPos': opt['p2'], 'anchorPos': opt['p1'], 'u': opt['u2'], 'anchorU': opt['u1'], 'type': 'pp'})
        if ptCandidate is not None:
            opt = self.optimizePerpTan(anchorSpline, anchorU, self, ptCandidate, minU, maxU)
            if opt['error'] < CFG.STRICT_ERROR_TOLERANCE:
                snaps.append({'targetPos': opt['p2'], 'anchorPos': opt['p1'], 'u': opt['u2'], 'anchorU': opt['u1'], 'type': 'pt'})
        return snaps

    def optimizeBiNormal(self, s1, u1_in, s2, u2_in, minU, maxU):
        u1, u2 = u1_in, u2_in
        step = 0.01
        finalError = float('inf')

        for _ in range(15):
            p1, p2 = s1.evalCatmull(u1), s2.evalCatmull(u2)
            chord = p2 - p1
            length = chord.length
            chordDir = chord / length if length > 1e-4 else Vector((0,0))
            t1, t2 = s1.evalDeriv(u1).normalized(), s2.evalDeriv(u2).normalized()

            if length < 1e-4: e = 1.0 - abs(t1.dot(t2))
            else: e = abs(t1.dot(chordDir)) + abs(t2.dot(chordDir))
            finalError = e
            if e < 1e-6: break
            
            def get_err(u1t, u2t):
                p1t, p2t = s1.evalCatmull(u1t), s2.evalCatmull(u2t)
                ct = p2t - p1t
                lt = ct.length
                cdt = ct / lt if lt > 1e-4 else Vector((0,0))
                return abs(s1.evalDeriv(u1t).normalized().dot(cdt)) + abs(s2.evalDeriv(u2t).normalized().dot(cdt))

            if get_err(u1 + step, u2) < e: u1 += step
            elif get_err(u1 - step, u2) < e: u1 -= step
            if not s1.is_cyclic: u1 = max(minU, min(maxU, u1))

            if get_err(u1, u2 + step) < e: u2 += step
            elif get_err(u1, u2 - step) < e: u2 -= step
            step *= 0.95
        return {'u1': u1, 'u2': u2, 'p1': s1.evalCatmull(u1), 'p2': s2.evalCatmull(u2), 'error': finalError}

    def optimizePerpTan(self, s1, u1_in, s2, u2_in, minU, maxU):
        u1, u2 = u1_in, u2_in
        step = 0.01
        finalError = float('inf')
        
        for _ in range(15):
            p1, p2 = s1.evalCatmull(u1), s2.evalCatmull(u2)
            chord = p2 - p1
            length = chord.length
            chordDir = chord / length if length > 1e-4 else Vector((0,0))
            t1, t2 = s1.evalDeriv(u1).normalized(), s2.evalDeriv(u2).normalized()

            if length < 1e-4: e = abs(t1.dot(t2))
            else: e = abs(t1.dot(chordDir)) + abs(t2.x*chordDir.y - t2.y*chordDir.x)
            finalError = e
            if e < 1e-6: break

            def get_err(u1t, u2t):
                p1t, p2t = s1.evalCatmull(u1t), s2.evalCatmull(u2t)
                ct = p2t - p1t
                lt = ct.length
                cdt = ct / lt if lt > 1e-4 else Vector((0,0))
                t2t = s2.evalDeriv(u2t).normalized()
                return abs(s1.evalDeriv(u1t).normalized().dot(cdt)) + abs(t2t.x*cdt.y - t2t.y*cdt.x)

            if get_err(u1 + step, u2) < e: u1 += step
            elif get_err(u1 - step, u2) < e: u1 -= step
            if not s1.is_cyclic: u1 = max(minU, min(maxU, u1))

            if get_err(u1, u2 + step) < e: u2 += step
            elif get_err(u1, u2 - step) < e: u2 -= step
            step *= 0.95
        return {'u1': u1, 'u2': u2, 'p1': s1.evalCatmull(u1), 'p2': s2.evalCatmull(u2), 'error': finalError}

    def getClosestU_Global(self, point):
        return self.find_nearest_u(point)

def get_disjoint_chains(bm, include_verts=False):
    if not bm: return []
    try: selected_edges = [e for e in bm.edges if e.select]
    except ReferenceError: return []
    if not selected_edges: return []
    vert_map = {}
    for e in selected_edges:
        for v in e.verts:
            if v not in vert_map: vert_map[v] = []
            vert_map[v].append(e)
    processed_edges, chains = set(), []
    endpoints = [v for v, edges in vert_map.items() if len(edges) == 1]
    if not endpoints and vert_map: endpoints = [list(vert_map.keys())[0]]
    for start_node in endpoints:
        if not vert_map.get(start_node): continue
        curr, path = start_node, [start_node]
        while True:
            edges = vert_map.get(curr, [])
            next_edge = next((e for e in edges if e not in processed_edges), None)
            if not next_edge: break
            processed_edges.add(next_edge)
            curr = next_edge.other_vert(curr)
            path.append(curr)
        if len(path) > 1:
            chains.append(
                path
                if include_verts
                else [vert.co.copy() for vert in path]
            )
    remaining = [e for e in selected_edges if e not in processed_edges]
    while remaining:
        seed = remaining[0]
        curr, path = seed.verts[0], [seed.verts[0]]
        processed_edges.add(seed)
        curr = seed.other_vert(curr)
        path.append(curr)
        while True:
            edges = vert_map.get(curr, [])
            next_edge = next((e for e in edges if e not in processed_edges), None)
            if not next_edge: break
            processed_edges.add(next_edge)
            curr = next_edge.other_vert(curr)
            path.append(curr)
        chains.append(
            path
            if include_verts
            else [vert.co.copy() for vert in path]
        )
        remaining = [e for e in selected_edges if e not in processed_edges]
    return chains

def solve_rhino_tangent(s1, s2, seed_u1, seed_u2):
    bestU1 = seed_u1
    bestU2 = seed_u2

    # 1. Global Search (Constrained nearby search to prevent jitter)
    search_range = 1.0
    p1 = s1.evalCatmull(bestU1)
    bestU2 = s2.find_tangent_param_from_point(p1, bestU2, search_range)
    p2 = s2.evalCatmull(bestU2)
    bestU1 = s1.find_tangent_param_from_point(p2, bestU1, search_range)

    # 2. Local Refinement
    range_val = 2.0
    for _ in range(9):
        p1 = s1.evalCatmull(bestU1)
        bestU2 = s2.find_tangent_param_from_point(p1, bestU2, range_val)
        p2 = s2.evalCatmull(bestU2)
        bestU1 = s1.find_tangent_param_from_point(p2, bestU1, range_val)

    return bestU1, bestU2

def solve_rhino_perp(s1, s2, seed_u1, seed_u2):
    bestU1 = seed_u1
    bestU2 = seed_u2

    # 1. Global Search (Constrained nearby search to prevent jitter)
    search_range = 1.0
    p1 = s1.evalCatmull(bestU1)
    bestU2 = s2.find_perp_param_from_point(p1, bestU2, search_range)
    p2 = s2.evalCatmull(bestU2)
    bestU1 = s1.find_perp_param_from_point(p2, bestU1, search_range)

    # 2. Local Refinement
    range_val = 2.0
    for _ in range(9):
        p1 = s1.evalCatmull(bestU1)
        bestU2 = s2.find_perp_param_from_point(p1, bestU2, range_val)
        p2 = s2.evalCatmull(bestU2)
        bestU1 = s1.find_perp_param_from_point(p2, bestU1, range_val)
        
    # --- NEW: Strict Validation ---
    p1 = s1.evalCatmull(bestU1)
    p2 = s2.evalCatmull(bestU2)
    line_vec = p2 - p1
    if line_vec.length_squared < 1e-8: return None, None
    
    line_dir = line_vec.normalized()
    t1 = s1.evalDeriv(bestU1).normalized()
    t2 = s2.evalDeriv(bestU2).normalized()
    
    # If not perpendicular to both (dot product should be near 0)
    tol = 0.001 # Extremely strict: roughly 0.05 degrees
    if abs(line_dir.dot(t1)) > tol or abs(line_dir.dot(t2)) > tol:
        return None, None

    return bestU1, bestU2

class LineTool_Poly(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_POLY"
        self.points, self.current, self.constraint_axis = [], None, None
        self.shift_lock_vec = None
        self.locked_length = None # Store confirmed length input
        self.face_normal = None
        self.state["line_normal_locked"] = False

    def update(self, context, event, snap_point, snap_normal):
        if snap_point is None: snap_point = Vector((0,0,0))
        
        # Reset axis vec default
        self.state["current_axis_vector"] = None

        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            normal = self.state.get("line_hover_normal")
            self.face_normal = normal.copy() if normal is not None else None
            self.current, self.preview_pts = snap_point, []
            return
        
        ref = self.points[-1] if self.points else self.pivot
        target = snap_point
        
        # --- SHIFT LOCK LOGIC ---
        if event.shift:
            if self.shift_lock_vec is None:
                # 1. First frame of press: keep an explicit axis constraint
                # stable.  Otherwise lock to the axis the cursor is already
                # visually approaching, falling back to its current direction.
                if self.constraint_axis is not None:
                    self.shift_lock_vec = self.constraint_axis.copy()
                else:
                    strength = max(0.1, min(89.0, self.state.get("snap_strength", 6.0)))
                    inf_loc, inf_axis, _ = get_axis_snapped_location(
                        ref, (event.mouse_region_x, event.mouse_region_y),
                        context,
                        snap_threshold=math.cos(math.radians(strength))
                    )

                    if inf_axis:
                        self.shift_lock_vec = inf_axis
                    else:
                        diff = target - ref
                        if diff.length_squared > 1e-6:
                            self.shift_lock_vec = diff.normalized()
            
            if self.shift_lock_vec:
                # Use 3D ray intersection to handle vertical lines correctly
                ray_o = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y))
                ray_v = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y))

                line_p1 = ref
                line_p2 = ref + self.shift_lock_vec

                res = geometry.intersect_line_line(ray_o, ray_o + ray_v, line_p1, line_p2)

                if res:
                    target = res[1]
                else:
                    v = target - ref
                    dist = v.dot(self.shift_lock_vec)
                    target = ref + self.shift_lock_vec * dist

                # --- SNAP + SHIFT LOCK: Project snap_point onto the locked axis ---
                if self.state.get("geometry_snap") and snap_point is not None:
                    diff = snap_point - ref
                    target = ref + self.shift_lock_vec * diff.dot(self.shift_lock_vec)

                self.state["current_axis_vector"] = self.shift_lock_vec
        else:
            self.shift_lock_vec = None
        # ------------------------
        
        # --- DIRECTION DETERMINATION ---
        # 1. Axis Constraint (Override target if active)
        if self.constraint_axis:
            self.state["current_axis_vector"] = self.constraint_axis
            ray_o = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y))
            ray_v = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y))
            res = geometry.intersect_line_line(ray_o, ray_o + ray_v, ref, ref + self.constraint_axis)
            if res: target = res[1]
            else:
                target = ref + self.constraint_axis * (target - ref).dot(self.constraint_axis)

            # Match Shift + snap behavior: use the snapped point to determine
            # the distance along the constrained axis instead of discarding
            # it in favor of the cursor/ray intersection.
            if self.state.get("geometry_snap") and snap_point is not None:
                delta = snap_point - ref
                target = ref + self.constraint_axis * delta.dot(self.constraint_axis)

        # 2. Passive axis inference must not replace an explicit geometry snap.
        elif not self.shift_lock_vec and not self.state.get("geometry_snap", False):
            strength = max(0.1, min(89.0, self.state.get("snap_strength", 6.0)))
            inf_loc, inf_axis, _ = get_axis_snapped_location(ref, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=math.cos(math.radians(strength)))
            if inf_loc: 
                target = inf_loc
                self.state["current_axis_vector"] = inf_axis

        # Resolve the actual edge/axis crossing before applying a typed length.
        # Projecting the hovered edge point only matches its height/distance;
        # it does not generally land on the edge itself.
        axis = self.state.get("current_axis_vector")
        if axis is not None and self.state.get("snap_intersections", False):
            from ..snapping_utils import snap_axis_intersection
            intersection = snap_axis_intersection(
                context, context.edit_object,
                event.mouse_region_x, event.mouse_region_y, ref, axis,
                self.state.get("snap_strength", 6.0) * 2.0,
            )
            if intersection is not None:
                target = intersection
                self.state["snap_point"] = intersection.copy()
                self.state["geometry_snap"] = True

        # --- LENGTH LOCK ---
        # Apply confirmed input length OR active typing length to the DETERMINED direction
        
        # Check input_mode or locked_length
        has_input = (self.state.get("input_string") and len(self.state["input_string"]) > 0)
        
        if has_input:
             # TRY LIVE UPDATE
             try:
                 val_meters = parse_length_input(self.state["input_string"])
                 self.locked_length = max(0.0001, abs(val_meters))
             except:
                 # Fallback to stored radius if parsing fails (e.g. partial string)
                 val = self.state.get("radius")
                 if val is not None:
                     self.locked_length = val
        else:
             if self.locked_length is not None and not has_input:
                 self.locked_length = None

        user_len = None
        if self.locked_length is not None:
            user_len = self.locked_length
            
        if user_len is not None:
            # We now apply this length to the direction vector (target - ref)
            diff = target - ref
            if diff.length_squared > 1e-6:
                target = ref + diff.normalized() * user_len
            else:
                # Fallback: If mouse is exactly on pivot, default to X axis or last known axis
                fallback_dir = self.state.get("current_axis_vector") if self.state.get("current_axis_vector") else self.Xp
                target = ref + fallback_dir * user_len
        
        self.current = target
        self.preview_pts = self.points + [self.current]
        self.radius = (self.current - ref).length

    def apply_typed_length_now(self):
        val_str = self.state.get("input_string", "")
        if not val_str:
            return False

        try:
            length = max(0.0001, abs(parse_length_input(val_str)))
        except Exception:
            return False

        ref = self.points[-1] if self.points else self.pivot
        if ref is None:
            return False

        direction = None
        if self.current is not None:
            delta = self.current - ref
            if delta.length_squared > 1e-10:
                direction = delta.normalized()

        if direction is None:
            direction = self.state.get("current_axis_vector") or self.Xp or Vector((1, 0, 0))
            if direction.length_squared <= 1e-10:
                direction = Vector((1, 0, 0))
            else:
                direction = direction.normalized()

        self.locked_length = length
        self.current = ref + direction * length
        self.preview_pts = self.points + [self.current]
        self.radius = length
        return True

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            normal = self.state.get("line_hover_normal")
            self.face_normal = normal.copy() if normal is not None else self.face_normal
            self.pivot = snap_point
            self.points.append(snap_point)
            self.state["locked"], self.state["locked_normal"], self.stage = True, self.Zp, 1
            return 'NEXT_STAGE'
        if (
            self.state.get("geometry_snap", False)
            and snap_point is not None
            and self.constraint_axis is None
            and self.shift_lock_vec is None
            and self.state.get("current_axis_vector") is None
            and not self.state.get("input_string")
        ):
            self.current = snap_point.copy()
            self.preview_pts = self.points + [self.current]
        if (self.current - self.points[-1]).length < 1e-5: return None
        self.points.append(self.current)
        self.constraint_axis = self.state["constraint_axis"] = None
        self.state["line_normal_locked"] = False
        normal = self.state.get("line_hover_normal")
        if normal is not None:
            self.face_normal = normal.copy()
        self.shift_lock_vec = None # Reset shift lock so next segment can start fresh
        self.locked_length = None # Reset length lock
        
        # --- FORCE CLEAR INPUT ---
        # Crucial: We must wipe the input buffer so the next segment doesn't immediately lock
        self.state["input_string"] = ""
        self.state["input_mode"] = None
        
        self.pivot = self.current
        return 'NEXT_STAGE'

    def handle_input(self, context, event):
        if self.mode == "LINE_POLY" and event.type == 'N' and event.value == 'PRESS':
            if self.stage == 0 or self.face_normal is None:
                self.core.report({'INFO'}, "Start a line on a face, edge or vertex first")
                return True
            enabled = not self.state.get("line_normal_locked", False)
            self.state["line_normal_locked"] = enabled
            self.constraint_axis = self.state["constraint_axis"] = self.face_normal.normalized() if enabled else None
            self.shift_lock_vec = None
            return True
        if super().handle_plane_lock_input(context, event): return True
        if event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            self.state["line_normal_locked"] = False
            axes = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}
            self.constraint_axis = self.state["constraint_axis"] = None if self.constraint_axis == axes[event.type] else axes[event.type]
            return True
        if event.type == 'BACK_SPACE' and event.value == 'PRESS' and len(self.points) > 1:
            self.points.pop()
            self.locked_length = None # Clear lock on undo
            self.pivot, self.preview_pts = self.points[-1], self.points + [self.current] if self.current else self.points
            return True
        return False

class LineTool_PerpFromCurve(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_PERP_FROM_CURVE"
        self.stage = 0
        self.splines, self.source_idx, self.current_u, self.snapped, self.locked = [], -1, 0.0, None, False
        self.current, self.pivot = None, None
        self.spline_geom = [] # Store 3D points for preview
        
        obj = bpy.context.edit_object
        if obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            self.all_chains = get_disjoint_chains(bm)
            if not self.all_chains: core.report({'WARNING'}, "Select at least one curve")
            
            # --- NEW: Pre-initialize splines (in 2D later) ---
            # We don't have the plane yet, so we store them as raw world points for now
            self.splines = [] 
        else:
            self.all_chains = []

    def update(self, context, event, snap_point, snap_normal):
        if self.Xp is None:
            if self.all_chains:
                self.update_initial_plane(context, event, snap_point, snap_normal)
                self.splines = []
                self.spline_geom = []
                mw = context.edit_object.matrix_world if context.edit_object else Matrix.Identity(4)
                for chain in self.all_chains:
                    # Transform local coordinates to World Space
                    world_chain = [mw @ v for v in chain]
                    pts_2d = [world_to_plane(v, self.Xp, self.Yp) for v in world_chain]
                    s = Spline(pts_2d)
                    self.splines.append(s)
                    # Convert spline samples to 3D for preview
                    pts_3d = [plane_to_world(sample['pos'], self.Xp, self.Yp) for sample in s.samples]
                    self.spline_geom.append(pts_3d)
            else:
                return
        
        # Sync with global state for the renderer
        if self.spline_geom and not self.state.get("catmull_spline_previews"):
            self.state["catmull_spline_previews"] = self.spline_geom

        if not self.splines: return
        
        m_2d = None
        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        
        # A component snap is the actual endpoint target.  The snap marker is
        # produced by ModalManager before this method is called, so ignoring
        # snap_point here makes the preview appear to snap while the committed
        # line still uses the unsnapped mouse/ray position.
        geometry_target = None
        if (
            self.state.get("geometry_snap", False)
            and snap_point is not None
        ):
            geometry_target = snap_point.copy()

        # Calculate the unconstrained mouse target when no component is
        # snapped.  Perpendicular solving is still done in the drawing plane.
        ref_point = self.pivot if self.pivot else self.state.get("last_surface_hit") or Vector((0,0,0))
        raw_world_pos = Vector((0,0,0))
        if geometry_target is not None:
            raw_world_pos = geometry_target
        elif ref_point and self.Zp:
            denom = ray_vector.dot(self.Zp)
            if abs(denom) > 1e-6:
                t = (ref_point - ray_origin).dot(self.Zp) / denom
                raw_world_pos = ray_origin + ray_vector * t
            else:
                raw_world_pos = ref_point
        else:
            raw_world_pos = ref_point

        m_2d = world_to_plane(raw_world_pos, self.Xp, self.Yp)

        if self.stage == 0:
            best_d = float('inf')
            for i, s in enumerate(self.splines):
                u = s.getClosestU_Global(m_2d)
                p = s.evalCatmull(u)
                d = (p - m_2d).length_squared
                if d < best_d:
                    best_d = d
                    self.source_idx = i
                    self.current_u = u
            if self.source_idx != -1:
                s = self.splines[self.source_idx]
                minU = 0.0
                maxU = float(len(s.points) - 1) if not s.is_cyclic else float(len(s.points))
                
                # --- NEW: Responsive Refinement (Perpendicular search) ---
                self.current_u = s.solve_robust(m_2d, self.current_u, minU, maxU)
                
                h2 = s.evalCatmull(self.current_u)
                self.head_3d = plane_to_world(h2, self.Xp, self.Yp)
                
                # Calculate perpendicular line preview
                deriv = s.evalDeriv(self.current_u)
                if deriv.length_squared == 0: norm = Vector((1,0))
                else:
                    tan = deriv.normalized()
                    norm = Vector((-tan.y, tan.x))
                
                t2 = h2 + norm * (m_2d - h2).dot(norm)
                self.tail_3d = (
                    geometry_target.copy()
                    if geometry_target is not None
                    else plane_to_world(t2, self.Xp, self.Yp)
                )
                
                self.preview_pts = [self.head_3d, self.tail_3d]
                self.current = self.tail_3d
            return
    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            if self.source_idx != -1:
                # Refresh at the click location so a vertex/edge snap is
                # reflected in the points that are committed, even when the
                # last mouse-move preview was one event behind.
                self.update(context, event, snap_point, snap_normal)
                return 'FINISHED'
            return None
        return 'FINISHED'
        
    def handle_input(self, context, event):
        return super().handle_plane_lock_input(context, event)

class LineTool_PerpFromEdge(SurfaceDrawTool):
    """Create a line perpendicular to the nearest selected mesh edge."""

    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_PERP_FROM_EDGE"
        self.stage = 0
        self.edge_segments = []
        self.edge_segments_world = []
        self.edge_segments_2d = []
        self.edge_idx = -1
        self.current = None
        self.pivot = None

        obj = bpy.context.edit_object
        if obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            selected_edges = [edge for edge in bm.edges if edge.select]
            active = bm.select_history.active
            # If Blender has an active selected edge, it is the edge the user
            # explicitly picked.  Fall back to nearest-edge selection only
            # when the selection is a face or there is no active edge.
            if isinstance(active, bmesh.types.BMEdge) and active.select:
                selected_edges = [active]
            self.edge_segments = [
                (edge.verts[0].co.copy(), edge.verts[1].co.copy())
                for edge in selected_edges
            ]
            if not self.edge_segments:
                core.report({'WARNING'}, "Select at least one edge")

    @staticmethod
    def _closest_point_on_segment(point, start, end):
        edge = end - start
        length_squared = edge.length_squared
        if length_squared <= 1.0e-12:
            return start.copy()

        parameter = (point - start).dot(edge) / length_squared
        parameter = max(0.0, min(1.0, parameter))
        return start + edge * parameter

    @staticmethod
    def _segment_parameter(point, start, end):
        edge = end - start
        length_squared = edge.length_squared
        if length_squared <= 1.0e-12:
            return 0.0
        return max(
            0.0,
            min(1.0, (point - start).dot(edge) / length_squared),
        )

    def update(self, context, event, snap_point, snap_normal):
        if self.Xp is None:
            if not self.edge_segments:
                return

            self.update_initial_plane(context, event, snap_point, snap_normal)
            mw = (
                context.edit_object.matrix_world
                if context.edit_object
                else Matrix.Identity(4)
            )
            self.edge_segments_world = [
                (mw @ start, mw @ end)
                for start, end in self.edge_segments
            ]
            self.edge_segments_2d = [
                (
                    world_to_plane(start, self.Xp, self.Yp),
                    world_to_plane(end, self.Xp, self.Yp),
                )
                for start, end in self.edge_segments_world
            ]

        if not self.edge_segments_2d:
            return

        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

        # With Shift held, the source edge and free endpoint are both kept
        # under direct mouse control.  Releasing Shift restores component
        # snapping so the cursor can find and jump to another edge.
        geometry_target = None
        if (
            not event.shift
            and self.state.get("geometry_snap", False)
            and snap_point is not None
        ):
            geometry_target = snap_point.copy()
        elif event.shift:
            # Do not leave a snap marker visible when Shift is explicitly
            # disabling endpoint snapping for this tool.
            self.state["snap_point"] = None
            self.state["geometry_snap"] = False

        ref_point = (
            self.pivot
            if self.pivot
            else self.state.get("last_surface_hit") or Vector((0, 0, 0))
        )
        if geometry_target is not None:
            raw_world_pos = geometry_target
        elif ref_point and self.Zp:
            denom = ray_vector.dot(self.Zp)
            if abs(denom) > 1.0e-6:
                distance = (ref_point - ray_origin).dot(self.Zp) / denom
                raw_world_pos = ray_origin + ray_vector * distance
            else:
                raw_world_pos = ref_point
        else:
            raw_world_pos = ref_point

        mouse_2d = world_to_plane(raw_world_pos, self.Xp, self.Yp)

        # Shift freezes the current source edge.  With Shift released, keep
        # searching so the source can move to the next nearest selected edge.
        # This gives the user an intentional sequence: release Shift to find
        # another edge, then hold Shift to draw as far as desired from it.
        if event.shift and self.edge_idx != -1:
            best_idx = self.edge_idx
            if best_idx >= len(self.edge_segments_2d):
                return
            start, end = self.edge_segments_2d[best_idx]
            best_point = self._closest_point_on_segment(
                mouse_2d,
                start,
                end,
            )
        else:
            best_distance = float('inf')
            best_idx = -1
            best_point = None
            for idx, (start, end) in enumerate(self.edge_segments_2d):
                candidate = self._closest_point_on_segment(
                    mouse_2d,
                    start,
                    end,
                )
                distance = (candidate - mouse_2d).length_squared
                if distance < best_distance:
                    best_distance = distance
                    best_idx = idx
                    best_point = candidate

            # Keep the current nearest edge available to lock immediately
            # when Shift is pressed on the next mouse event.
            if best_idx != -1:
                self.edge_idx = best_idx

        if best_idx == -1 or best_point is None:
            return

        start, end = self.edge_segments_2d[best_idx]
        edge_vector = end - start
        if edge_vector.length_squared <= 1.0e-12:
            return

        edge_direction = edge_vector.normalized()
        perpendicular = Vector((-edge_direction.y, edge_direction.x))
        edge_parameter = self._segment_parameter(mouse_2d, start, end)
        source_start, source_end = self.edge_segments_world[best_idx]
        self.head_3d = source_start + (source_end - source_start) * edge_parameter

        # Build the perpendicular direction in 3D from the drawing-plane
        # basis.  This keeps the line anchored to the real edge point instead
        # of projecting that point onto a parallel plane through the origin.
        perpendicular_3d = (
            self.Xp * perpendicular.x + self.Yp * perpendicular.y
        )
        target_2d = (
            world_to_plane(geometry_target, self.Xp, self.Yp)
            if geometry_target is not None
            else mouse_2d
        )
        distance = (target_2d - best_point).dot(perpendicular)
        self.tail_3d = self.head_3d + perpendicular_3d * distance
        self.preview_pts = [self.head_3d, self.tail_3d]
        self.current = self.tail_3d

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.edge_segments:
            self.update(context, event, snap_point, snap_normal)
        if self.edge_idx != -1:
            # Recalculate at click time so the committed endpoint exactly
            # matches the current vertex/edge snap, if one is active.
            return 'FINISHED'
        return None

    def handle_input(self, context, event):
        return super().handle_plane_lock_input(context, event)

class LineTool_TangentFromCurve(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_TANGENT_FROM_CURVE"
        self.stage = 0
        self.splines, self.source_idx, self.current_u, self.snapped, self.locked = [], -1, 0.0, None, False
        self.current, self.pivot = None, None
        self.spline_geom = []
        
        obj = bpy.context.edit_object
        if obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.verts.index_update()
            chain_vertices = get_disjoint_chains(bm, include_verts=True)
            self.all_chains = [
                [vert.co.copy() for vert in chain]
                for chain in chain_vertices
            ]
            self.chain_signatures = [
                tuple(sorted({vert.index for vert in chain}))
                for chain in chain_vertices
            ]
            if not self.all_chains: core.report({'WARNING'}, "Select at least one curve")
        else:
            self.all_chains = []
            self.chain_signatures = []
        self.state["tangent_curve_available"] = len(self.all_chains) >= 2

    def update(self, context, event, snap_point, snap_normal):
        if self.Xp is None:
            if self.all_chains:
                self.update_initial_plane(context, event, snap_point, snap_normal)
                self.splines = []
                self.spline_geom = []
                mw = context.edit_object.matrix_world if context.edit_object else Matrix.Identity(4)
                for chain in self.all_chains:
                    world_chain = [mw @ v for v in chain]
                    pts_2d = [world_to_plane(v, self.Xp, self.Yp) for v in world_chain]
                    s = Spline(pts_2d)
                    self.splines.append(s)
                    pts_3d = [plane_to_world(sample['pos'], self.Xp, self.Yp) for sample in s.samples]
                    self.spline_geom.append(pts_3d)
            else:
                return
        
        # Sync with global state for the renderer
        if self.spline_geom and not self.state.get("catmull_spline_previews"):
            self.state["catmull_spline_previews"] = self.spline_geom

        if not self.splines: return
        
        m_2d = None
        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        
        # Stage 0 uses the mouse ray to choose the source curve.  Once the
        # source is chosen, an enabled vertex/edge/center snap must become the
        # actual tangent-line endpoint (the HUD snap marker is drawn there).
        tangent_curve_snap = (
            self.stage == 1
            and self.state.get("snap_tangent_curve", False)
            and len(self.splines) >= 2
        )
        geometry_target = None
        if (
            self.stage == 1
            and not tangent_curve_snap
            and self.state.get("geometry_snap", False)
            and snap_point is not None
        ):
            geometry_target = snap_point.copy()

        # Calculate the unconstrained mouse target when no component is
        # snapped.  Tangency is solved in the drawing plane in either case.
        ref_point = self.pivot if self.pivot else self.state.get("last_surface_hit") or Vector((0,0,0))
        raw_world_pos = Vector((0,0,0))
        if geometry_target is not None:
            raw_world_pos = geometry_target
        elif ref_point and self.Zp:
            denom = ray_vector.dot(self.Zp)
            if abs(denom) > 1e-6:
                t = (ref_point - ray_origin).dot(self.Zp) / denom
                raw_world_pos = ray_origin + ray_vector * t
            else:
                raw_world_pos = ref_point
        else:
            raw_world_pos = ref_point

        m_2d = world_to_plane(raw_world_pos, self.Xp, self.Yp)

        if self.stage == 0:
            # Stage 0: Select the source curve (Dot Preview)
            best_d = float('inf')
            for j, s in enumerate(self.splines):
                u = s.getClosestU_Global(m_2d)
                p = s.evalCatmull(u)
                d = (p - m_2d).length_squared
                if d < best_d:
                    best_d = d
                    self.source_idx = j
                    self.current_u = u
            if self.source_idx != -1:
                s = self.splines[self.source_idx]
                p = s.evalCatmull(self.current_u)
                self.head_3d = plane_to_world(p, self.Xp, self.Yp)
                self.state["tan_points"] = [self.head_3d]
                self.state["tan_source_chains"] = [
                    self.chain_signatures[self.source_idx]
                ]
                
                self.current = self.head_3d
                self.preview_pts = [self.head_3d]
            return

        # STAGE 1: SLIDE & SNAP (Tangent Logic)
        sourceS = self.splines[self.source_idx]

        # When Tangent Curve snapping is enabled, the mouse chooses a second
        # selected curve and seeds its Catmull parameter.  Solve both contact
        # parameters together so both preview endpoints lie on the Catmull
        # curves and the line is tangent at both ends.
        if tangent_curve_snap:
            target_idx = -1
            target_u = 0.0
            target_dist = float('inf')
            for idx, spline in enumerate(self.splines):
                if idx == self.source_idx:
                    continue
                candidate_u = spline.getClosestU_Global(m_2d)
                candidate = spline.evalCatmull(candidate_u)
                distance = (candidate - m_2d).length_squared
                if distance < target_dist:
                    target_dist = distance
                    target_idx = idx
                    target_u = candidate_u

            if target_idx != -1:
                targetS = self.splines[target_idx]
                source_u, target_u = solve_rhino_tangent(
                    sourceS,
                    targetS,
                    self.current_u,
                    target_u,
                )
                self.current_u = source_u

                source_point = sourceS.evalCatmull(source_u)
                target_point = targetS.evalCatmull(target_u)
                self.head_3d = plane_to_world(
                    source_point,
                    self.Xp,
                    self.Yp,
                )
                self.tail_3d = plane_to_world(
                    target_point,
                    self.Xp,
                    self.Yp,
                )
                self.state["tan_points"] = [
                    self.head_3d,
                    self.tail_3d,
                ]
                self.state["tan_source_chains"] = [
                    self.chain_signatures[self.source_idx],
                    self.chain_signatures[target_idx],
                ]
                self.state["snap_point"] = self.tail_3d
                self.state["geometry_snap"] = True
                self.preview_pts = [self.head_3d, self.tail_3d]
                self.current = self.tail_3d
                return
        
        constraintRadius = 2.0 # Adjust range
        suggestedU = sourceS.solveRollingContact(m_2d, self.current_u, self.current_u, constraintRadius)
        self.current_u = suggestedU
        
        # Calculate tangent line
        h2 = sourceS.evalCatmull(self.current_u)
        deriv = sourceS.evalDeriv(self.current_u)
        tan = deriv.normalized() if deriv.length_squared > 1e-8 else Vector((1,0))
        toM = m_2d - h2
        dist = toM.dot(tan)
        t2 = h2 + tan * dist
        
        self.head_3d = plane_to_world(h2, self.Xp, self.Yp)
        self.tail_3d = (
            geometry_target
            if geometry_target is not None
            else plane_to_world(t2, self.Xp, self.Yp)
        )
        self.state["tan_points"] = [self.head_3d]
        self.state["tan_source_chains"] = [
            self.chain_signatures[self.source_idx]
        ]
        
        self.preview_pts = [self.head_3d, self.tail_3d]
        self.current = self.tail_3d

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            if self.source_idx != -1:
                self.stage = 1
                return 'NEXT_STAGE'
            return None
        elif self.stage == 1:
            # Re-evaluate at the click coordinates.  This both avoids a stale
            # last-mouse-move preview and guarantees that the committed tail is
            # exactly the vertex/edge position advertised by the snap marker.
            self.update(context, event, snap_point, snap_normal)
            return 'FINISHED'
        return 'CANCELLED'
    def handle_input(self, context, event):
        return super().handle_plane_lock_input(context, event)

class LineTool_TanTan(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_TAN_TAN"
        self.stage = 0
        self.splines = []
        self.current, self.pivot = None, None
        self.first_click_info = None
        self.spline_geom = []
        
        obj = bpy.context.edit_object
        if obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.verts.index_update()
            chain_vertices = get_disjoint_chains(bm, include_verts=True)
            self.all_chains = [
                [vert.co.copy() for vert in chain]
                for chain in chain_vertices
            ]
            self.chain_signatures = [
                tuple(sorted({vert.index for vert in chain}))
                for chain in chain_vertices
            ]
            if len(self.all_chains) < 2:
                core.report({'WARNING'}, "Select at least 2 disjoint curves")
                self.all_chains = []
                self.chain_signatures = []
        else:
            self.all_chains = []
            self.chain_signatures = []

    def update(self, context, event, snap_point, snap_normal):
        # 1. Initialize Plane & Splines
        if self.Xp is None:
            if self.all_chains and len(self.all_chains) >= 2:
                self.update_initial_plane(context, event, snap_point, snap_normal)
                self.splines = []
                self.spline_geom = []
                mw = context.edit_object.matrix_world if context.edit_object else Matrix.Identity(4)
                for chain in self.all_chains:
                    world_chain = [mw @ v for v in chain]
                    pts_2d = [world_to_plane(v, self.Xp, self.Yp) for v in world_chain]
                    s = Spline(pts_2d)
                    self.splines.append(s)
                    pts_3d = [plane_to_world(sample['pos'], self.Xp, self.Yp) for sample in s.samples]
                    self.spline_geom.append(pts_3d)
            else:
                return
        
        # Sync with global state for the renderer
        if self.spline_geom and not self.state.get("catmull_spline_previews"):
            self.state["catmull_spline_previews"] = self.spline_geom

        if len(self.splines) < 2: return
        
        # 2. Mouse Input
        m_2d = None
        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        
        # --- FIXED: IGNORE SNAP_POINT AND CALCULATE PURE RAY-PLANE INTERSECTION ---
        # We need a reference point on the drawing plane for the intersection.
        ref_p = self.pivot if self.pivot else self.state.get("locked_plane_point") or self.state.get("last_surface_hit") or Vector((0,0,0))

        raw_world_pos = Vector((0,0,0))  # Initialize
        if self.Zp:
            denom = ray_vector.dot(self.Zp)
            if abs(denom) > 1e-6:
                t = (ref_p - ray_origin).dot(self.Zp) / denom
                raw_world_pos = ray_origin + ray_vector * t
            else:
                raw_world_pos = ref_p
        
        m_2d = world_to_plane(raw_world_pos, self.Xp, self.Yp)
        
        if self.stage == 0:
            best_dist = float('inf')
            best_idx = -1
            best_u = 0.0
            
            for i, s in enumerate(self.splines):
                u = s.getClosestU_Global(m_2d)
                p = s.evalCatmull(u)
                dist = (p - m_2d).length
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
                    best_u = u
            
            if best_idx != -1:
                self.current_candidate = {'idx': best_idx, 'u': best_u}
                
                # --- NEW: Immediate Preview in Stage 0 ---
                best_dist2 = float('inf')
                best_idx2 = -1
                best_u2 = 0.0
                for i, s in enumerate(self.splines):
                    if i == best_idx: continue
                    u = s.getClosestU_Global(m_2d)
                    p = s.evalCatmull(u)
                    dist = (p - m_2d).length
                    if dist < best_dist2:
                        best_dist2 = dist
                        best_idx2 = i
                        best_u2 = u
                
                if best_idx2 != -1:
                    s1 = self.splines[best_idx]
                    s2 = self.splines[best_idx2]
                    res_u1, res_u2 = solve_rhino_tangent(s1, s2, best_u, best_u2)
                    p1_2d = s1.evalCatmull(res_u1)
                    p2_2d = s2.evalCatmull(res_u2)
                    start_3d = plane_to_world(p1_2d, self.Xp, self.Yp)
                    end_3d = plane_to_world(p2_2d, self.Xp, self.Yp)
                    self.state["tan_points"] = [start_3d, end_3d]
                    self.state["tan_source_chains"] = [
                        self.chain_signatures[best_idx],
                        self.chain_signatures[best_idx2],
                    ]
                    self.preview_pts = [start_3d, end_3d]
                    self.current = end_3d
                else:
                    p2d = self.splines[best_idx].evalCatmull(best_u)
                    p3d = plane_to_world(p2d, self.Xp, self.Yp)
                    self.preview_pts = [p3d]
                    self.current = p3d

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            if hasattr(self, 'current_candidate') and self.current_candidate:
                return 'FINISHED'
            return None
        return 'CANCELLED'
        
    def handle_input(self, context, event):
        return super().handle_plane_lock_input(context, event)

class LineTool_PerpToTwoCurves(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_PERP_TO_TWO_CURVES"
        self.stage = 0
        self.splines = []
        self.current, self.pivot = None, None
        self.first_click_info = None
        self.spline_geom = []
        self.last_valid_perp = None # Store to prevent flickering
        
        obj = bpy.context.edit_object
        if obj and obj.type == 'MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            self.all_chains = get_disjoint_chains(bm)
            if len(self.all_chains) < 2:
                core.report({'WARNING'}, "Select at least 2 disjoint curves")
                self.all_chains = []
        else:
            self.all_chains = []

    def update(self, context, event, snap_point, snap_normal):
        # 1. Initialize Plane & Splines
        if self.Xp is None:
            if self.all_chains and len(self.all_chains) >= 2:
                self.update_initial_plane(context, event, snap_point, snap_normal)
                self.splines = []
                self.spline_geom = []
                mw = context.edit_object.matrix_world if context.edit_object else Matrix.Identity(4)
                for chain in self.all_chains:
                    world_chain = [mw @ v for v in chain]
                    pts_2d = [world_to_plane(v, self.Xp, self.Yp) for v in world_chain]
                    s = Spline(pts_2d)
                    self.splines.append(s)
                    pts_3d = [plane_to_world(sample['pos'], self.Xp, self.Yp) for sample in s.samples]
                    self.spline_geom.append(pts_3d)
            else:
                return
        
        # Sync with global state for the renderer
        if self.spline_geom and not self.state.get("catmull_spline_previews"):
            self.state["catmull_spline_previews"] = self.spline_geom

        if len(self.splines) < 2: return
        
        # 2. Mouse Input
        m_2d = None
        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        
        # --- CALCULATE PURE RAY-PLANE INTERSECTION (no geometry snap) ---
        ref_point = self.pivot if self.pivot else self.state.get("last_surface_hit") or Vector((0,0,0))
        raw_world_pos = Vector((0,0,0))
        if ref_point and self.Zp:
            denom = ray_vector.dot(self.Zp)
            if abs(denom) > 1e-6:
                t = (ref_point - ray_origin).dot(self.Zp) / denom
                raw_world_pos = ray_origin + ray_vector * t
            else:
                raw_world_pos = ref_point
        else:
            raw_world_pos = ref_point

        m_2d = world_to_plane(raw_world_pos, self.Xp, self.Yp)
        
        if self.stage == 0:
            best_dist = float('inf')
            best_idx = -1
            best_u = 0.0
            
            for i, s in enumerate(self.splines):
                u = s.getClosestU_Global(m_2d)
                p = s.evalCatmull(u)
                dist = (p - m_2d).length
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
                    best_u = u
            
            if best_idx != -1:
                self.current_candidate = {'idx': best_idx, 'u': best_u}
                
                # --- NEW: Immediate Preview in Stage 0 ---
                best_dist2 = float('inf')
                best_idx2 = -1
                best_u2 = 0.0
                for i, s in enumerate(self.splines):
                    if i == best_idx: continue
                    u = s.getClosestU_Global(m_2d)
                    p = s.evalCatmull(u)
                    dist = (p - m_2d).length
                    if dist < best_dist2:
                        best_dist2 = dist
                        best_idx2 = i
                        best_u2 = u
                
                if best_idx2 != -1:
                    s1 = self.splines[best_idx]
                    s2 = self.splines[best_idx2]
                    res_u1, res_u2 = solve_rhino_perp(s1, s2, best_u, best_u2)
                    
                    if res_u1 is not None:
                        # Valid solution found
                        p1_2d = s1.evalCatmull(res_u1)
                        p2_2d = s2.evalCatmull(res_u2)
                        start_3d = plane_to_world(p1_2d, self.Xp, self.Yp)
                        end_3d = plane_to_world(p2_2d, self.Xp, self.Yp)
                        self.last_valid_perp = (start_3d, end_3d)
                        self.preview_pts = [start_3d, end_3d]
                        self.current = end_3d
                    elif self.last_valid_perp:
                        # Use last valid solution to prevent flickering
                        self.preview_pts = list(self.last_valid_perp)
                        self.current = self.last_valid_perp[1]
                    else:
                        # Fallback to single point
                        p2d = self.splines[best_idx].evalCatmull(best_u)
                        p3d = plane_to_world(p2d, self.Xp, self.Yp)
                        self.preview_pts = [p3d]
                        self.current = p3d
                else:
                    p2d = self.splines[best_idx].evalCatmull(best_u)
                    p3d = plane_to_world(p2d, self.Xp, self.Yp)
                    self.preview_pts = [p3d]
                    self.current = p3d

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            if hasattr(self, 'current_candidate') and self.current_candidate:
                return 'FINISHED'
            return None
        return 'CANCELLED'
        
    def handle_input(self, context, event):
        return super().handle_plane_lock_input(context, event)
