# -*- coding: utf-8 -*-
"""Per-unit metadata carried by the imported Blender objects.

A Disciples 3 unit is a *bundle* of files sharing one stem in one folder::

    character_empire_angel.g      geometry + bind skeleton   (binary)
    character_empire_angel_idle.a animation streams          (binary)
    character_empire_angel.ac     clip/event config          (text)
    character_empire_angel.scene  scene description          (text)
    character_empire_angel.t      texture atlas              (binary)
    Aliases/*.alias               sound/FX events            (text)

Only the `.g` can be re-emitted from Blender; the rest is *donated* by the
originals on export (a glTF cannot carry a `.ac` event table, nor the
`.scene` particle emitters, nor the `.a` morph scaffolding).  The import
therefore records where that donor bundle lives, on the armature object, and
every later export reads it back.  Nothing is guessed from the file name
alone unless the user explicitly exports into a folder that holds a bundle.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

PROP = "d3unit"          # dict on the armature object
PART_PROP = "d3unit_part"  # True on every mesh that belongs to the unit
# which unit a part belongs to (the `.g` base): several units may live in one
# scene, and an export must pick only its own meshes
OWNER_PROP = "d3unit_owner"
VERSION = 1


def find_armature(context=None, prefer=None):
    """The armature object that owns the unit in this scene, if any.

    Preference order: the object the caller named, the active object (or its
    armature parent), the single selected armature, then the armature that
    carries the :data:`PROP` metadata (there is normally exactly one).
    """
    context = context or _context()
    if prefer is not None:
        arm = as_armature(prefer)
        if arm is not None:
            return arm
    obj = getattr(context, "object", None)
    arm = as_armature(obj)
    if arm is not None:
        return arm
    try:
        selected = [o for o in context.selected_objects if o.type == "ARMATURE"]
    except AttributeError:
        selected = []
    if len(selected) == 1:
        return selected[0]
    try:
        tagged = [o for o in context.scene.objects
                  if o.type == "ARMATURE" and PROP in o]
    except AttributeError:
        tagged = []
    if len(tagged) == 1:
        return tagged[0]
    if tagged:
        # several units in one scene: prefer the one whose meshes are
        # selected, else the first (the caller warns about the ambiguity)
        sel = set(getattr(context, "selected_objects", ()) or ())
        for arm in tagged:
            if arm in sel or any(c in sel for c in _unit_meshes(arm)):
                return arm
        return tagged[0]
    return None


def as_armature(obj):
    """Return ``obj`` if it is an armature, or its armature parent."""
    if obj is None:
        return None
    if getattr(obj, "type", "") == "ARMATURE":
        return obj
    parent = getattr(obj, "parent", None)
    if parent is not None and getattr(parent, "type", "") == "ARMATURE":
        return parent
    for mod in getattr(obj, "modifiers", ()) or ():
        if getattr(mod, "type", "") == "ARMATURE" and mod.object is not None:
            return mod.object
    return None


def _context():
    import bpy
    return bpy.context


def get_info(arm) -> Dict:
    """The stored unit metadata of an armature (``{}`` when untagged)."""
    if arm is None:
        return {}
    raw = arm.get(PROP)
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    try:
        return {k: raw[k] for k in raw.keys()}
    except Exception:  # noqa: BLE001 - IDProperty groups behave oddly
        return dict(raw) if hasattr(raw, "__iter__") else {}


def set_info(arm, info: Dict) -> None:
    if arm is None:
        return
    data = dict(info)
    data["version"] = VERSION
    try:
        arm[PROP] = data
    except (TypeError, ValueError):
        # older Blender cannot store nested dicts: fall back to JSON
        arm[PROP] = json.dumps(data)


def find_unit_root(context=None):
    """The object that carries the unit: its armature, or a mesh when rigid.

    A rigged unit is owned by its armature.  A *rigid* unit (a statue, a
    projectile, an animated-by-morph mesh — the corpus has several) has no
    armature at all: its metadata lives on the mesh object instead, and the
    export must not refuse to run for the lack of a skeleton.
    """
    context = context or _context()
    arm = find_armature(context)
    if arm is not None:
        return arm
    obj = getattr(context, "object", None)
    if obj is not None and PROP in obj:
        return obj
    tagged = [o for o in getattr(context.scene, "objects", ()) or ()
              if PROP in o]
    if len(tagged) == 1:
        return tagged[0]
    parts = [o for o in getattr(context.scene, "objects", ()) or ()
             if o.get(PART_PROP)]
    if len(parts) == 1:
        return parts[0]
    meshes = [o for o in getattr(context.scene, "objects", ()) or ()
              if o.type == "MESH"]
    if len(meshes) == 1:
        return meshes[0]
    return None


def unit_meshes(arm):
    """Every mesh object bound to ``arm`` (children, bone children, modifier)."""
    if arm is None:
        return []
    return _unit_meshes(arm)


def _unit_meshes(arm):
    import bpy
    out = []
    for obj in getattr(bpy.context.scene, "objects", ()) or ():
        if obj.type != "MESH":
            continue
        if obj.parent is arm:
            out.append(obj)
            continue
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object is arm:
                out.append(obj)
                break
    return out


def tag_imported(arm, meshes, info: Dict) -> None:
    if arm is not None:
        set_info(arm, info)
    owner = (info or {}).get("base") or (arm.name if arm is not None else "")
    for ob in meshes:
        try:
            ob[PART_PROP] = True
            ob[OWNER_PROP] = owner
        except Exception:  # noqa: BLE001
            pass
    if arm is None and meshes:
        set_info(meshes[0], info)


def describe(info: Dict) -> str:
    """One-line human summary of a unit for the sidebar panel."""
    if not info:
        return "not a Disciples III unit"
    base = info.get("base") or "?"
    n = len(info.get("anims") or [])
    return f"{base}  ({n} animation stream{'s' if n != 1 else ''})"


def resolve_output(path: str, info: Dict) -> Dict:
    """Where an export should write, and which folder donates the originals.

    ``path`` is what the user typed into the file browser (a ``.g`` path).
    The output folder is its folder; the donor bundle is the unit the scene
    was imported from when there is one — a Blender scene is normally saved
    somewhere else entirely (a scratch folder, the desktop) and the engine
    files must still keep their `.ac` events, `.scene` emitters and `.a`
    scaffolding.
    """
    path = os.path.abspath(path)
    out_dir = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    donor = info.get("dir") or ""
    donor_base = info.get("base") or ""
    if not donor and os.path.isdir(out_dir):
        donor = out_dir
    return {
        "out_dir": out_dir,
        "base": base,
        "donor_dir": donor,
        "donor_base": donor_base,
    }


def find_unit_bundle(path: str) -> Optional[Dict]:
    """Inspect a ``.g`` path and describe the bundle sitting next to it.

    Used when exporting a scene that was NOT imported through the add-on
    (the user opened a hand-made .blend, or imported a glTF): everything the
    export needs from the originals is discovered here.
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return None
    folder = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    bundle = {
        "g": path,
        "dir": folder,
        "base": base,
        "ac": "",
        "scene": "",
        "anims": [],
        "textures": [],
    }
    for ext, key in ((".ac", "ac"), (".scene", "scene")):
        cand = os.path.join(folder, base + ext)
        if os.path.isfile(cand):
            bundle[key] = cand
    try:
        for fn in sorted(os.listdir(folder)):
            low = fn.lower()
            if low.endswith(".a") and low.startswith(base.lower()):
                bundle["anims"].append(fn)
            elif low.endswith((".t", ".dds")) and (
                    low.startswith(base.lower())
                    or low.startswith(os.path.splitext(
                        os.path.basename(bundle.get("ac") or ""))[0].lower())):
                bundle["textures"].append(fn)
    except OSError:
        pass
    return bundle
