import bpy
import bmesh
import math
from mathutils import Vector
from ..modal_state import state
from ..modal_core import begin_modal, modal_arc_common
from .circle_tools import get_selected_edge_chains

def get_fit_data_from_points(pts, Xp, Yp, pivot):
    if len(pts) < 2: return None
    
    def p2d(p): 
        d = p - pivot
        return Vector((d.dot(Xp), d.dot(Yp), 0))
    
    vp1, vp2 = p2d(pts[0]), p2d(pts[1])
    vp3 = p2d(pts[len(pts)//2])
    
    D = 2 * (vp1.x * (vp2.y - vp3.y) + vp2.x * (vp3.y - vp1.y) + vp3.x * (vp1.y - vp2.y))
    
    if abs(D) < 1e-7 or len(pts) < 3:
        return ((vp1, vp2), 'LINE')
    
    ux = ((vp1.x**2 + vp1.y**2)*(vp2.y - vp3.y) + (vp2.x**2 + vp2.y**2)*(vp3.y - vp1.y) + (vp3.x**2 + vp3.y**2)*(vp1.y - vp2.y)) / D
    uy = ((vp1.x**2 + vp1.y**2)*(vp3.x - vp2.x) + (vp2.x**2 + vp2.y**2)*(vp1.x - vp3.x) + (vp3.x**2 + vp3.y**2)*(vp2.x - vp1.x)) / D
    cen = Vector((ux, uy, 0))
    return ((cen, (cen - vp1).length), 'CIRCLE')

def solve_apollonius(c1, c2, c3, s1, s2, s3):
    try:
        x1, y1, r1 = c1[0], c1[1], c1[2] * s1
        x2, y2, r2 = c2[0], c2[1], c2[2] * s2
        x3, y3, r3 = c3[0], c3[1], c3[2] * s3
        v11, v12 = 2*x2 - 2*x1, 2*y2 - 2*y1
        v13 = x1**2 - x2**2 + y1**2 - y2**2 - r1**2 + r2**2
        v14, v21, v22 = 2*r2 - 2*r1, 2*x3 - 2*x2, 2*y3 - 2*y2
        v23 = x2**2 - x3**2 + y2**2 - y3**2 - r2**2 + r3**2
        v24 = 2*r3 - 2*r2
        if abs(v11) < 1e-9: return None
        w12, w13, w14 = v12/v11, v13/v11, v14/v11
        w22 = v22 - v21*w12
        w23, w24 = v23 - v21*w13, v24 - v21*w14
        if abs(w22) < 1e-9: return None
        P, Q = -w23/w22, w24/w22
        M, N = -w12*P - w13, w14 - w12*Q
        a, b = N*N + Q*Q - 1, 2*M*N - 2*N*x1 + 2*P*Q - 2*Q*y1 + 2*r1
        c = x1*x1 + M*M - 2*M*x1 + P*P + y1*y1 - 2*P*y1 - r1*r1
        D = b*b - 4*a*c
        if D < 0: return None
        rs = (-b - math.sqrt(D)) / (2*a)
        if rs < 0: rs = (-b + math.sqrt(D)) / (2*a)
        return (M + N * rs, P + Q * rs, abs(rs))
    except: return None

class CircleTool_TanTanTan_Circles:
    def __init__(self, manager):
        self.manager = manager
        self.inputs = [] 
        self.stage = 0
        self.pivot = None
        self.current = None
        self.segments = 32
        self.preview_pts = []
        self.Xp, self.Yp, self.Zp = Vector((1,0,0)), Vector((0,1,0)), Vector((0,0,1))
        
        state.update({
            "stage": 0, 
            "preview_pts": [],   
            "visual_pts": [],    
            "tan_solutions": [], 
            "solution_index": 0, 
            "choosing_solution": False, 
            "tan_input_overlays": [], 
            "tan_solution_active": False
        })

    def solve(self):
        Xp, Yp, pivot = self.Xp, self.Yp, state.get("pivot")
        c2d, overlays = [], []
        
        for data, typ in self.inputs:
            if typ == 'CIRCLE':
                cen, rad = data
                c2d.append((cen.x, cen.y, rad))
                overlays.append([pivot + Xp*(cen.x + math.cos(a)*rad) + Yp*(cen.y + math.sin(a)*rad) for a in [i*math.pi*2/64 for i in range(65)]])
            else:
                p1, p2 = data
                overlays.append([pivot + Xp*p1.x + Yp*p1.y, pivot + Xp*p2.x + Yp*p2.y])
                dv = (p2 - p1).normalized()
                nm = Vector((-dv.y, dv.x))
                c_huge = p1 + nm * 10000.0
                c2d.append((c_huge.x, c_huge.y, 10000.0))
        
        state["tan_input_overlays"] = overlays
        
        sols = []
        signs = [(1,1,1),(-1,-1,-1),(1,1,-1),(1,-1,1),(-1,1,1),(1,-1,-1),(-1,1,-1),(-1,-1,1)]
        for s in signs:
            res = solve_apollonius(c2d[0], c2d[1], c2d[2], *s)
            if res: sols.append((pivot + Xp*res[0] + Yp*res[1], res[2]))
        
        sols.sort(key=lambda x: x[1])
        state["tan_solutions"] = sols

    def update(self, context, event, snap_pt, snap_normal):
        if state.get("Xp"): self.Xp, self.Yp = state["Xp"], state["Yp"]
        if state["choosing_solution"] and state["tan_solutions"]:
            idx = state["solution_index"] % len(state["tan_solutions"])
            c, r = state["tan_solutions"][idx]
            
            state["visual_pts"] = [c + self.Xp*math.cos(a)*r + self.Yp*math.sin(a)*r for a in [i*math.pi*2/128 for i in range(129)]]
            self.preview_pts = [c + self.Xp*math.cos(a)*r + self.Yp*math.sin(a)*r for a in [i*math.pi*2/self.segments for i in range(self.segments+1)]]
            state["preview_pts"] = self.preview_pts
            state["tan_solution_active"] = True

    def handle_click(self, context, event, snap_pt, snap_normal, button_id=None):
        if state["choosing_solution"]: return 'FINISHED'
        return 'RUNNING_MODAL'

    def handle_input(self, context, event):
        if state["choosing_solution"] and event.type == 'TAB' and event.value == 'PRESS':
            state["solution_index"] += 1
            return True
        return False

class VIEW3D_OT_circle_tan_tan_tan_circles(bpy.types.Operator):
    bl_idname = "view3d.radcad_circle_tan_tan_tan_circles"
    bl_label = "Circle Tan-Tan-Tan (Circles)"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    def modal(self, context, event): return modal_arc_common(self, context, event)
    def invoke(self, context, event):
        state["tool_mode"] = "CIRCLE_TAN_TAN_TAN_CIRCLES"
        obj = context.edit_object
        
        chains = get_selected_edge_chains(obj)
        
        if len(chains) == 3:
            bpy.ops.mesh.select_all(action='DESELECT')
            ret = begin_modal(self, context, event)
            if ret == {'RUNNING_MODAL'}:
                rv3d = context.region_data
                view_inv = rv3d.view_matrix.inverted()
                Xp, Yp = view_inv.col[0].xyz.normalized(), view_inv.col[1].xyz.normalized()
                
                state.update({"Xp": Xp, "Yp": Yp, "pivot": obj.matrix_world @ chains[0][0][0]})
                tool = self.manager.active_tool
                tool.Xp, tool.Yp = Xp, Yp
                
                for chain_pts, is_closed in chains:
                    pts_world = [obj.matrix_world @ p for p in chain_pts]
                    res = get_fit_data_from_points(pts_world, Xp, Yp, state["pivot"])
                    if res: tool.inputs.append(res)
                
                if len(tool.inputs) == 3:
                    tool.solve()
                    if state["tan_solutions"]: state["choosing_solution"] = True
            return ret
        return begin_modal(self, context, event)