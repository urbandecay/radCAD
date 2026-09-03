# radCAD Dimensions

The Dimension panel exposes SketchUp-style linear and angular dimension workflows.
Linear dimensions use a three-click workflow:

1. Pick the first measured point.
2. Pick the second measured point.
3. Place the dimension line.

The resulting annotation is drawn entirely as a persistent GPU/HUD overlay.
It creates no visible Empty, Curve, Font, or renderable geometry. A hidden data
object retains the saved settings and endpoint references, and dimensions are
selected for editing from the Dimension panel. Vertex, edge, and face-surface
picks retain mesh element weights, so endpoints follow edits and transforms
whenever the source topology remains compatible.

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
