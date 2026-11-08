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
# the `.g` attribute block's `material0_diffuse` of one part, as a string on
# the mesh object.  The name lives in THREE places (the part's block in the
# `.g`, the same block in the `_lod.g`, and the `gobj` in both `.scene`
# files) and the engine reads the scene's copy, so it can only be set on the
# Blender side when the exporter is the one writing all three.  Set on import
# from the part's own attribute, honoured on export by writing
# `extras.d3unit_diffuse` into the glTF (`d3tool/gltf.py`).
DIFFUSE_PROP = "d3unit_diffuse"
# which unit a part belongs to (the `.g` base): several units may live in one
# scene, and an export must pick only its own meshes
OWNER_PROP = "d3unit_owner"
VERSION = 1


#: a material named after the atlas it wants, e.g. ``weapon_x_diffuse``
_ATLAS_HINTS = ("_diffuse", "_lightmap", "_specular")


def clean_atlas_name(name: str, folders=()) -> str:
    """``weapon_x_diffuse.tga`` for any spelling of a unit texture, else "".

    Blender keeps an imported atlas as ``x.dds.png`` and an artist may type
    ``x.t``; the `.g` understands only ``<stem>.tga``.  Names that are not unit
    atlases at all — ``material0``, ``Material.001``, ``Image`` — come back
    empty, so a material merely *called* something is never mistaken for a
    texture.

    A name that follows none of the retail conventions is still a texture when
    the file is *there*: a part the artist modelled himself wears whatever
    atlas he painted for it (``helmet_of_the_order.tga``), and refusing the
    name because it does not look like ``weapon_*_diffuse`` made the export
    quietly fall back to the donor atlas.  *folders* is where that evidence is
    looked for — the unit folder first, then the .blend's.
    """
    from . import bridge
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        return ""
    raw = os.path.basename(raw)
    try:
        stem = bridge.module("texture").clean_stem(raw)
    except Exception:  # noqa: BLE001 - a fallback is better than a crash
        stem = os.path.splitext(raw)[0]
    low = stem.lower()
    if not stem or low.startswith(("material", "image", "texture", "untitled")):
        return ""
    if any(hint in low for hint in _ATLAS_HINTS) or low.startswith(
            ("weapon_", "character_", "armor_", "armour_", "creature_",
             "tower_", "decoration_")):
        return stem + ".tga"
    for folder in folders or ():
        if not folder or not os.path.isdir(folder):
            continue
        for ext in (".t", ".dds", ".tga", ".png", ".bmp"):
            if os.path.isfile(os.path.join(folder, stem + ext)):
                return stem + ".tga"
    return ""


def texture_folders(context=None, mesh=None) -> list:
    """Folders searched for a part's texture: the unit's, then the .blend's.

    A unit keeps its atlas next to its ``.g``; a texture picked by hand may sit
    anywhere, so the unit folder comes first and the folder the scene was saved
    in second (a scratch folder is where a re-exported atlas usually lands).
    """
    context = context or _context()
    folders = []
    root = find_unit_root(context)
    info = get_info(root) if root else {}
    for value in (info.get("dir"), info.get("g"), info.get("gltf")):
        value = str(value or "")
        if not value:
            continue
        folder = value if os.path.isdir(value) else os.path.dirname(value)
        if folder and folder not in folders:
            folders.append(folder)
    try:
        import bpy
        blend = os.path.dirname(bpy.data.filepath or "")
    except Exception:  # noqa: BLE001
        blend = ""
    if blend and blend not in folders:
        folders.append(blend)
    return folders


def _feeds_base_colour(tree, node) -> bool:
    """True when an image node feeds the material's colour, not its normal map."""
    try:
        links = tree.links
    except AttributeError:
        return False
    for link in links:
        try:
            if link.from_node is not node:
                continue
            socket = str(getattr(link.to_socket, "name", "")).lower()
        except AttributeError:
            continue
        if socket in ("base color", "color", "diffuse"):
            return True
    return False


def material_texture_name(ob, folders=()) -> str:
    """The atlas the object's own material points at, ``""`` when it names none.

    Re-texturing a part in Blender means giving it a material with the image —
    the natural thing to do — but until 1.1.33 that only reached the `.g` when
    the image arrived in the glTF as a file URI.  A *packed* image has none, so
    the part exported with the donor's atlas while the report said it had "no
    texture name of its own": the material was right there, naming the atlas.
    """
    guessed = ""
    for slot in (getattr(ob, "material_slots", ()) or ()):
        mat = getattr(slot, "material", None)
        if mat is None:
            continue
        tree = None
        try:
            if getattr(mat, "use_nodes", False):
                tree = mat.node_tree
        except AttributeError:
            tree = None
        for node in (getattr(tree, "nodes", ()) or ()):
            if getattr(node, "type", "") != "TEX_IMAGE":
                continue
            image = getattr(node, "image", None)
            if image is None:
                continue
            name = clean_atlas_name(getattr(image, "filepath", "")
                                    or getattr(image, "name", ""), folders)
            if not name:
                continue
            if _feeds_base_colour(tree, node):
                return name
            guessed = guessed or name
        if not guessed:
            guessed = clean_atlas_name(getattr(mat, "name", ""), folders)
    return guessed


def normalise_material_names(meshes) -> list:
    """Name a unit's materials ``material0``, ``material1``, … in part order.

    Blender keeps datablock names unique, so a unit whose parts each carry
    ``material0`` ends up with ``material0``, ``material0.001``,
    ``material0.002`` — names the engine never uses, and the artist can no
    longer tell the weapon's material from the body's.  Numbering the whole
    unit once (part order, then slot order) gives the mapping the `.g` uses:
    the body is ``material0``, the sword ``material1``.

    Returns ``[(object name, [material name per slot]), …]`` for the report.
    """
    seen: Dict[int, str] = {}
    planned = []
    rows = []
    for ob in (meshes or ()):
        row = []
        for slot in (getattr(ob, "material_slots", ()) or ()):
            mat = getattr(slot, "material", None)
            if mat is None:
                row.append("")
                continue
            try:
                key = mat.as_pointer()
            except AttributeError:
                key = id(mat)
            if key not in seen:
                seen[key] = "material%d" % len(seen)
                planned.append((mat, seen[key]))
            row.append(seen[key])
        rows.append((getattr(ob, "name", ""), row))
    if not planned:
        return rows
    # two passes: renaming onto a name another datablock still holds would get
    # it a ".001" suffix — which is the thing being removed here
    for i, pair in enumerate(planned):
        try:
            pair[0].name = "_d3unit_tmp_%d" % i
        except Exception:  # noqa: BLE001
            pass
    for mat, name in planned:
        try:
            mat.name = name
        except Exception:  # noqa: BLE001
            pass
    return rows


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
    # The active object decides, and it decides *before* any armature scan:
    # a scene may hold a rigged unit and a rigid one, and asking for "the
    # armature that has unit metadata" would hand the export the rigged
    # unit no matter which one the user is working on.
    obj = getattr(context, "object", None)
    if obj is not None:
        arm = as_armature(obj)
        if arm is not None:
            return arm
        if PROP in obj:                 # rigid unit: metadata on the mesh
            return obj
    try:
        selected = list(context.selected_objects)
    except AttributeError:
        selected = []
    roots = [o for o in selected if PROP in o and as_armature(o) is None]
    if len(roots) == 1:
        return roots[0]
    arm = find_armature(context)
    if arm is not None:
        return arm
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
