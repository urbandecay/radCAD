"""Keymap helpers for construction-line operators."""

import bpy


_TRANSLATE_KEYMAP_ITEMS = []


_OPERATOR_IDS = {
    "view3d.radcad_construction_translate",
    "view3d.radcad_construction_duplicate_translate",
    "view3d.radcad_construction_pick",
    "view3d.radcad_construction_delete",
}


def register_translate_keymap():
    unregister_translate_keymap()
    window_manager = getattr(bpy.context, "window_manager", None)
    key_config = getattr(getattr(window_manager, "keyconfigs", None), "addon", None)
    if key_config is None:
        return
    keymap = key_config.keymaps.new(name="Mesh", space_type="EMPTY")
    # A source reload can replace this module before its old Python list is
    # available to unregister. Remove any stale binding by operator id so G
    # always reaches the current implementation exactly once.
    for existing in list(keymap.keymap_items):
        if existing.idname in _OPERATOR_IDS:
            keymap.keymap_items.remove(existing)
    keymap_item = keymap.keymap_items.new(
        "view3d.radcad_construction_translate",
        "G",
        "PRESS",
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((keymap, keymap_item))
    duplicate_keymap_item = keymap.keymap_items.new(
        "view3d.radcad_construction_duplicate_translate",
        "D",
        "PRESS",
        shift=True,
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((keymap, duplicate_keymap_item))
    view_keymap = key_config.keymaps.new(name="3D View", space_type="VIEW_3D")
    for existing in list(view_keymap.keymap_items):
        if existing.idname in {
            "view3d.radcad_construction_pick",
            "view3d.radcad_construction_delete",
        }:
            view_keymap.keymap_items.remove(existing)
    pick_item = view_keymap.keymap_items.new(
        "view3d.radcad_construction_pick",
        "LEFTMOUSE",
        "PRESS",
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((view_keymap, pick_item))
    delete_item = view_keymap.keymap_items.new(
        "view3d.radcad_construction_delete",
        "DEL",
        "PRESS",
        head=True,
    )
    _TRANSLATE_KEYMAP_ITEMS.append((view_keymap, delete_item))


def unregister_translate_keymap():
    for keymap, keymap_item in _TRANSLATE_KEYMAP_ITEMS:
        try:
            keymap.keymap_items.remove(keymap_item)
        except (ReferenceError, RuntimeError):
            pass
    _TRANSLATE_KEYMAP_ITEMS.clear()
