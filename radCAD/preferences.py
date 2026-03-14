# radCAD/preferences.py

import bpy

def get_prefs():
    """Helper to access the addon preferences from anywhere."""
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None

class RADCAD_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # =========================================================================
    # UI STATE PROPERTIES
    # =========================================================================
    show_global_settings: bpy.props.BoolProperty(name="Global Settings", default=True)
    show_arc_settings: bpy.props.BoolProperty(name="1 Point Arc Settings", default=True)
    show_points_by_arc_settings: bpy.props.BoolProperty(name="Points by Arc Settings", default=True)
    show_arc_2pt_settings: bpy.props.BoolProperty(name="2 Point Arc Settings", default=True)
    show_line_settings: bpy.props.BoolProperty(name="Line Settings", default=True)

    # =========================================================================
    # SETTINGS PROPERTIES
    # =========================================================================
    
    use_axis_colors: bpy.props.BoolProperty(
        name="Use Axis Colors",
        description="When snapped to X, Y, or Z, the line will turn the axis color (Red, Green, Blue). If off, it stays the default color",
        default=True
    )

    compass_size: bpy.props.IntProperty(
        name="Compass Size",
        description="Controls the size of the visual compass circle is on your screen",
        default=125,
        min=50,
        max=500
    )

    # --- FONT SETTINGS ---
    font_size_hotkey: bpy.props.IntProperty(
        name="Hotkey Font Size",
        description="Change size of fonts",
        default=12,
        min=6,
        max=64
    )

    font_size_label: bpy.props.IntProperty(
        name="Label Font Size",
        description="Change size of fonts",
        default=10,
        min=6,
        max=64
    )

    preview_vertex_size: bpy.props.IntProperty(
        name="Preview Vertex Size",
        description="Customize the size of the points shown during drawing",
        default=3,
        min=1,
        max=50
    )
    
    # VISUAL COLORS (1-Point)
    color_arc_start: bpy.props.FloatVectorProperty(
        name="Start Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.2, 0.8, 0.2, 1.0), # Green, matches End Color
        description="These are your arc start and end point previews"
    )

    color_arc_end: bpy.props.FloatVectorProperty(
        name="End Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.2, 0.8, 0.2, 1.0), 
        description="These are your arc start and end point previews"
    )

    # VISUAL COLORS (2-Point)
    color_arc_2pt_chord: bpy.props.FloatVectorProperty(
        name="Chord Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.2, 0.8, 0.2, 1.0), # Green
        description="This is straight line connecting your two start/end points"
    )

    color_arc_2pt_height: bpy.props.FloatVectorProperty(
        name="Height Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.2, 0.8, 0.2, 1.0), # Green
        description="This colors the line that shoots up from the middle to set the arc's height"
    )

    # VISUAL COLORS (Points by Arc)
    color_points_by_arc_1: bpy.props.FloatVectorProperty(
        name="Arc 1 Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.2, 0.8, 0.2, 1.0), # Green
        description="Color for the first reference arc"
    )

    color_points_by_arc_2: bpy.props.FloatVectorProperty(
        name="Arc 2 Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.2, 0.8, 0.2, 1.0), # Green
        description="Color for the second reference arc"
    )

    color_points_by_arc_start: bpy.props.FloatVectorProperty(
        name="Start Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5, 1.0), # Grey
        description="Color for the radius guide line"
    )

    color_points_by_arc_end: bpy.props.FloatVectorProperty(
        name="End Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5, 1.0), # Grey
        description="Color for the angle guide line"
    )

    points_by_arc_crosshair_size: bpy.props.IntProperty(
        name="Crosshair Size",
        description="Size of the crosshair marker during second radius setup",
        default=25,
        min=1,
        max=500
    )

    points_by_arc_square_size: bpy.props.IntProperty(
        name="Intersection Square Size",
        description="Size of the final result intersection square",
        default=3,
        min=1,
        max=100
    )

    # --- SNAP MARKER SETTINGS (1-POINT) ---
    # Removed snap_marker_type (Hardcoded to 'X')
    
    snap_marker_size: bpy.props.IntProperty(
        name="Marker Size",
        description="Customize the little target thingy that shows up where you are snapping",
        default=6,
        min=1,
        max=50
    )
    
    snap_marker_color: bpy.props.FloatVectorProperty(
        name="Marker Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 0.8, 0.0, 1.0),
        description="Customize the little target thingy that shows up where you are snapping"
    )

    # Removed snap_line_width (Defaults to 1.0 in logic)
    snap_line_color: bpy.props.FloatVectorProperty(
        name="Snap Pointer Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 1.0, 0.0, 0.7),
        description="Colors the little leash connecting your mouse to the pivot. Make it bright so you never lose track of it"
    )
    
    # --- SNAP MARKER SETTINGS (2-POINT) ---
    # Removed snap_marker_type_2pt (Hardcoded to 'X')
    
    snap_marker_size_2pt: bpy.props.IntProperty(
        name="Marker Size",
        description="Customize the little target thingy that shows up where you are snapping",
        default=6,
        min=1,
        max=50
    )
    
    snap_marker_color_2pt: bpy.props.FloatVectorProperty(
        name="Marker Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 0.8, 0.0, 1.0),
        description="Customize the little target thingy that shows up where you are snapping"
    )

    # Removed snap_line_width_2pt
    snap_line_color_2pt: bpy.props.FloatVectorProperty(
        name="Snap Pointer Line Color",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 1.0, 0.0, 0.7),
        description="Colors the little leash connecting your mouse to the pivot. Make it bright so you never lose track of it"
    )
    
    # PARAMETER OVERLAY OFFSETS
    overlay_offset_x: bpy.props.IntProperty(
        name="Param X Offset",
        description="This nudges the text labels (like 'R: 5m') away from the center point",
        default=75,
        min=-1000,
        max=1000
    )

    overlay_offset_y: bpy.props.IntProperty(
        name="Param Y Offset",
        description="This nudges the text labels (like 'R: 5m') away from the center point",
        default=0,
        min=-1000,
        max=1000
    )

    # HOTKEYS PANEL OFFSETS
    show_hotkeys: bpy.props.BoolProperty(
        name="Show Hotkeys",
        description="Toggles that little cheat sheet overlay",
        default=True
    )
    
    hotkeys_offset_x: bpy.props.IntProperty(
        name="Hotkeys X",
        description="This just moves the hotkey cheat sheet around",
        default=1000,
        min=0,
        max=2000
    )
    
    hotkeys_offset_y: bpy.props.IntProperty(
        name="Hotkeys Y (From Top)",
        description="This just moves the hotkey cheat sheet around",
        default=20,
        min=0,
        max=2000
    )

    # SNAPPING
    angle_snap_type: bpy.props.EnumProperty(
        name="Angle Snap Increment",
        items=[
            ('1', "1 Degree", ""),
            ('2', "2 Degrees", ""),
            ('3', "3 Degrees", ""),
            ('5', "5 Degrees", ""),
            ('10', "10 Degrees", ""),
            ('15', "15 Degrees", ""),
            ('22.5', "22.5 Degrees", ""),
            ('30', "30 Degrees", ""),
            ('45', "45 Degrees", ""),
            ('90', "90 Degrees", ""),
        ],
        default='15',
        description="This sets the step size for the angle snapping of the compass"
    )

    angle_snap_type_rad: bpy.props.EnumProperty(
        name="Radian Snap Increment",
        items=[
            ('1', "Pi/180 (1°)", ""),
            ('2', "Pi/90 (2°)", ""),
            ('3', "Pi/60 (3°)", ""),
            ('5', "Pi/36 (5°)", ""),
            ('10', "Pi/18 (10°)", ""),
            ('15', "Pi/12 (15°)", ""),
            ('22.5', "Pi/8 (22.5°)", ""),
            ('30', "Pi/6 (30°)", ""),
            ('45', "Pi/4 (45°)", ""),
            ('60', "Pi/3 (60°)", ""),
            ('90', "Pi/2 (90°)", ""),
        ],
        default='15',
        description="This sets the step size for the angle snapping of the compass"
    )

    use_angle_snap: bpy.props.BoolProperty(name="Use Angle Snap", default=True)
    use_radians: bpy.props.BoolProperty(name="Use Radians", default=False)
    
    snap_strength: bpy.props.FloatProperty(
        name="Snap Strength", 
        default=6.0, 
        min=0.1, 
        max=45.0,
        description="Think of this as how 'sticky' the angle snapping feels"
    )

    snap_to_verts: bpy.props.BoolProperty(name="Vertices", default=True)
    snap_to_edges: bpy.props.BoolProperty(name="Edges", default=True)
    snap_to_faces: bpy.props.BoolProperty(name="Faces", default=True)

    display_precision: bpy.props.IntProperty(
        name="Display Precision", 
        default=3, 
        min=0, 
        max=9,
        description="Sets the number of decimal places for metric values"
    )

    weld_radius: bpy.props.FloatProperty(
        name="Weld Radius", 
        default=0.001, 
        precision=5, 
        min=0.00001, 
        max=1.0,
        description="This is basically the magnet strength for auto-welding"
    )
    weld_to_faces: bpy.props.BoolProperty(name="Cut Faces (Knife Project)", default=True)

    # --- DEBUG / Z-FIGHTING PROPS ---
    lift_compass: bpy.props.FloatProperty(
        name="Compass Lift (Ortho)",
        description="Glitch fix for flat views. If your compass looks glitched out on surfaces, this pulls it up so it sits on top",
        default=4.0,
        min=0.0, max=500.0
    )
    lift_arc: bpy.props.FloatProperty(
        name="Arc Line Lift (Ortho)",
        description="Glitch fix for flat views. If your drawing lines look glitched out on surfaces, this pulls them up so they sit on top",
        default=20.0,
        min=0.0, max=5000.0
    )
    lift_perspective: bpy.props.FloatProperty(
        name="Perspective Bias (%)",
        description="Glitch fix for 3D views. If your compass looks glitched out on surfaces, this pulls it up so it sits on top",
        default=0.2,
        min=0.0, max=10.0,
        precision=3
    )

    # =========================================================================
    # DRAWING HELPERS
    # =========================================================================
    def draw_section_header(self, layout, title, prop_name, icon='NONE'):
        """Draws a collapsible box header."""
        box = layout.box()
        row = box.row(align=True)
        
        is_expanded = getattr(self, prop_name)
        icon_state = "TRIA_DOWN" if is_expanded else "TRIA_RIGHT"
        
        row.prop(self, prop_name, icon=icon_state, text="", icon_only=True, emboss=False)
        row.label(text=title, icon=icon)
        
        if is_expanded:
            return box.column(align=True)
        return None

    def draw(self, context):
        layout = self.layout
        
        # ==================================
        # 1. GLOBAL SETTINGS (Collapsible)
        # ==================================
        col_global = self.draw_section_header(layout, "Global Settings", "show_global_settings", icon='PREFERENCES')
        
        if col_global:
            # Save Button
            row_save = col_global.row()
            row_save.scale_y = 1.5
            row_save.operator("wm.save_userpref", text="Save Preferences", icon='FILE_TICK')
            
            col_global.separator()

            # --- Weld Parameters ---
            col_global.label(text="Weld / Auto-Connect:", icon='AUTOMERGE_ON')
            
            split_weld = col_global.split(factor=0.5, align=True)
            row_label = split_weld.row()
            row_label.separator()
            row_label.label(text="Search Radius (Magnet):", icon='BLANK1')
            split_weld.prop(self, "weld_radius", text="")
            
            col_global.separator(factor=2.5)

            # --- Geometry Snaps ---
            col_global.label(text="Geometry Snaps:", icon='SNAP_PEEL_OBJECT')
            
            split_geo = col_global.split(factor=0.5, align=True)
            row_label = split_geo.row()
            row_label.separator()
            row_label.label(text="Snap Strength:", icon='BLANK1')
            split_geo.prop(self, "snap_strength", text="")
            
            col_global.separator(factor=2.5)

            # --- Z-Fighting Tweaks ---
            col_global.label(text="Z-Fighting Tweaks", icon='OPTIONS')
            
            split_z1 = col_global.split(factor=0.5, align=True)
            row_z1 = split_z1.row()
            row_z1.separator()
            row_z1.label(text="Ortho Lifts (Compass/Arc):", icon='BLANK1')
            row_props1 = split_z1.row(align=True)
            row_props1.prop(self, "lift_compass", text="Compass")
            row_props1.prop(self, "lift_arc", text="Arc")
            
            split_z2 = col_global.split(factor=0.5, align=True)
            row_z2 = split_z2.row()
            row_z2.separator()
            row_z2.label(text="Perspective Lift %:", icon='BLANK1')
            split_z2.prop(self, "lift_perspective", text="")
            
            col_global.separator(factor=2.5)

            # --- Hotkeys Helper ---
            col_global.label(text="Hotkeys Helper:", icon='HELP')
            
            split_keys = col_global.split(factor=0.5, align=True)
            row_label = split_keys.row()
            row_label.separator()
            row_label.label(text="Show Panel:", icon='BLANK1')
            split_keys.prop(self, "show_hotkeys", text="Enable")
            
            if self.show_hotkeys:
                split_pos = col_global.split(factor=0.5, align=True)
                row_label = split_pos.row()
                row_label.separator()
                row_label.label(text="Screen Position:", icon='BLANK1')
                
                row_xy = split_pos.row(align=True)
                row_xy.prop(self, "hotkeys_offset_x", text="X (Right)")
                row_xy.prop(self, "hotkeys_offset_y", text="Y (Top)")

            col_global.separator(factor=2.5)

            # --- Metric Display (MOVED HERE) ---
            col_global.label(text="Metric Display:", icon='DOT')
            
            split_metric = col_global.split(factor=0.5, align=True)
            row_label = split_metric.row()
            row_label.separator()
            row_label.label(text="Decimal Precision:", icon='BLANK1')
            split_metric.prop(self, "display_precision", text="")

            col_global.separator()

        # ==================================
        # 2. POINTS BY ARC SETTINGS
        # ==================================
        icon_val_points = 0
        try:
            from . import panel
            pcoll = getattr(panel, "preview_collection", None)
            if pcoll and "point_by_arcs" in pcoll:
                icon_val_points = pcoll["point_by_arcs"].icon_id
        except ImportError:
            pass

        box_points = layout.box()
        row_header_points = box_points.row(align=True)
        
        is_expanded_points = self.show_points_by_arc_settings
        icon_state_points = "TRIA_DOWN" if is_expanded_points else "TRIA_RIGHT"
        row_header_points.prop(self, "show_points_by_arc_settings", icon=icon_state_points, text="", icon_only=True, emboss=False)
        
        if icon_val_points:
            row_header_points.label(text="Points by Arc Settings", icon_value=icon_val_points)
        else:
            row_header_points.label(text="Points by Arc Settings", icon='GP_SELECT_POINTS')

        if is_expanded_points:
            split_main = box_points.split(factor=0.02)
            split_main.label(text="") 
            col = split_main.column(align=True)
            
            # --- Visual Colors ---
            col.label(text="Visuals (Reference Arcs):", icon='COLOR')
            
            # Arc 1
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Arc 1 Color:", icon='BLANK1')
            split.prop(self, "color_points_by_arc_1", text="")
            
            # Arc 2
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Arc 2 Color:", icon='BLANK1')
            split.prop(self, "color_points_by_arc_2", text="")
            
            # Start Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Start Line Color:", icon='BLANK1')
            split.prop(self, "color_points_by_arc_start", text="")
            
            # End Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="End Line Color:", icon='BLANK1')
            split.prop(self, "color_points_by_arc_end", text="")
            
            col.separator(factor=2.0)

            # --- Marker Sizes ---
            col.label(text="Marker Sizes:", icon='SNAP_ON')
            
            # Crosshair
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Crosshair Size (Setup):", icon='BLANK1')
            split.prop(self, "points_by_arc_crosshair_size", text="")
            
            # Square
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Intersection Square (Final):", icon='BLANK1')
            split.prop(self, "points_by_arc_square_size", text="")
            
            col.separator()

        # ==================================
        # 3. LINE SETTINGS
        # ==================================
        box_line = layout.box()
        row_header_line = box_line.row(align=True)
        
        is_expanded_line = self.show_line_settings
        icon_state_line = "TRIA_DOWN" if is_expanded_line else "TRIA_RIGHT"
        row_header_line.prop(self, "show_line_settings", icon=icon_state_line, text="", icon_only=True, emboss=False)
        row_header_line.label(text="Line Settings", icon='LINCURVE')

        if is_expanded_line:
            split_main = box_line.split(factor=0.02)
            split_main.label(text="") 
            col = split_main.column(align=True)
            
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Snapping Visuals:", icon='BLANK1')
            split.prop(self, "use_axis_colors", text="Use Axis Colors")
            
            col.separator()

        # ==================================
        # 4. 1 POINT ARC SETTINGS (Collapsible Wrapper)
        # ==================================
        icon_val = 0
        try:
            from . import panel
            pcoll = getattr(panel, "preview_collection", None)
            if pcoll and "arc_1_point" in pcoll:
                icon_val = pcoll["arc_1_point"].icon_id
        except ImportError:
            pass
            
        box_arc = layout.box()
        row_header = box_arc.row(align=True)
        
        is_expanded = self.show_arc_settings
        icon_state = "TRIA_DOWN" if is_expanded else "TRIA_RIGHT"
        row_header.prop(self, "show_arc_settings", icon=icon_state, text="", icon_only=True, emboss=False)
        
        if icon_val:
            row_header.label(text="1 Point Arc Settings", icon_value=icon_val)
        else:
            row_header.label(text="1 Point Arc Settings", icon='CURVE_DATA')

        if is_expanded:
            split_main = box_arc.split(factor=0.02)
            split_main.label(text="") 
            col = split_main.column(align=True)
            
            # --- A. Compass & Fonts ---
            col.label(text="Display & Fonts:", icon='FONT_DATA')
            
            # Compass Size
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Display Compass Size:", icon='BLANK1')
            split.prop(self, "compass_size", text="")
            
            col.separator()
            
            # Font Hotkey
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Hotkey Font Size:", icon='BLANK1')
            split.prop(self, "font_size_hotkey", text="")
            
            # Font Label
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Label/Param Font Size:", icon='BLANK1')
            split.prop(self, "font_size_label", text="")

            # Preview Vertex Size
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Preview Vertex Size:", icon='BLANK1')
            split.prop(self, "preview_vertex_size", text="")
            
            col.separator(factor=2.0)

            # --- B. Angle Snap ---
            col.label(text="Angle Snap:", icon='DRIVER_ROTATIONAL_DIFFERENCE')
            
            # Increment
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Increment:", icon='BLANK1')
            if self.use_radians:
                split.prop(self, "angle_snap_type_rad", text="")
            else:
                split.prop(self, "angle_snap_type", text="")

            # Radians Checkbox
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Angle Units:", icon='BLANK1')
            split.prop(self, "use_radians", text="Show Angle in Radians")

            col.separator(factor=2.0)

            # --- C. Snap Guides ---
            col.label(text="Snap Guides:", icon='SNAP_ON')
            
            # Marker
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Marker (Size/Color):", icon='BLANK1') # Renamed label
            
            row_props = split.row(align=True)
            # REMOVED SHAPE SELECTOR UI
            row_props.prop(self, "snap_marker_size", text="")
            row_props.prop(self, "snap_marker_color", text="")

            # Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Pointer Line Color:", icon='BLANK1')
            split.prop(self, "snap_line_color", text="")

            col.separator(factor=2.0)
            
            # --- D. Overlay Position ---
            col.label(text="Overlay Position:", icon='OVERLAY')
            
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Offset (Compass):", icon='BLANK1')
            sub = split.row(align=True)
            sub.prop(self, "overlay_offset_x", text="X")
            sub.prop(self, "overlay_offset_y", text="Y")
            
            col.separator(factor=2.0) 

            # --- I. Visuals ---
            col.label(text="Visuals (Preview Lines):", icon='COLOR')
            
            # Start Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Start Line Color:", icon='BLANK1')
            split.prop(self, "color_arc_start", text="")
            
            # End Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="End Line Color:", icon='BLANK1')
            split.prop(self, "color_arc_end", text="")
            
            col.separator()

        # ==================================
        # 5. 2 POINT ARC SETTINGS
        # ==================================
        icon_val_2pt = 0
        try:
            from . import panel
            pcoll = getattr(panel, "preview_collection", None)
            if pcoll and "arc_2_point" in pcoll:
                icon_val_2pt = pcoll["arc_2_point"].icon_id
        except ImportError:
            pass

        box_2pt = layout.box()
        row_header_2pt = box_2pt.row(align=True)
        
        is_expanded_2pt = self.show_arc_2pt_settings
        icon_state_2pt = "TRIA_DOWN" if is_expanded_2pt else "TRIA_RIGHT"
        row_header_2pt.prop(self, "show_arc_2pt_settings", icon=icon_state_2pt, text="", icon_only=True, emboss=False)
        
        if icon_val_2pt:
            row_header_2pt.label(text="2 Point Arc Settings", icon_value=icon_val_2pt)
        else:
            row_header_2pt.label(text="2 Point Arc Settings", icon='CURVE_DATA')

        if is_expanded_2pt:
            split_main = box_2pt.split(factor=0.02)
            split_main.label(text="") 
            col = split_main.column(align=True)
            
            # --- C. Snap Guides (2-Point) ---
            col.label(text="Snap Guides:", icon='SNAP_ON')
            
            # Marker
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Marker (Size/Color):", icon='BLANK1') 
            
            row_props = split.row(align=True)
            # REMOVED SHAPE SELECTOR UI
            row_props.prop(self, "snap_marker_size_2pt", text="")
            row_props.prop(self, "snap_marker_color_2pt", text="")

            # Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Pointer Line Color:", icon='BLANK1')
            
            row_props = split.row(align=True)
            row_props.prop(self, "snap_line_color_2pt", text="")

            col.separator(factor=2.0)
            
            col.label(text="Visuals (Preview Lines):", icon='COLOR')
            
            # Chord Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Chord Line (P1-P2):", icon='BLANK1')
            row_props = split.row(align=True)
            row_props.prop(self, "color_arc_2pt_chord", text="")
            
            # Height Line
            split = col.split(factor=0.5, align=True)
            row_label = split.row()
            row_label.separator()
            row_label.label(text="Height Line (Mid-Peak):", icon='BLANK1')
            row_props = split.row(align=True)
            row_props.prop(self, "color_arc_2pt_height", text="")
            
            col.separator()

# =========================================================================
# REGISTRATION
# =========================================================================
def register():
    bpy.utils.register_class(RADCAD_Preferences)

def unregister():
    bpy.utils.unregister_class(RADCAD_Preferences)