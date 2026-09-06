"""Diagnostics and lifecycle guards for dimension viewport overlays.

The dimension tool is modal while its persistent renderer is a separate
Blender draw handler.  Keeping a small amount of lifecycle state outside the
module makes stale callbacks observable and harmless across add-on reloads.
"""

import gc
import time
import types

import bpy


DEBUG_ENABLED_KEY = "radcad_dimension_debug_enabled"
DEBUG_LOG_KEY = "radcad_dimension_debug_log"
PREVIEW_GENERATION_KEY = "radcad_dimension_preview_generation"
ACTIVE_PREVIEW_KEY = "radcad_dimension_active_preview"
ACTIVE_OPERATOR_KEY = "radcad_dimension_active_operator"
PERSISTENT_RENDERER_KEY = "radcad_dimension_persistent_renderer"
DRAW_TRACE_KEY = "radcad_dimension_draw_trace"
LAST_SIGNATURE_PREFIX = "radcad_dimension_debug_last_"
MAX_LOG_ENTRIES = 500
DEBUG_LOG_PATH = "/tmp/radcad_dimension_debug.log"


def _namespace():
    return bpy.app.driver_namespace


def enabled():
    """Return whether dimension diagnostics are enabled.

    The default is intentionally on while the dimension overlay bug is being
    diagnosed.  Blender's Python console can disable it with:
    ``bpy.app.driver_namespace["radcad_dimension_debug_enabled"] = False``.
    """
    return bool(_namespace().get(DEBUG_ENABLED_KEY, True))


def _format_value(value):
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value.replace(" ", "_")
    if isinstance(value, dict):
        items = ",".join(
            f"{key}:{_format_value(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
        return "{" + items + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_format_value(item) for item in value) + "]"
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        return "(" + ",".join(
            _format_value(float(getattr(value, axis)))
            for axis in ("x", "y", "z")
        ) + ")"
    return str(value).replace(" ", "_")


def log(event, **fields):
    """Print, retain, and persist one diagnostic line for later inspection."""
    if not enabled():
        return
    stamp = time.strftime("%H:%M:%S")
    line = f"[radCAD Dimension DEBUG {stamp}] {event}"
    if fields:
        line += " " + " ".join(
            f"{key}={_format_value(value)}"
            for key, value in sorted(fields.items())
        )
    print(line, flush=True)
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except (OSError, RuntimeError):
        # Diagnostics must never interfere with viewport drawing or commits.
        pass
    namespace = _namespace()
    entries = namespace.get(DEBUG_LOG_KEY)
    if not isinstance(entries, list):
        entries = []
        namespace[DEBUG_LOG_KEY] = entries
    entries.append(line)
    if len(entries) > MAX_LOG_ENTRIES:
        del entries[:-MAX_LOG_ENTRIES]


def log_change(key, event, **fields):
    """Log an event only when its compact state changes."""
    if not enabled():
        return
    signature = repr(
        tuple(sorted((name, _format_value(value)) for name, value in fields.items()))
    )
    namespace = _namespace()
    state_key = LAST_SIGNATURE_PREFIX + str(key)
    if namespace.get(state_key) == signature:
        return
    namespace[state_key] = signature
    log(event, **fields)


def trace_draw_call(source, context=None, **fields):
    """Trace one dimension draw without logging every normal redraw.

    A normal viewport redraw happens many times per second.  Two callbacks
    painting the same region, however, run back-to-back.  Keep that short
    burst visible in the log while using ``log_change`` for stable geometry.
    """
    if not enabled():
        return

    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    try:
        area_id = int(area.as_pointer()) if area is not None else 0
    except (AttributeError, TypeError, ValueError, ReferenceError):
        area_id = 0
    try:
        region_id = int(region.as_pointer()) if region is not None else 0
    except (AttributeError, TypeError, ValueError, ReferenceError):
        region_id = 0

    key = f"{source}:{area_id}:{region_id}"
    now = time.monotonic()
    namespace = _namespace()
    traces = namespace.get(DRAW_TRACE_KEY)
    if not isinstance(traces, dict):
        traces = {}
        namespace[DRAW_TRACE_KEY] = traces
    previous = traces.get(key)
    if isinstance(previous, dict) and now - previous.get("time", 0.0) <= 0.003:
        burst = int(previous.get("burst", 1)) + 1
    else:
        burst = 1
    traces[key] = {"time": now, "burst": burst}

    payload = dict(fields)
    payload.update(source=source, area=area_id, region=region_id)
    if burst >= 2:
        log("dimension_draw_duplicate", calls=burst, **payload)
    else:
        log_change(f"draw_state_{key}", "dimension_draw", **payload)


def _disabled_dimension_draw(*_args, **_kwargs):
    """Replacement used to silence callbacks from an unloaded module copy."""
    return None


def retire_stale_dimension_draw_callbacks(current_callback):
    """Quarantine old drawing-module functions that Blender still owns.

    Blender does not expose a list of ``SpaceView3D`` draw handlers.  During
    a hot reload, an old handler can therefore outlive the Python module that
    registered it.  The current handler registry cannot remove such a handle.
    Function objects retained by Blender are still discoverable by ``gc``;
    replace only the old module's drawing helpers so that callback becomes a
    harmless no-op.  The current module dictionary is never modified.
    """
    if not enabled():
        return
    module_name = getattr(current_callback, "__module__", "")
    current_globals = getattr(current_callback, "__globals__", None)
    if not module_name or current_globals is None:
        return

    function_names = {
        "draw_persistent_dimensions_2d",
        "draw_preview_2d",
        "draw_preview_3d",
    }
    helper_names = (
        "draw_screen_dimension",
        "draw_screen_angle_dimension",
        "_draw_segments",
        "_draw_segments_2d",
        "_draw_points",
        "_draw_box",
        "_draw_angle_compass",
        "_draw_angle_preview_3d",
    )
    retired = {}
    try:
        candidates = gc.get_objects()
    except (RuntimeError, MemoryError):
        return

    for candidate in candidates:
        # Do not use isinstance() on arbitrary Blender RNA objects here.  A
        # removed operator can execute bpy's RNA attribute machinery while
        # Python is checking its type, raising ReferenceError and aborting
        # add-on registration.  An exact type check is sufficient because we
        # only need real Python function objects.
        try:
            if type(candidate) is not types.FunctionType:
                continue
            candidate_module = candidate.__module__
            candidate_name = candidate.__name__
            candidate_globals = candidate.__globals__
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if candidate_module != module_name or candidate_name not in function_names:
            continue
        if candidate_globals is None or candidate_globals is current_globals:
            continue
        if candidate_globals.get("_radcad_dimension_stale_quarantined", False):
            continue

        for helper_name in helper_names:
            if helper_name in candidate_globals:
                candidate_globals[helper_name] = _disabled_dimension_draw
        candidate_globals["_radcad_dimension_stale_quarantined"] = True
        module_key = str(id(candidate_globals))
        retired.setdefault(module_key, set()).add(candidate_name)

    if retired:
        log(
            "legacy_draw_callbacks_retired",
            callbacks=[
                sorted(names)
                for _module_key, names in sorted(retired.items())
            ],
            module=module_name,
        )


def _next_generation():
    namespace = _namespace()
    generation = int(namespace.get(PREVIEW_GENERATION_KEY, 0)) + 1
    namespace[PREVIEW_GENERATION_KEY] = generation
    return generation


def start_preview(operator, tool_name):
    """Start a preview session and retire any older modal preview."""
    namespace = _namespace()
    previous = namespace.get(ACTIVE_PREVIEW_KEY)
    previous_operator = namespace.get(ACTIVE_OPERATOR_KEY)
    if previous_operator is not None and previous_operator is not operator:
        try:
            previous_operator.running = False
        except (AttributeError, ReferenceError, RuntimeError):
            pass

    generation = _next_generation()
    operator._radcad_dimension_preview_generation = generation
    info = {
        "generation": generation,
        "tool": tool_name,
        "instance": getattr(operator, "tool_instance_id", ""),
    }
    namespace[ACTIVE_PREVIEW_KEY] = info
    namespace[ACTIVE_OPERATOR_KEY] = operator
    log(
        "preview_start",
        tool=tool_name,
        generation=generation,
        previous=previous,
    )
    return generation


def preview_is_current(operator):
    generation = getattr(operator, "_radcad_dimension_preview_generation", None)
    active = _namespace().get(ACTIVE_PREVIEW_KEY)
    return (
        generation is not None
        and isinstance(active, dict)
        and active.get("generation") == generation
    )


def stop_preview(operator, reason):
    """Retire a preview session without touching persistent dimensions."""
    namespace = _namespace()
    active = namespace.get(ACTIVE_PREVIEW_KEY)
    generation = getattr(operator, "_radcad_dimension_preview_generation", None)
    was_current = (
        isinstance(active, dict) and active.get("generation") == generation
    )
    if was_current:
        namespace.pop(ACTIVE_PREVIEW_KEY, None)
        namespace.pop(ACTIVE_OPERATOR_KEY, None)
    log(
        "preview_stop",
        tool=active.get("tool") if isinstance(active, dict) else "unknown",
        generation=generation,
        reason=reason,
        current=was_current,
    )


def invalidate_active_preview(reason):
    """Invalidate a modal preview, including one surviving a module reload."""
    namespace = _namespace()
    active = namespace.get(ACTIVE_PREVIEW_KEY)
    operator = namespace.get(ACTIVE_OPERATOR_KEY)
    stopped = 0
    stopped_ids = set()
    if operator is not None:
        try:
            operator.running = False
            stopped += 1
            stopped_ids.add(id(operator))
            operator_context = getattr(operator, "context", None)
            scene = getattr(operator_context, "scene", None)
            if (
                scene is not None
                and getattr(scene, "active_cad_tool_id", "")
                == getattr(operator, "tool_instance_id", None)
            ):
                scene.active_cad_tool_id = ""
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    metadata = namespace.get("radcad_draw_handler_metadata", {})
    if isinstance(metadata, dict):
        for source_id, details in metadata.items():
            if not str(source_id).startswith("RADCADDIM_"):
                continue
            args = details.get("args", ()) if isinstance(details, dict) else ()
            for candidate in args:
                if (
                    candidate is operator
                    or id(candidate) in stopped_ids
                    or not hasattr(candidate, "running")
                ):
                    continue
                try:
                    candidate.running = False
                    stopped += 1
                    stopped_ids.add(id(candidate))
                except (AttributeError, ReferenceError, RuntimeError):
                    pass
    generation = _next_generation()
    namespace.pop(ACTIVE_PREVIEW_KEY, None)
    namespace.pop(ACTIVE_OPERATOR_KEY, None)
    log(
        "preview_invalidate",
        previous=active,
        generation=generation,
        reason=reason,
        stopped=stopped,
    )


def activate_persistent_renderer(callback):
    """Mark the newest persistent draw callback as the only valid renderer."""
    _namespace()[PERSISTENT_RENDERER_KEY] = {"callback": callback}
    log(
        "persistent_renderer_activate",
        callback=f"{callback.__module__}.{callback.__name__}",
    )


def invalidate_persistent_renderer(reason):
    """Make callbacks from an older module generation no-ops."""
    namespace = _namespace()
    previous = namespace.get(PERSISTENT_RENDERER_KEY)
    namespace[PERSISTENT_RENDERER_KEY] = {"callback": None}
    log(
        "persistent_renderer_invalidate",
        previous=(
            f"{previous['callback'].__module__}.{previous['callback'].__name__}"
            if isinstance(previous, dict) and callable(previous.get("callback"))
            else previous
        ),
        reason=reason,
    )


def persistent_renderer_is_current(callback):
    """Return whether *callback* belongs to the active renderer generation."""
    namespace = _namespace()
    if PERSISTENT_RENDERER_KEY not in namespace:
        # Preserve direct/manual calls made before the add-on registers its
        # persistent handler. Normal registered callbacks always have a token.
        return True
    info = namespace.get(PERSISTENT_RENDERER_KEY)
    return isinstance(info, dict) and info.get("callback") is callback


def handler_snapshot():
    """Return the modal and persistent draw-handler registries."""
    namespace = _namespace()
    modal_registry = namespace.get("radcad_draw_handler_registry", {})
    if not isinstance(modal_registry, dict):
        modal_registry = {}
    modal = {
        str(source_id): {
            "handle": repr(value[0]) if isinstance(value, tuple) and value else repr(value),
            "region": value[1] if isinstance(value, tuple) and len(value) > 1 else "?",
        }
        for source_id, value in modal_registry.items()
    }
    metadata = namespace.get("radcad_draw_handler_metadata", {})
    callbacks = {
        str(source_id): {
            "callback": (
                f"{details['callback'].__module__}.{details['callback'].__name__}"
                if isinstance(details, dict) and callable(details.get("callback"))
                else repr(details)
            ),
            "args": [type(arg).__name__ for arg in details.get("args", ())]
            if isinstance(details, dict)
            else [],
        }
        for source_id, details in metadata.items()
        if str(source_id).startswith("RADCADDIM_")
    }
    persistent = namespace.get("radcad_dimension_overlay_handles", [])
    renderer = namespace.get(PERSISTENT_RENDERER_KEY)
    return {
        "modal": modal,
        "callbacks": callbacks,
        "persistent": [repr(value) for value in persistent]
        if isinstance(persistent, list)
        else repr(persistent),
        "renderer": (
            f"{renderer['callback'].__module__}.{renderer['callback'].__name__}"
            if isinstance(renderer, dict) and callable(renderer.get("callback"))
            else repr(renderer)
        ),
    }


def dimension_snapshot(scene):
    """Return compact saved-state details for every persistent dimension."""
    snapshot = []
    for obj in getattr(scene, "objects", ()):
        data = getattr(obj, "radcad_dimension", None)
        if data is None or not getattr(data, "is_dimension", False):
            continue
        anchors = []
        anchor_names = (
            ("anchor_1", "anchor_2", "anchor_3")
            if getattr(data, "dimension_type", "LINEAR") == "ANGLE"
            else (
                ("anchor_1", "anchor_2", "placement_anchor")
                if getattr(data, "placement_initialized", False)
                else ("anchor_1", "anchor_2")
            )
        )
        for name in anchor_names:
            anchor = getattr(data, name, None)
            if anchor is None:
                continue
            fallback = getattr(anchor, "fallback", None)
            anchors.append(_format_value(fallback))
        direction = getattr(data, "linear_direction", None)
        snapshot.append(
            {
                "name": getattr(obj, "name", "<unnamed>"),
                "type": getattr(data, "dimension_type", "LINEAR"),
                "anchors": anchors,
                "plane": _format_value(getattr(data, "plane_normal", None)),
                "direction": _format_value(direction),
                "offset": float(getattr(data, "offset_distance", 0.0)),
                "placement_mode": getattr(data, "placement_mode", "FACE"),
                "label": getattr(data, "text_override", "") or "<live>",
            }
        )
    return snapshot


def log_dimension_snapshot(scene, event, **fields):
    dimensions = dimension_snapshot(scene)
    fields.update(
        count=len(dimensions),
        names=[item["name"] for item in dimensions],
        dimensions=dimensions,
    )
    log(event, **fields)
