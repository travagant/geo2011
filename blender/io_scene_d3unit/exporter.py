# -*- coding: utf-8 -*-
"""Export the edited unit back into Disciples 3 (GM engine) files.

The scene is written as a Blender glTF and then converted by ``d3tool``'s
reverse exporter, which is the code path verified byte-for-byte against
dis3tool's own exports (84/84 of the bundled references).  Everything the
engine needs but a glTF cannot express — the `.g` container header, per-vertex
diffuse, light-map UVs, the `.ac` event table, the `.scene` particle emitters,
the `.a` record scaffolding — is donated by the ORIGINAL bundle recorded at
import time, so an untouched import re-exports to the same bytes.

A unit may carry more meshes than the original `.g` did: a scene routinely
gets a new weapon, a cape split off with *Separate by Material*, or a prop
parented to a hand bone.  Every mesh bound to the armature is exported (as a
part of the compound container, rigidly bound to its bone when it has no
weights of its own), never silently dropped.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import bridge, unitprops
from .preferences import get_prefs

FPS = 30


# --------------------------------------------------------------------------- #
#  scene inspection
# --------------------------------------------------------------------------- #
def _collect_unit(arm, owner=None):
    """Armature + every object that belongs to it (children, bone props).

    Without an armature (a rigid unit) the tagged mesh objects are the unit.
    ``owner`` is the unit's base name: a scene may hold several Disciples
    units, and one export must never swallow another unit's meshes (the
    Blender glTF importer leaves unbound parts — a morph base, a rigid CPU
    part — in the scene, and they are tagged with their own owner).
    """
    if owner is None and arm is not None:
        owner = (unitprops.get_info(arm) or {}).get("base") or None

    def mine(ob):
        if not ob.get(unitprops.PART_PROP):
            return False
        tagged = ob.get(unitprops.OWNER_PROP)
        return owner is None or not tagged or tagged == owner

    if arm is None:
        return [o for o in bpy.context.scene.objects
                if o.type == "MESH" and mine(o)]
    objects = [arm]
    seen = {arm}
    # Every mesh the importer tagged belongs to the unit even when nothing
    # binds it to the armature: a morph base (the Hierophant robe) or a rigid
    # CPU-side part has no armature modifier at all.  They must go into the
    # glTF as well — a `.g` compound is rebuilt slot-by-slot against the
    # donor, and a mesh missing from the export shifts every remaining slot.
    for ob in list(bpy.context.scene.objects):
        if ob.type == "MESH" and mine(ob) and ob not in seen:
            objects.append(ob)
            seen.add(ob)
    changed = True
    while changed:
        changed = False
        for ob in list(bpy.context.scene.objects):
            if ob in seen or ob.type != "MESH":
                continue
            parent = ob.parent
            bound = any(mod.type == "ARMATURE" and mod.object is arm
                        for mod in ob.modifiers)
            if parent is arm or parent in seen or bound:
                objects.append(ob)
                seen.add(ob)
                changed = True
    # A compound unit ships one skin per part, so Blender builds one armature
    # per skin (Rod-1: the body's skin holds `Reference`, the alpha part's
    # skin holds `Head`/`Neck`/`Spine1`).  Every one of those armatures, and
    # every empty on the way down to it, has to go into the glTF too: an
    # object whose parent stays behind is re-hung under the nearest exported
    # bone without a parent bone recorded, and Blender's own exporter then
    # dies while sampling the armature action (`KeyError: None`, Rod-1 and
    # Nosferatu).
    extra = {mod.object for ob in objects for mod in ob.modifiers
             if mod.type == "ARMATURE" and mod.object is not None
             and mod.object not in seen}
    pending = list(objects) + list(extra)
    while pending:
        ob = pending.pop()
        parent = ob.parent
        # meshes are never added here: one would become an extra part
        if parent is not None and parent not in seen and parent.type in (
                "ARMATURE", "EMPTY"):
            pending.append(parent)
            extra.add(parent)
    for ob in extra:
        if ob not in seen:
            objects.append(ob)
            seen.add(ob)
    return objects


def _influence_report(mesh_obj, limit=4):
    """>limit influences per vertex makes the glTF exporter drop the tail."""
    too_many = 0
    worst = 0
    for v in mesh_obj.data.vertices:
        n = 0
        for g in v.groups:
            if g.weight > 1e-6:
                n += 1
        worst = max(worst, n)
        if n > limit:
            too_many += 1
    return too_many, worst


def validate_unit(arm, meshes, context=None):
    """Everything that would produce a broken unit, as readable warnings."""
    warns = []
    if not meshes:
        warns.append("the armature has no mesh bound to it — there is "
                     "nothing to export")
    total_verts = 0
    for ob in meshes:
        me = ob.data
        total_verts += len(me.vertices)
        if not len(me.polygons):
            warns.append(f"'{ob.name}' has no faces and will be skipped")
            continue
        if len(ob.material_slots) > 1:
            warns.append(f"'{ob.name}' has {len(ob.material_slots)} material "
                         f"slots; a .g part carries one material — only the "
                         f"first ({ob.material_slots[0].name or 'empty'}) is "
                         f"exported")
        # `.g` carries no transform: whatever the object's scale is, the
        # engine draws the part that many times bigger.  Borrowed geometry
        # keeps its own scale (a Knight sword sits at 1.802 in an Inquisitor
        # scene), so say it here instead of on the battlefield.
        sc = tuple(ob.scale)
        if any(abs(c - 1.0) > 1e-3 for c in sc):
            warns.append(
                f"'{ob.name}' has a scale of "
                f"{sc[0]:.3f}, {sc[1]:.3f}, {sc[2]:.3f} — a .g stores raw "
                f"mesh data, so the part is exported that many times bigger. "
                f"Apply it (Object > Apply > Scale) before exporting")
        elif not ob.material_slots or ob.material_slots[0].material is None:
            warns.append(f"'{ob.name}' has no material — its part will reuse "
                         f"the unit's main texture")
        if arm is None:
            # rigid unit: no skeleton to bind to, so nothing to check here
            continue
        bound = any(mod.type == "ARMATURE" and mod.object is arm
                    for mod in ob.modifiers)
        if not bound:
            if ob.parent is arm and ob.parent_type == "BONE":
                warns.append(f"'{ob.name}' is parented to the bone "
                             f"'{ob.parent_bone}' without an Armature "
                             f"modifier: it will be exported rigid, moving "
                             f"with that bone")
            elif ob.parent is arm:
                warns.append(f"'{ob.name}' is parented to the armature "
                             f"without an Armature modifier: it will be "
                             f"exported rigid (bound to the nearest bone) "
                             f"instead of deformed")
            elif ob.get(unitprops.PART_PROP):
                # A part the unit itself brought in (the Hierophant robe is a
                # morph base, the skirt is rigid in the donor `.g`) has no
                # deformation bones by design — saying so on every clean
                # round trip would train the user to ignore the panel.
                pass
            else:
                warns.append(f"'{ob.name}' is not attached to the armature: "
                             f"it is exported as a rigid part")
        else:
            too_many, worst = _influence_report(ob)
            if too_many:
                warns.append(
                    f"'{ob.name}' has {too_many} vertex(es) with more than 4 "
                    f"influences (max {worst}); glTF keeps the 4 strongest — "
                    f"normalise the weights to avoid losing influence")
        if not ob.vertex_groups and bound:
            warns.append(f"'{ob.name}' has an Armature modifier but no vertex "
                         f"groups — every vertex will sit at the origin in "
                         f"game")
    if total_verts == 0:
        warns.append("the unit has no geometry at all")
    return warns


def _pick_unit(context):
    """Resolve the unit: its armature (or the rigid meshes) and metadata."""
    tagged = [o for o in context.scene.objects
              if o.type == "ARMATURE" and unitprops.PROP in o]
    root = unitprops.find_unit_root(context)
    if root is not None and root.type != "ARMATURE":
        rinfo = unitprops.get_info(root) or {}
        meshes = _collect_unit(None, rinfo.get("base") or None) or [root]
        return None, meshes, rinfo or unitprops.get_info(
            meshes[0] if meshes else None)
    arm = root
    if arm is None:
        all_arms = [o for o in context.scene.objects if o.type == "ARMATURE"]
        if len(all_arms) == 1:
            arm = all_arms[0]
        elif not all_arms:
            meshes = [o for o in context.scene.objects if o.type == "MESH"]
            if not meshes:
                raise RuntimeError(
                    "nothing to export — import a unit first "
                    "(File > Import > Disciples III Unit)")
            return None, meshes, {}
        else:
            raise RuntimeError(
                "the scene has several armatures and none is selected — "
                "select the unit's armature (or one of its meshes) and try "
                "again")
    if len(tagged) > 1:
        others = [o.name for o in tagged if o is not arm]
        print(f"[d3unit] several units in the scene; exporting '{arm.name}' "
              f"(also present: {', '.join(others)})")
    return arm, None, unitprops.get_info(arm)


class _bone_parent_fixup:
    """Temporarily un-parent bone-parented props for the glTF export.

    Blender's bone parenting is not representable in glTF: the exporter
    hangs the object under the joint node but measures its transform from
    the bone's TAIL, so a plain glTF reader (d3tool included) resolves the
    object somewhere else entirely.  Re-parenting to the armature with the
    world transform preserved makes the node matrix mean what it says, and
    the bone the prop must follow travels in ``extras`` as ``d3unit_bone``
    (``export_extras`` is switched on for the export).
    """

    def __init__(self, arm, objects):
        self.arm = arm
        self.saved = []
        self.objects = objects

    def __enter__(self):
        for ob in self.objects:
            if ob.type != "MESH" or ob.parent is not self.arm:
                continue
            if getattr(ob, "parent_type", "OBJECT") != "BONE":
                continue
            bone = ob.parent_bone
            world = ob.matrix_world.copy()
            inv = ob.matrix_parent_inverse.copy()
            self.saved.append((ob, bone, world, inv))
            ob.parent_type = "OBJECT"
            ob.parent_bone = ""
            ob.matrix_parent_inverse = self.arm.matrix_world.inverted()
            ob.matrix_world = world
            ob["d3unit_bone"] = bone
        return self

    def __exit__(self, *exc):
        for ob, bone, world, inv in self.saved:
            ob.parent_type = "BONE"
            ob.parent_bone = bone
            ob.matrix_parent_inverse = inv
            ob.matrix_world = world
            if "d3unit_bone" in ob:
                del ob["d3unit_bone"]
        return False


# --------------------------------------------------------------------------- #
#  glTF writer
# --------------------------------------------------------------------------- #
def _gltf_export_props():
    try:
        return set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
    except Exception:  # noqa: BLE001
        return set()


def _export_gltf(context, path, objects, use_anim, base):
    """Write the unit's objects to a glTF Blender can and d3tool can read."""
    for ob in context.scene.objects:
        try:
            ob.select_set(False)
        except RuntimeError:
            pass
    for ob in objects:
        try:
            ob.select_set(True)
        except RuntimeError:
            pass
    context.view_layer.objects.active = objects[0] if objects else None

    desired = {
        "filepath": path,
        "export_format": "GLTF_SEPARATE",
        "use_selection": True,
        "export_yup": True,
        "export_apply": False,
        "export_texcoords": True,
        "export_normals": True,
        "export_materials": "EXPORT",
        "export_skins": True,
        "export_def_bones": False,
        "export_animations": bool(use_anim),
        "export_frame_range": False,
        "export_force_sampling": True,
        "export_optimize_animation_size": False,
        "export_anim_single_armature": True,
        "export_nla_strips": False,
        "export_animation_mode": "ACTIONS",
        "export_bake_animation": True,
        "export_reset_pose_bones": True,
        "export_anim_slide_to_zero": False,
        "export_current_frame": False,
        "export_extras": True,
        "export_cameras": False,
        "export_lights": False,
        "export_image_format": "AUTO",
    }
    props = _gltf_export_props()
    kwargs = {k: v for k, v in desired.items() if k in props}
    if "filepath" not in desired:            # pragma: no cover - defensive
        kwargs["filepath"] = path
    return bpy.ops.export_scene.gltf(**kwargs)


# --------------------------------------------------------------------------- #
#  core (also driven directly by the tests, without operators)
# --------------------------------------------------------------------------- #
def _stamp_diffuse_names(target: str, meshes) -> tuple:
    """Write every mesh object's ``d3unit_diffuse`` into the glTF as extras.

    Returns ``(stamped, missing)`` — the object names whose texture name could
    not be attached to any node.  ``d3tool`` reads the value on the way back
    (``d3tool.gltf._extras_diffuse``) and it outranks both the primitive's own
    image and the donor part's attribute, which is how a part whose texture
    lives in *another* unit's folder keeps a name the engine can resolve.
    """
    import json
    wanted = {}
    for ob in meshes:
        try:
            value = ob.get(unitprops.DIFFUSE_PROP)
        except Exception:  # noqa: BLE001
            value = None
        if not value:
            continue
        wanted[ob.name] = str(value)
        try:
            wanted.setdefault(ob.data.name, str(value))
        except Exception:  # noqa: BLE001
            pass
    if not wanted:
        return 0, []
    try:
        with open(target, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return 0, sorted(wanted)
    nodes = doc.get("nodes") or []
    meshes_json = doc.get("meshes") or []
    stamped, seen = 0, set()
    for node in nodes:
        for key in ("name", ""):
            value = wanted.get(node.get(key) or "")
            if not value:
                continue
            mi = node.get("mesh")
            if mi is None or mi >= len(meshes_json):
                continue
            mesh = meshes_json[mi]
            mesh.setdefault("extras", {})["d3unit_diffuse"] = value
            stamped += 1
            seen.add(node.get(key))
            break
    # A node is normally named after its object, but an exporter that folds
    # an object's name away (`.001` suffixes, a renamed node, a mesh shared by
    # two objects) would drop the name without a word.  Match the *mesh data*
    # name as a second chance before reporting the part as unattached.
    if len(seen) < len(wanted):
        by_mesh = {m.get("name"): i for i, m in enumerate(meshes_json)}
        for ob in meshes:
            if ob.name in seen:
                continue
            value = wanted.get(ob.name)
            if not value:
                continue
            try:
                data_name = ob.data.name
            except Exception:  # noqa: BLE001
                continue
            mi = by_mesh.get(data_name)
            if mi is None:
                mi = by_mesh.get(ob.name)
            if mi is None or mi >= len(meshes_json):
                continue
            meshes_json[mi].setdefault("extras", {})[
                "d3unit_diffuse"] = value
            stamped += 1
            seen.add(ob.name)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    missing = sorted(set(wanted) - seen)
    return stamped, [n for n in missing if n in {ob.name for ob in meshes}]


def export_unit(context, path: str, use_anim: bool = True, weights: int = 0,
                donor_dir: str = "", quiet: bool = False,
                keep_temp: bool = False) -> dict:
    """Export the scene's unit to ``path`` (a ``.g`` path)."""
    import io
    import contextlib

    bridge.require(_pref_path(context))
    cli = bridge.module("cli")
    path = os.path.abspath(path)
    out_dir = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(out_dir, exist_ok=True)

    arm, picked, info = _pick_unit(context)
    unit_objects = picked if picked is not None else _collect_unit(arm)
    if not donor_dir:
        donor_dir = info.get("dir") or ""
        if not donor_dir and os.path.isdir(out_dir):
            # exporting straight into a game folder: its .g/.a/.ac/.scene are
            # the donors; don't let the file we are about to overwrite be one
            donor_dir = out_dir
    donor_base = info.get("base") or base

    meshes = [o for o in unit_objects if o.type == "MESH"
              and not o.hide_render and len(o.data.polygons)]
    hidden = [o.name for o in unit_objects
              if o.type == "MESH" and o.hide_render]
    # every object of the unit goes to the glTF (empties keep parenting
    # transforms intact), but only the visible meshes are exported as parts
    objects = ([arm] if arm is not None else []) + [
        o for o in unit_objects if o.type != "MESH" or o in meshes]
    warns = validate_unit(arm, meshes, context)
    if hidden:
        warns.insert(0, "hidden mesh(es) skipped: " + ", ".join(hidden))
    # A mesh imported before 1.1.27 carries no `d3unit_diffuse`; its part then
    # keeps whatever texture the material or the donor .g names, which is how
    # a grafted sword came out wearing the donor's atlas without a word.
    unnamed = [o.name for o in meshes
               if not str(o.get(unitprops.DIFFUSE_PROP) or "").strip()]
    if unnamed:
        more = "" if len(unnamed) <= 6 else f" (+{len(unnamed) - 6} more)"
        warns.append(
            "part(s) with no texture name of their own — the export keeps "
            "whatever the material/donor .g names: "
            + ", ".join(unnamed[:6]) + more
            + " (set it in the sidebar: Texture of <part>)")

    scene = context.scene
    saved = (scene.render.fps, scene.render.fps_base, scene.frame_current,
             scene.frame_start, scene.frame_end)
    tmp = tempfile.mkdtemp(prefix="d3unit_out_")
    log = io.StringIO()
    try:
        scene.render.fps = FPS
        scene.render.fps_base = 1.0
        target = os.path.join(tmp, base + ".gltf")
        with contextlib.redirect_stdout(log), \
                contextlib.redirect_stderr(log), \
                _bone_parent_fixup(arm, objects):
            res = _export_gltf(context, target, objects, use_anim, base)
        if res != {"FINISHED"}:
            raise RuntimeError(f"the glTF exporter returned {res!r}")
        if not os.path.isfile(target):
            raise RuntimeError("the glTF exporter wrote no file")
        stamped, missing = _stamp_diffuse_names(target, meshes)
        if stamped:
            print(f"[d3unit] named the texture of {stamped} part(s) in the "
                  f"glTF (d3unit_diffuse)")
        if missing:
            warns.append(
                "texture name not attached to any node: "
                + ", ".join(missing))

        with contextlib.redirect_stdout(log), \
                contextlib.redirect_stderr(log):
            cli._export(target, out_dir, weights, anim=use_anim,
                        quiet=quiet, donor_dir=donor_dir or None)

        result = _verify(out_dir, base)
        if result["warnings"]:
            warns.extend(result["warnings"])
        tex_lines = _texture_lines(out_dir, base, donor_dir, warns)
        for line in tex_lines:
            print("[d3unit] texture" + line)
        text = log.getvalue() + "".join(
            "\n" + line for line in tex_lines)
        _log_to_text(f"{base} export", text + "\n" + "\n".join(warns))
        return {"g": os.path.join(out_dir, base + ".g"),
                "base": base, "out_dir": out_dir, "armature": arm,
                "rigid": arm is None,
                "meshes": meshes, "warnings": warns,
                "log": text, "stats": result["stats"]}
    finally:
        scene.render.fps, scene.render.fps_base = saved[0], saved[1]
        try:
            scene.frame_set(saved[2])
        except Exception:  # noqa: BLE001
            pass
        scene.frame_start, scene.frame_end = saved[3], saved[4]
        if keep_temp:
            print(f"[d3unit] temp folder kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def _pref_path(context):
    prefs = get_prefs(context)
    return getattr(prefs, "d3tool_path", "") if prefs else ""


def _texture_lines(out_dir, base, donor_dir, warns):
    """Read the written ``.g``/``_lod.g`` back: what texture each part wears.

    The name can come from three places (the mesh's own ``d3unit_diffuse``, the
    image Blender exported, the donor part) and nothing on the Blender side
    tells them apart — so the export reports what actually landed in the file,
    and says so when the file that name points at is not next to the unit.
    """
    try:
        parts = bridge.module("parts")
    except Exception:  # noqa: BLE001 - a report must never break an export
        return []
    lines, said = [], set()
    for suffix in ("", "_lod"):
        g_path = os.path.join(out_dir, base + suffix + ".g")
        if not os.path.isfile(g_path):
            continue
        try:
            rows = parts.texture_report(g_path, [donor_dir])
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{base}{suffix}.g: could not read the part "
                         f"textures back ({exc})")
            continue
        for part, diffuse, found in rows:
            if not diffuse:
                if part not in said:
                    said.add(part)
                    warns.append(
                        f"part '{part}' has no material0_diffuse in "
                        f"{base}{suffix}.g — the game draws it untextured")
                continue
            status = os.path.basename(found) if found else "NOT FOUND"
            lines.append(f"  {base}{suffix}.g: {part} -> {diffuse} ({status})")
            key = diffuse.lower()
            if not found and key not in said:
                said.add(key)
                warns.append(
                    f"part '{part}' wears {diffuse}, but no such texture is "
                    f"next to the unit — put {os.path.splitext(diffuse)[0]}"
                    f".t in the folder, or set another name in the panel "
                    f"(Texture of {part})")
    return lines


def _verify(out_dir, base):
    """Re-read what was written: counts for the report, defects as warnings."""
    out = {"warnings": [], "stats": {}}
    g_path = os.path.join(out_dir, base + ".g")
    try:
        gfile = bridge.module("gfile")
        mesh = gfile.parse_geometry_file(open(g_path, "rb").read())
        out["stats"] = {
            "parts": 1 + len(mesh.parts),
            "vertices": mesh.vertex_count + sum(p.vertex_count
                                                for p in mesh.parts),
            "triangles": mesh.tri_count + sum(p.tri_count
                                              for p in mesh.parts),
            "bones": len(mesh.bones),
            "parts_names": [mesh.unit_name or mesh.name]
            + [p.name for p in mesh.parts],
        }
        if mesh.parse_error:
            out["warnings"].append(f"{base}.g does not re-parse: "
                                   f"{mesh.parse_error}")
        # The engine's loader trusts the vertex/triangle pair every sub-mesh
        # header advertises; when it disagrees with the data behind it, the
        # unit crashes the game (and the toolkit) on load instead of looking
        # wrong.  Cheap to check here, impossible to notice afterwards.
        check = getattr(gfile, "count_mismatches", None)
        if check is not None:
            for line in check(mesh):
                out["warnings"].append(
                    f"{base}.g: {line} — the game will crash loading it")
    except Exception as exc:  # noqa: BLE001
        out["warnings"].append(f"could not re-read {base}.g: {exc}")
    return out


def _log_to_text(name, text):
    try:
        block = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        block.clear()
        block.write(text)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
#  operator
# --------------------------------------------------------------------------- #
class D3UNIT_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "d3unit.export_unit"
    bl_label = "Export Disciples III Unit"
    bl_description = ("Write the scene's unit back into GM files "
                      "(.g/.scene/.ac/.a + .t textures)")
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".g"
    filter_glob: StringProperty(default="*.g", options={"HIDDEN"})

    use_anim: BoolProperty(
        name="Export animation",
        default=True,
        description="Write the .a animation streams the unit's .ac names",
    )
    weights: EnumProperty(
        name="Influence slots",
        items=[("AUTO", "Auto (from the scene)",
                "Keep every influence the mesh actually uses"),
               ("2", "2 influences", "Smaller vertex records"),
               ("3", "3 influences", "The common Disciples 3 layout"),
               ("4", "4 influences", "Widest records")],
        default="AUTO",
        description="Influence slots written into the .g vertex records",
    )

    def invoke(self, context, event):
        # propose "<unit folder>/<base>.g" so a re-export overwrites the
        # unit in place instead of scattering a second copy
        if not self.filepath:
            root = unitprops.find_unit_root(context)
            info = unitprops.get_info(root) if root else {}
            if info.get("g"):
                self.filepath = info["g"]
            elif info.get("dir") and info.get("base"):
                self.filepath = os.path.join(info["dir"],
                                             info["base"] + ".g")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        prefs = get_prefs(context)
        keep = getattr(prefs, "keep_temp", False)
        try:
            w = 0 if self.weights == "AUTO" else int(self.weights)
            res = export_unit(context, self.filepath,
                              use_anim=self.use_anim, weights=w,
                              keep_temp=keep)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"export failed: {exc}")
            return {"CANCELLED"}
        stats = res.get("stats") or {}
        for warning in res["warnings"][:10]:
            self.report({"WARNING"}, warning)
        self.report({"INFO"},
                    f"Wrote {res['base']}.g — {stats.get('parts', '?')} part(s), "
                    f"{stats.get('vertices', '?')} vertices, "
                    f"{stats.get('bones', '?')} bones + .scene/.ac/.a")
        return {"FINISHED"}
