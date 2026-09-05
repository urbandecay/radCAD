"""Small helpers that make add-on teardown safe after a failed reload."""

import bpy


def safe_unregister_class(cls):
    """Unregister a class when live, ignoring Blender's stale-class errors."""
    try:
        bpy.utils.unregister_class(cls)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False
    return True


def safe_delete_property(owner, name):
    """Delete an optional Blender property without aborting teardown."""
    try:
        if hasattr(owner, name):
            delattr(owner, name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        pass
