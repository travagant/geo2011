# -*- coding: utf-8 -*-
"""Import a Disciples 3 unit into the current Blender scene.

The heavy lifting is done by ``d3tool``'s forward exporter: the original
``.g``/``.a`` bundle is converted into a *dis3tool-faithful* glTF (85/85
byte-exact against the shipped reference exports), and Blender's own glTF
importer builds the objects.  That route inherits working skinning,
materials, textures, and the full concatenated animation without a second
implementation of the format — the add-on only has to put the scene back
into the shape the exporter expects (30 fps, linear keys, unit metadata).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import bridge, unitprops
from .preferences import get_prefs

UNIT_FILTER = [
    ("*.g", "Disciples III geometry (.g)", "GM engine character geometry"),
    ("*.gltf", "dis3tool glTF (.gltf)", "A glTF previously exported from .g"),
    ("*.glb", "dis3tool glTF (.glb)", "A binary glTF"),
    ("*.scene", "Disciples III scene (.scene)", "Scene description"),
    ("*.ac", "Disciples III animation config (.ac)", "Animation config"),
    ("*", "All files", ""),
]

FPS = 30


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _unit_from_pick(path: str) -> str:
    """Map a picked ``.scene``/``.ac`` onto the ``.g`` it belongs to."""
    folder = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()
    if ext in (".g", ".gltf", ".glb"):
        return path
    cand = os.path.join(folder, base + ".g")
    if os.path.isfile(cand):
        return cand
    raise FileNotFoundError(
        f"{os.path.basename(path)} has no matching {base}.g in its folder — "
        f"pick the unit's .g file instead")


def _new_objects(before):
    return [o for o in bpy.data.objects if o not in before]


def _set_fps(context):
    scene = context.scene
    scene.render.fps = FPS
    scene.render.fps_base = 1.0


def _linear_keys(arm):
    """GM clips are sampled per frame; linear keys are what the exporter
    re-samples, so an imported Bezier curve would round-trip differently."""
    anim = getattr(arm, "animation_data", None)
    if not anim or not anim.action:
        return None
    action = anim.action
    try:
        curves = list(action.fcurves)
    except AttributeError:      # Blender 4.4+ layered actions
        curves = []
        for layer in getattr(action, "layers", ()):
            for strip in getattr(layer, "strips", ()):
                for bag in getattr(strip, "channelbags", ()):
                    curves.extend(getattr(bag, "fcurves", ()))
    for fc in curves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    return action


def _fit_frame_range(context, arm):
    scene = context.scene
    action = getattr(getattr(arm, "animation_data", None), "action", None)
    if action is None:
        return
    start, end = action.frame_range
    scene.frame_start = int(round(start))
    scene.frame_end = max(int(round(end)), int(round(start)) + 1)
    scene.frame_set(scene.frame_start)


def _pack_images(context, folder: str, pack: bool):
    """Embed the textures so removing the temp folder cannot blank the unit."""
    if not pack:
        return 0
    packed = 0
    folder = os.path.abspath(folder)
    for img in bpy.data.images:
        path = bpy.path.abspath(img.filepath) if img.filepath else ""
        if not path or img.source != "FILE" or img.packed_file:
            continue
        if os.path.abspath(path).startswith(folder):
            try:
                img.pack()
                packed += 1
            except Exception:  # noqa: BLE001 - an unreadable image must not
                pass           # abort the import
    return packed


def _drop_broken_images(context):
    """Forget image datablocks whose file does not exist.

    A `.g` may name a texture the unit folder does not ship (Blacknaga's
    cubemap).  Blender then holds an image pointing at nothing and complains
    on every pack/save; dropping it keeps the .blend clean and the material
    still has its other maps.
    """
    for img in list(bpy.data.images):
        path = bpy.path.abspath(img.filepath) if img.filepath else ""
        if not path or img.source != "FILE" or img.packed_file:
            continue
        if not os.path.isfile(path):
            bpy.data.images.remove(img)


def _tag(arm, meshes, info):
    unitprops.tag_imported(arm, meshes, info)
    for ob in [arm] + list(meshes):
        try:
            ob["d3unit_source"] = info.get("g", "")
        except Exception:  # noqa: BLE001
            pass


def _select_only(context, objects):
    for ob in context.scene.objects:
        ob.select_set(False)
    for ob in objects:
        ob.select_set(True)
    if objects:
        context.view_layer.objects.active = objects[0]


def _log_to_text(name: str, text: str) -> None:
    """Keep the tool output in a text block so the user can read it later."""
    try:
        block = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        block.clear()
        block.write(text)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
#  core (also used by the tests, which run without operators)
# --------------------------------------------------------------------------- #
def import_unit(context, path: str, use_anim: bool = True,
                use_textures: bool = True, pack: bool = True,
                keep_temp: bool = False, quiet: bool = False) -> dict:
    """Import one unit; returns ``{armature, meshes, info, log}``."""
    import io
    import contextlib

    cli = bridge.module("cli")
    bridge.require(_get_pref_path(context))

    path = _unit_from_pick(os.path.abspath(path))
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.dirname(path)
    log = io.StringIO()

    tmp = tempfile.mkdtemp(prefix="d3unit_")
    before = set(bpy.data.objects)
    try:
        if path.lower().endswith((".gltf", ".glb")):
            gltf_path = path
        else:
            anim_path = None
            if use_anim:
                anim_path = cli._find_animation_for_geometry(path)
            gltf_path = os.path.join(tmp, base + ".gltf")
            with contextlib.redirect_stdout(log), \
                    contextlib.redirect_stderr(log):
                cli._export_gl(path, anim_path, gltf_path, None,
                               quiet=quiet)
            if not use_textures:
                _strip_images(gltf_path)

        _set_fps(context)
        kwargs = {"filepath": gltf_path}
        props = _gltf_import_props()
        if "bone_heuristic" in props:
            kwargs["bone_heuristic"] = "TEMPERANCE"
        if "import_pack_images" in props:
            kwargs["import_pack_images"] = False
        with contextlib.redirect_stdout(log), \
                contextlib.redirect_stderr(log):
            result = bpy.ops.import_scene.gltf(**kwargs)
        if result != {"FINISHED"}:
            raise RuntimeError(f"the glTF importer returned {result!r}")

        created = _new_objects(before)
        armatures = [o for o in created if o.type == "ARMATURE"]
        meshes = [o for o in created if o.type == "MESH"]
        if not meshes:
            raise RuntimeError("the unit has no geometry to import")

        packed = _pack_images(context, tmp, pack) if use_textures else 0
        _drop_broken_images(context)
        frames = 0
        for arm in armatures:
            action = _linear_keys(arm)
            _fit_frame_range(context, arm)
            frames = max(frames, int(action.frame_range[1]) + 1
                         if action else 0)
            arm.show_in_front = True
            arm.data.display_type = "OCTAHEDRAL"
        info = _bundle_info(path, base, folder, use_anim)
        info["gltf"] = "" if path.lower().endswith((".gltf", ".glb")) \
            else gltf_path
        info["imported"] = time.strftime("%Y-%m-%d %H:%M:%S")
        info["textures"] = packed
        info["frames"] = frames
        info["meshes"] = len(meshes)
        if armatures:
            for arm in armatures:
                own = [m for m in meshes
                       if m.parent is arm or any(
                           mod.type == "ARMATURE" and mod.object == arm
                           for mod in m.modifiers)]
                _tag(arm, own, info)
            # glTF meshes without a skin (a morph base such as the Hierophant
            # robe, a rigid CPU-side part) belong to the unit just as much:
            # tag them so the exporter hands the whole mesh set to the glTF
            # and the donor's compound slots stay one-to-one with the parts
            loose = [m for m in meshes if not m.get(unitprops.PART_PROP)]
            # Stamp the owner as well, not just "belongs to a unit": two
            # units in one scene must stay separable, or the first export
            # would swallow the second unit's unbound meshes.
            owner = (info or {}).get("base") or (
                armatures[0].name if armatures else "")
            for ob in loose:
                try:
                    ob[unitprops.PART_PROP] = True
                    ob[unitprops.OWNER_PROP] = owner
                except Exception:  # noqa: BLE001
                    pass
        else:
            # a purely rigid unit: the metadata lives on the geometry
            _tag(None, meshes, info)
        _select_only(context, armatures + meshes)
        root = armatures[0] if armatures else meshes[0]
        _log_to_text(f"{base} import", log.getvalue())
        if not quiet:
            print(f"[d3unit] imported {base}: "
                  f"{len(armatures)} armature(s), "
                  f"{len(meshes)} mesh(es), {packed} packed texture(s)")
        return {"armature": armatures[0] if armatures else None,
                "root": root, "armatures": armatures,
                "meshes": meshes, "info": info, "log": log.getvalue()}
    finally:
        if keep_temp:
            print(f"[d3unit] temp folder kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def _get_pref_path(context):
    prefs = get_prefs(context)
    return getattr(prefs, "d3tool_path", "") if prefs else ""


def _bundle_info(path, base, folder, use_anim):
    info = unitprops.find_unit_bundle(path) or {}
    info.update({"g": path, "base": base, "dir": folder})
    if not use_anim:
        info["anims"] = []
    try:
        acmod = bridge.module("ac")
        named = acmod.detect_anim_files(folder, base)
        if use_anim and named:
            info["anims"] = sorted({v for v in named.values() if v})
            info["clips"] = dict(named)
    except Exception:  # noqa: BLE001
        pass
    return info


def _strip_images(gltf_path):
    """Drop textures from a glTF the user asked to import without them."""
    import json
    try:
        with open(gltf_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return
    for mat in doc.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness") or {}
        pbr.pop("baseColorTexture", None)
        mat.pop("normalTexture", None)
    with open(gltf_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _gltf_import_props():
    try:
        return set(bpy.ops.import_scene.gltf.get_rna_type().properties.keys())
    except Exception:  # noqa: BLE001
        return set()


# --------------------------------------------------------------------------- #
#  operator
# --------------------------------------------------------------------------- #
class D3UNIT_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "d3unit.import_unit"
    bl_label = "Import Disciples III Unit"
    bl_description = ("Load a .g unit (with its .a animation, .ac clips and "
                      ".t texture) for editing in Blender")
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".g"
    filter_glob: StringProperty(default="*.g;*.gltf;*.glb;*.scene;*.ac",
                                options={"HIDDEN"})
    use_anim: BoolProperty(
        name="Import animation",
        default=True,
        description="Load every animation stream the unit's .ac names",
    )
    use_textures: BoolProperty(
        name="Import textures",
        default=True,
        description="Load the unit's .t atlas (converted to .dds) as a "
                    "material texture",
    )

    def execute(self, context):
        prefs = get_prefs(context)
        pack = getattr(prefs, "pack_textures", True)
        keep = getattr(prefs, "keep_temp", False)
        try:
            out = import_unit(context, self.filepath,
                              use_anim=self.use_anim,
                              use_textures=self.use_textures,
                              pack=pack, keep_temp=keep)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"import failed: {exc}")
            return {"CANCELLED"}
        info = out["info"]
        self.report({"INFO"},
                    f"Imported {info.get('base', '')}: "
                    f"{len(out['meshes'])} mesh(es), "
                    f"{len(info.get('anims') or [])} animation stream(s)")
        self.report({"INFO"}, f"{info.get('frames', 0)} animation frames")
        return {"FINISHED"}


class D3UNIT_OT_import_batch(bpy.types.Operator):
    bl_idname = "d3unit.import_folder"
    bl_label = "Import All Units In Folder"
    bl_description = ("Import every .g in a folder (each as its own unit)")
    bl_options = {"UNDO"}

    directory: StringProperty(subtype="DIR_PATH")
    use_anim: BoolProperty(name="Import animation", default=True)
    use_textures: BoolProperty(name="Import textures", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        folder = self.directory
        files = sorted(f for f in os.listdir(folder)
                       if f.lower().endswith(".g"))
        if not files:
            self.report({"ERROR"}, f"no .g files in {folder}")
            return {"CANCELLED"}
        done = 0
        for fn in files:
            try:
                import_unit(context, os.path.join(folder, fn),
                            use_anim=self.use_anim,
                            use_textures=self.use_textures, quiet=True)
                done += 1
            except Exception as exc:  # noqa: BLE001
                self.report({"WARNING"}, f"{fn}: {exc}")
        self.report({"INFO"}, f"imported {done}/{len(files)} unit(s)")
        return {"FINISHED"}
