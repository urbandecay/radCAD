# radCAD Dimensions

The Dimension panel exposes SketchUp-style linear and angular dimension workflows.
Linear dimensions use a three-click workflow:

1. Pick the first measured point.
2. Pick the second measured point.
3. Pick the placement point.

Faces shared by the measured mesh components define the available placement
planes, including live Edit Mode geometry. Placement defaults to an offset
along a supporting face's normal, like extruding that face. Press `N` to toggle
to an offset within the face. When multiple faces support an edge, the cursor
chooses the closest legal offset in the selected mode. Both modes retain the
measured span and its true length, including on rotated or beveled meshes.
On a right-angle cube edge the two modes offer the same set of planes.
Continue pressing `N` to cycle through valid X/Y/Z extension directions within
the supporting faces, then back to normal mode. These projected states measure
the perpendicular distance between the parallel extension lines. For a sloped
edge, vertical (Z) extensions therefore report the shorter horizontal span.
Directions that collapse the measurement or merely repeat its true length are
omitted. The HUD shows the active state.
With no supporting face, the existing free-space axis inference remains
available. The placement point is saved with the dimension, and linear
dimensions remain aligned to the two measured points.

The resulting annotation is drawn entirely as a persistent GPU/HUD overlay.
It creates no visible Empty, Curve, Font, or renderable geometry. A hidden data
object retains the saved settings and endpoint references, and dimensions are
selected for editing from the Dimension panel. Vertex, edge, and face-surface
picks retain mesh element weights, so endpoints follow edits and transforms
whenever the source topology remains compatible.

Each committed linear dimension remains visible. Multiple linear dimensions
can measure the same edge; their placement offsets and saved styles remain
independent. Existing dimensions are only removed explicitly through the
dimension delete/edit controls.

Module responsibilities:

- `../operators/op_dimension_linear.py`: linear-dimension creation entry point.
- `../operators/op_dimension_angle.py`: angle-dimension creation entry point.
- `../operators/op_dimension_edit.py`: shared dimension selection, editing, and deletion entry points.
- `linear/`: linear measurement geometry and length formatting.
- `angular/`: angular measurement geometry and angle formatting.
- `operator.py`: shared interaction implementation used by those operator entry points.
- `geometry.py` and `formatting.py`: compatibility exports for older internal imports.
- `model.py`: hidden persistent data and associative anchor resolution.
- `drawing.py`: modal preview plus persistent POST_PIXEL dimension overlays.
- `snapping.py`: existing radCAD snap-engine integration and plane projection.
- `properties.py`: saved dimension/style properties.
- `formatting.py`: scene-unit label formatting.
- `updater.py`: dependency-graph and file-load refresh handlers.

Diagnostics:

Dimension lifecycle logging is enabled by default while overlay issues are
being diagnosed. Messages use the `[radCAD Dimension DEBUG]` prefix and are
also retained in `bpy.app.driver_namespace["radcad_dimension_debug_log"]` and
appended to `/tmp/radcad_dimension_debug.log`.
The log records modal stages, preview generations, draw-handler ownership, the
complete persistent dimension list before and after each commit, and the
source/geometry of each 2D dimension draw. During a hot reload, callbacks from
older drawing-module copies are quarantined so they cannot continue painting a
second annotation. Disable diagnostics from Blender's Python Console with:

```python
bpy.app.driver_namespace["radcad_dimension_debug_enabled"] = False
```
