# -*- coding: utf-8 -*-
"""Disciples III unit I/O for Blender (GM engine: .g/.a/.scene/.ac/.t).

Install: ``Edit > Preferences > Add-ons > Install…`` and pick the add-on
folder or the ``disciples3_io.zip`` produced by ``release/build_addon.py``.

Usage
-----
*Import*: ``File > Import > Disciples III Unit`` and pick the unit's ``.g``.
The add-on converts the bundle to a glTF with ``d3tool`` and imports it:
one armature, one object per mesh part (body, cloth, wings, weapon…), the
full concatenated animation at 30 fps, and the texture atlas as a material.

*Edit*: shape the mesh, repaint weights, retime the action, add objects and
skin them to the armature — anything Blender can do.

*Export*: ``File > Export > Disciples III Unit`` (or the sidebar panel).
The scene is written as a glTF and converted back by ``d3tool`` using the
original bundle as a donor, so the engine-visible parts a glTF cannot carry
(``.ac`` event/sound table, ``.scene`` particle emitters, ``.a`` record
scaffolding, per-vertex diffuse, light-map UVs) survive untouched.

The format code lives in :mod:`d3tool` (pure Python, no dependencies); this
package only builds and reads the Blender scene.
"""
bl_info = {
    "name": "Disciples III Unit (GM .g/.a/.scene/.ac/.t)",
    "author": "Arena agent / geo2011",
    "version": (1, 1, 46),
    "blender": (3, 0, 0),
    "location": "File > Import/Export > Disciples III Unit",
    "description": "Import and export Disciples III character units "
                   "(geometry, skinning, animation, scene, textures)",
    "category": "Import-Export",
    "doc_url": "https://github.com/travagant/geo2011",
    "tracker_url": "https://github.com/travagant/geo2011/issues",
}

import os  # noqa: E402  (kept for the panels)
import tempfile  # noqa: E402

import bpy  # noqa: E402
from bpy.props import (BoolProperty, FloatProperty,  # noqa: E402
                       PointerProperty, StringProperty)
from bpy.types import Panel  # noqa: E402
from bpy_extras.io_utils import ImportHelper  # noqa: E402

from . import (bridge, exporter, importer, preferences,  # noqa: E402
               unitprops, weapon)


# --------------------------------------------------------------------------- #
#  the texture a part is drawn with
# --------------------------------------------------------------------------- #
def texture_folders(context, mesh=None) -> list:
    """Folders searched for a part's texture: the unit's, then the .blend's."""
    return unitprops.texture_folders(context, mesh)


def find_part_texture(context, mesh):
    """``(name, path)`` of the texture a mesh part is meant to wear."""
    name = str(mesh.get(unitprops.DIFFUSE_PROP) or "").strip()
    if not name:
        return "", ""
    try:
        tex = bridge.module("texture")
    except Exception:  # noqa: BLE001
        return name, ""
    return name, tex.find_texture_file(name, texture_folders(context, mesh)) or ""


def _blender_image(path: str):
    """A Blender-readable image for a GM texture (``.t`` -> a temporary ``.dds``).

    Blender has no ``.t`` loader, so the container is unwrapped into a plain
    ``.dds`` — the payload is byte-identical, only the 59-byte GM header goes.
    """
    if path.lower().endswith(".t"):
        tex = bridge.module("texture")
        with open(path, "rb") as fh:
            dds = tex.t_to_dds(fh.read(), source=path)
        stem = tex.clean_stem(os.path.basename(path)) or "texture"
        tmp = os.path.join(tempfile.gettempdir(), "d3unit_" + stem + ".dds")
        with open(tmp, "wb") as fh:
            fh.write(dds)
        path = tmp
    return bpy.data.images.load(path, check_existing=True)


def _assign_image(mesh, image) -> int:
    """Point every image node of the part's materials at *image*.

    The node feeding *Base Color* wins when a material has several (an atlas
    plus a light map), otherwise the first image node is reused.  A material
    without nodes (or without an image node) is left alone — the export does
    not read it, and inventing a shader for the artist is not this operator's
    job.
    """
    used = 0
    for slot in getattr(mesh, "material_slots", ()) or ():
        mat = slot.material
        if mat is None or not getattr(mat, "use_nodes", False):
            continue
        tree = getattr(mat, "node_tree", None)
        if tree is None:
            continue
        nodes = [n for n in tree.nodes if n.type == "TEX_IMAGE"]
        if not nodes:
            continue
        target = None
        for node in nodes:
            for link in tree.links:
                if link.from_node is node and link.to_socket.name == "Base Color":
                    target = node
                    break
            if target is not None:
                break
        node = target or nodes[0]
        node.image = image
        used += 1
    return used


def apply_part_texture(context, mesh=None) -> tuple:
    """Load the part's named texture into its material so the viewport matches.

    Returns ``(ok, message)``.  Setting the name only tells the exporter what to
    write; until the image is on the material Blender keeps drawing the old
    atlas, which looks exactly like a broken export.  This makes what the
    viewport shows and what the ``.g`` says the same thing.
    """
    mesh = mesh if mesh is not None else getattr(context, "object", None)
    if mesh is None or getattr(mesh, "type", "") != "MESH":
        return False, "select the mesh part first"
    name, path = find_part_texture(context, mesh)
    if not name:
        return False, (f"{mesh.name} has no texture name yet — set one in the "
                       f"panel (it is written into the .g and the .scene)")
    if not path:
        folders = ", ".join(texture_folders(context, mesh)) or "(no folder)"
        return False, f"{name} is not in {folders} — the export would name a file the game cannot find"
    try:
        image = _blender_image(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"could not read {os.path.basename(path)}: {exc}"
    used = _assign_image(mesh, image)
    try:
        image.pack()
    except Exception:  # noqa: BLE001 - packing is a convenience, not a must
        pass
    if not used:
        return False, (f"loaded {os.path.basename(path)} but {mesh.name} has "
                       f"no material image node to put it in")
    for area in getattr(context.screen, "areas", ()) or ():
        if area.type == "VIEW_3D":
            area.tag_redraw()
    return True, f"{mesh.name} -> {os.path.basename(path)}"


class D3UNIT_OT_apply_texture(bpy.types.Operator):
    """Show the named texture on the part (viewport = what the export writes)"""

    bl_idname = "d3unit.apply_texture"
    bl_label = "Show This Texture"
    bl_description = ("Load the texture the part is named after into its "
                      "material, so the viewport shows what the export will "
                      "write into the .g/.scene")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ok, message = apply_part_texture(context)
        self.report({"INFO"} if ok else {"WARNING"}, message)
        return {"FINISHED"} if ok else {"CANCELLED"}



#: text block the export writes its log into, and the scene property the
#: sidebar shows afterwards — warnings that only reach the system console are
#: warnings nobody reads, and "the export kept the donor's texture" is exactly
#: the kind of note that has to be in the artist's face
EXPORT_LOG = exporter.D3UNIT_EXPORT_LOG
NOTES_PROP = "d3unit_last_export"


def inherited_texture(context, mesh) -> str:
    """The name the export would keep for a part that names none itself.

    Reads it out of the donor `.g` the unit was imported from, so the panel can
    say "inherited: weapon_empire_inquisitor.tga" instead of leaving the artist
    guessing why a re-textured part still looks wrong.
    """
    if not mesh or unitprops.DIFFUSE_PROP in mesh:
        return ""
    root = unitprops.find_unit_root(context)
    info = unitprops.get_info(root) if root else {}
    g_path = str(info.get("g") or "")
    if not g_path or not os.path.isfile(g_path):
        return ""
    try:
        parts = bridge.module("parts")
        rows = parts.texture_report(g_path)
    except Exception:  # noqa: BLE001
        return ""
    for name, diffuse, _found in rows:
        if name == mesh.name or name == mesh.name.split(".")[0]:
            return diffuse
    return ""


def _draw_texture_box(layout, context, mesh) -> None:
    """The one material field Blender cannot infer: the part's texture name.

    It lives outside the unit-metadata branch on purpose — a scene built before
    the property existed, or assembled by hand, has no metadata but still needs
    to say which atlas its sword wears.
    """
    tb = layout.box()
    tb.label(text=f"Texture of {mesh.name}", icon="TEXTURE")
    row = tb.row(align=True)
    own = unitprops.DIFFUSE_PROP in mesh
    if own:
        row.prop(mesh, '["' + unitprops.DIFFUSE_PROP + '"]', text="Material")
    else:
        row.label(text="(not set)")
    op = row.operator("d3unit.name_texture", text="", icon="GREASEPENCIL")
    op.value = str(mesh.get(unitprops.DIFFUSE_PROP) or "")
    tb.operator("d3unit.add_texture",
                text="Add Texture File…  (writes <name>.t next to the unit)",
                icon="FILE_IMAGE")
    tb.operator("d3unit.number_materials",
                text="Number the materials: material0, material1, …")
    hint = tb.column(align=True)
    hint.scale_y = 0.8
    if not own:
        # the material on the part usually knows the atlas already; a packed
        # image carries no file URI into the glTF, so the export has to read
        # the name off the material itself (`unitprops.material_texture_name`)
        from_material = ""
        try:
            from_material = unitprops.material_texture_name(
                mesh, texture_folders(context, mesh))
        except Exception:  # noqa: BLE001 - a panel must never raise
            from_material = ""
        if from_material:
            hint.label(text="from the material: " + os.path.basename(from_material),
                       icon="CHECKMARK")
            hint.label(text="the export writes this name")
        else:
            inherited = inherited_texture(context, mesh)
            if inherited:
                hint.label(text="export keeps: " + os.path.basename(inherited),
                           icon="INFO")
                hint.label(text="set it above to change the atlas")
            else:
                hint.label(text="export keeps the material's texture", icon="INFO")
        _mats = [getattr(getattr(sl, "material", None), "name", "")
                 for sl in (getattr(mesh, "material_slots", ()) or ())]
        _mats = [m for m in _mats if m]
        if _mats:
            hint.label(text="materials: " + ", ".join(_mats), icon="MATERIAL")
        return
    _name, _path = find_part_texture(context, mesh)
    row2 = tb.row(align=True)
    row2.enabled = bool(_name)
    row2.operator("d3unit.apply_texture", text="Show in the Viewport",
                  icon="IMAGE_DATA")
    if _name and _path:
        _size = ""
        try:
            _size = bridge.module("texture").describe(_path, True)
            _size += f", {max(1, os.path.getsize(_path) // 1024)} KB"
        except Exception:  # noqa: BLE001
            _size = ""
        hint.label(text="file: " + os.path.basename(_path)
                        + (f"  ({_size})" if _size else ""), icon="CHECKMARK")
    elif _name:
        hint.label(text="NOT in the unit folder: "
                        + os.path.splitext(os.path.basename(_name))[0] + ".t",
                   icon="ERROR")
    else:
        hint.label(text="no texture named yet", icon="INFO")


class D3UNIT_OT_number_materials(bpy.types.Operator):
    """Renumber the unit's materials material0, material1, … in part order"""

    bl_idname = "d3unit.number_materials"
    bl_label = "Number the Materials"
    bl_description = ("Blender hands out material0, material0.001, material0.002 "
                      "for the parts of one unit; the .g numbers them "
                      "material0, material1, … per part, in slot order")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = unitprops.find_unit_root(context)
        meshes = [m for m in (unitprops.unit_meshes(arm) if arm else [])
                  if m.type == "MESH"]
        if not meshes:
            ob = getattr(context, "object", None)
            meshes = [ob] if ob is not None and ob.type == "MESH" else []
        if not meshes:
            self.report({"ERROR"}, "no unit mesh in this scene")
            return {"CANCELLED"}
        rows = unitprops.normalise_material_names(meshes)
        summary = "; ".join(f"{name}: {', '.join(slots) or '(none)'}"
                            for name, slots in rows)
        self.report({"INFO"}, summary or "no materials to number")
        return {"FINISHED"}


class D3UNIT_OT_open_log(bpy.types.Operator):
    """Open the text block the last export (or self-test) wrote"""

    bl_idname = "d3unit.open_log"
    bl_label = "Open the Export Log"
    bl_description = "Show the last export's log and warnings"
    bl_options = {"REGISTER"}

    def execute(self, context):
        block = bpy.data.texts.get(EXPORT_LOG)
        if block is None:
            self.report({"WARNING"}, "no export log yet")
            return {"CANCELLED"}
        for area in getattr(context.screen, "areas", ()) or ():
            if area.type != "TEXT_EDITOR":
                continue
            area.spaces[0].text = block
            return {"FINISHED"}
        self.report({"INFO"}, f"the log is in the Text editor: {EXPORT_LOG}")
        return {"FINISHED"}


class D3UNIT_OT_name_texture(bpy.types.Operator):
    """Name the texture this part wears in the game"""

    bl_idname = "d3unit.name_texture"
    bl_label = "Name the Texture…"
    bl_description = ("The name is written into the part's block in the .g, "
                      "the same block of the _lod.g and the gobj of both "
                      ".scene files on export")
    bl_options = {"REGISTER", "UNDO"}

    value: StringProperty(
        name="Texture",
        description="Material file name, e.g. weapon_empire_knight_sword_"
                    "diffuse.tga (the .t next to the unit is matched by stem)",
        default="")  # type: ignore[valid-type]

    def invoke(self, context, event):
        ob = getattr(context, "object", None)
        if ob is not None and ob.type == "MESH":
            self.value = str(ob.get(unitprops.DIFFUSE_PROP) or "")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob = getattr(context, "object", None)
        if ob is None or ob.type != "MESH":
            self.report({"ERROR"}, "select the mesh part first")
            return {"CANCELLED"}
        name = self.value.strip()
        try:
            if name:
                ob[unitprops.DIFFUSE_PROP] = name
            elif unitprops.DIFFUSE_PROP in ob:
                del ob[unitprops.DIFFUSE_PROP]
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"could not store the name: {exc}")
            return {"CANCELLED"}
        # the name alone changes nothing on screen: load the file it points at
        # so the viewport agrees with what the export is about to write
        if name:
            ok, message = apply_part_texture(context, ob)
            self.report({"INFO"} if ok else {"WARNING"},
                        f"{ob.name}: {name} — {message}")
        else:
            self.report({"INFO"}, f"{ob.name}: (none)")
        return {"FINISHED"}


class D3UNIT_OT_add_texture(bpy.types.Operator, ImportHelper):
    """Put a texture file into the unit folder and give it to this part"""

    bl_idname = "d3unit.add_texture"
    bl_label = "Add Texture File…"
    bl_description = (
        "Bring a texture you painted (.png/.tga/.bmp/.dds, or a .t) into the "
        "unit's folder as <name>.t — the only form the engine reads — and "
        "name this part after it, so the export writes the atlas into the .g, "
        "the _lod.g and both .scene files")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.png;*.tga;*.bmp;*.dds;*.t", options={"HIDDEN"})  # type: ignore[valid-type]

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ob = getattr(context, "object", None)
        if ob is None or getattr(ob, "type", "") != "MESH":
            self.report({"ERROR"}, "select the mesh part first")
            return {"CANCELLED"}
        folders = texture_folders(context, ob)
        if not folders:
            self.report({"ERROR"},
                        "no unit folder to put the texture in — import the "
                        "unit first, or save the .blend next to it")
            return {"CANCELLED"}
        # the unit's own folder is the one the game reads the `.t` from
        target = folders[0]
        try:
            tex = bridge.module("texture")
            dst = tex.add_texture(self.filepath, target)
        except Exception as exc:  # noqa: BLE001 - report, never crash Blender
            self.report({"ERROR"}, f"{exc}")
            return {"CANCELLED"}
        stem = tex.clean_stem(os.path.basename(dst))
        name = stem + ".tga"
        try:
            ob[unitprops.DIFFUSE_PROP] = name
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"could not store the texture name: {exc}")
            return {"CANCELLED"}
        ok, message = apply_part_texture(context, ob)
        self.report({"INFO"} if ok else {"WARNING"},
                    f"{ob.name}: wrote {os.path.basename(dst)} "
                    f"({max(1, os.path.getsize(dst) // 1024)} KB), part is "
                    f"now {name} — {message}")
        return {"FINISHED"}


class D3UNIT_OT_self_test(bpy.types.Operator):
    """Re-export the imported unit without changing anything and diff it"""

    bl_idname = "d3unit.self_test"
    bl_label = "Self-test: Re-export Unchanged"
    bl_description = (
        "Read the imported unit's .g again, write it out through the same "
        "format path the export uses, and compare every file byte by byte.  "
        "A unit nobody edited must come back identical: anything else here is "
        "a defect in this add-on, not in the unit")
    bl_options = {"REGISTER"}

    def execute(self, context):
        import os
        import tempfile

        root = unitprops.find_unit_root(context)
        info = unitprops.get_info(root) if root else {}
        folder = str(info.get("dir") or "")
        base = str(info.get("base") or "")
        if not folder or not base or not os.path.isdir(folder):
            self.report({"ERROR"}, "import a unit first")
            return {"CANCELLED"}
        try:
            bridge.require("")
            climod = bridge.module("cli")
            rtm = bridge.module("roundtrip")
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"d3tool is not usable: {exc}")
            return {"CANCELLED"}
        stem = os.path.join(folder, base)
        if not os.path.isfile(stem + ".g"):
            self.report({"ERROR"}, f"no {base}.g in {folder}")
            return {"CANCELLED"}
        tmp = tempfile.mkdtemp(prefix="d3selftest_")
        lines = [f"self-test: {base}", f"donor: {folder}", ""]
        bad = 0
        try:
            gltf = os.path.join(tmp, "unit.gltf")
            out = os.path.join(tmp, "out")
            climod._export_gl(stem + ".g",
                              climod._find_animation_for_geometry(stem + ".g"),
                              gltf, None, quiet=True)
            climod._export(gltf, out, 0, anim=True, quiet=True,
                           donor_dir=folder)
            diffs = rtm.diff_folders(folder, out)
            for diff in diffs:
                if diff.expected is None:      # an extra file is not a drift
                    continue
                lines.append(("  " if diff.same else "! ") + diff.summary())
                if not diff.same:
                    bad += 1
            counts = rtm.summary(diffs)
            lines += ["", f"{counts['identical']}/"
                          f"{counts['identical'] + bad} file(s) identical"]
        except Exception as exc:  # noqa: BLE001 - the report is the point
            lines.append(f"the re-export failed: {exc}")
            bad = -1
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        text = "\n".join(lines)
        block = bpy.data.texts.get(EXPORT_LOG)
        if block is None:
            block = bpy.data.texts.new(EXPORT_LOG)
        block.clear()
        block.write(text + "\n")
        for line in lines[:8]:
            print(f"[d3unit] {line}")
        if bad == 0:
            self.report({"INFO"}, "the unit re-exports byte for byte")
            return {"FINISHED"}
        self.report({"WARNING"}, f"{bad} file(s) came back different — see "
                                 f"the Text block '{EXPORT_LOG}'")
        return {"FINISHED"}


CLASSES = (
    preferences.D3UNIT_PT_preferences,
    preferences.D3UNIT_OT_check_tool,
    importer.D3UNIT_OT_import,
    importer.D3UNIT_OT_import_batch,
    exporter.D3UNIT_OT_export,
    weapon.D3UNIT_OT_place_weapon,
    D3UNIT_OT_name_texture,
    D3UNIT_OT_add_texture,
    D3UNIT_OT_apply_texture,
    D3UNIT_OT_open_log,
    D3UNIT_OT_number_materials,
    D3UNIT_OT_self_test,
)


# --------------------------------------------------------------------------- #
#  menu entries
# --------------------------------------------------------------------------- #
def _menu_import(self, context):
    self.layout.operator(importer.D3UNIT_OT_import.bl_idname,
                         text="Disciples III Unit (.g)")


def _menu_export(self, context):
    self.layout.operator(exporter.D3UNIT_OT_export.bl_idname,
                         text="Disciples III Unit (.g)")


# --------------------------------------------------------------------------- #
#  sidebar panel
# --------------------------------------------------------------------------- #
class D3UNIT_PT_panel(Panel):
    bl_idname = "D3UNIT_PT_panel"
    bl_label = "Disciples III Unit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Disciples III"

    def draw(self, context):
        layout = self.layout
        # weapon placement is a plain scene operation: it works on a unit that
        # was imported by this add-on and on a hand-assembled test scene alike
        ob = getattr(context, "active_object", None)
        scene = context.scene
        box = layout.box()
        box.label(text="Weapon placement", icon="SNAP_ON")
        col = box.column(align=True)
        col.prop(scene, "d3unit_weapon_src", text="Mesh")
        col.prop(scene, "d3unit_weapon_ref", text="Reference")
        opts = col.row(align=True)
        opts.prop(scene, "d3unit_weapon_roll", text="Roll")
        opts.prop(scene, "d3unit_weapon_flip", text="Flip")
        src = scene.d3unit_weapon_src
        if src is None and ob is not None and ob.type == "MESH":
            src = ob
        row = col.row(align=True)
        row.enabled = src is not None
        op = row.operator(weapon.D3UNIT_OT_place_weapon.bl_idname,
                          text="Place on Reference", icon="SNAP_ON")
        op.roll = scene.d3unit_weapon_roll
        op.flip = scene.d3unit_weapon_flip
        op.source = scene.d3unit_weapon_src.name if scene.d3unit_weapon_src else ""
        hint = col.column(align=True)
        hint.scale_y = 0.8
        if src is not None:
            hint.label(text=f"Mesh: {src.name}", icon="MESH_DATA")
        else:
            hint.label(text="pick the mesh to place", icon="INFO")
        hint.label(text="grip = the object's origin")
        hint.label(text="(or its one selected vertex)")

        mesh = ob if (ob is not None and ob.type == "MESH") else None
        if mesh is not None:
            _draw_texture_box(layout, context, mesh)
        root = unitprops.find_unit_root(context)
        info = unitprops.get_info(root) if root else {}
        if not info:
            layout.label(text="No unit imported yet", icon="INFO")
            col = layout.column(align=True)
            col.operator(importer.D3UNIT_OT_import.bl_idname,
                         text="Import Unit…", icon="IMPORT")
            return
        box = layout.box()
        box.label(text=info.get("base", "?"),
                  icon="ARMATURE_DATA" if root and root.type == "ARMATURE"
                  else "MESH_DATA")
        col = box.column(align=True)
        col.label(text=f"Folder: {info.get('dir', '?')}")
        anims = info.get("anims") or []
        col.label(text=f"Animation streams: {len(anims)}")
        if info.get("imported"):
            col.label(text=f"Imported: {info['imported']}")
        meshes = [o for o in context.scene.objects
                  if o.type == "MESH" and (o is root or o.parent is root
                                           or o.get(unitprops.PART_PROP))]
        col.label(text=f"Meshes: {len(meshes)}")
        notes = str(context.scene.get(NOTES_PROP) or "")
        if notes:
            nb = layout.box()
            nb.label(text="Last export", icon="INFO")
            for line in notes.splitlines()[:6]:
                nb.label(text=line[:70])
            if len(notes.splitlines()) > 6:
                nb.label(text=f"... {len(notes.splitlines()) - 6} more "
                              f"(Text block '{EXPORT_LOG}')")
            nb.operator("d3unit.open_log", text="Open the Log", icon="TEXT")
        op = layout.operator(exporter.D3UNIT_OT_export.bl_idname,
                             text="Export Unit", icon="EXPORT")
        op.filepath = info.get("g", "")
        row = layout.row(align=True)
        row.operator(importer.D3UNIT_OT_import.bl_idname, text="Re-import")
        row.operator("d3unit.self_test", text="Self-test", icon="CHECKMARK")
        layout.operator("d3unit.check_tool", text="Check d3tool")


# --------------------------------------------------------------------------- #
#  registration
# --------------------------------------------------------------------------- #
def register():
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # a reloaded add-on hits already-registered classes
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)
    try:
        bpy.utils.register_class(D3UNIT_PT_panel)
    except ValueError:
        bpy.utils.unregister_class(D3UNIT_PT_panel)
        bpy.utils.register_class(D3UNIT_PT_panel)

    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)

    # the weapon-placement panel remembers its reference across re-registers
    for prop in ("d3unit_weapon_src", "d3unit_weapon_ref",
                 "d3unit_weapon_roll", "d3unit_weapon_flip"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    bpy.types.Scene.d3unit_weapon_src = PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, ob: ob.type == "MESH",
        name="Mesh",
        description="Mesh to place on the reference weapon (empty = the "
                    "selected mesh)")
    bpy.types.Scene.d3unit_weapon_ref = PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, ob: ob.type == "MESH",
        name="Reference weapon",
        description="Mesh whose grip and long axis the placed mesh is aligned "
                    "to; empty = the only weapon-like mesh in the scene")
    bpy.types.Scene.d3unit_weapon_roll = FloatProperty(
        name="Roll",
        default=0.0,
        description="Extra spin about the reference axis, in degrees")
    bpy.types.Scene.d3unit_weapon_flip = BoolProperty(
        name="Flip",
        default=False,
        description="Turn the mesh half a circle about a perpendicular — the "
                    "answer to a blade pointing the wrong way")

    # a cheap self-check, visible in the console at start-up
    mod = bridge.load()
    print(f"[d3unit] add-on registered; {bridge.version_hint()}"
          if mod is not None else
          "[d3unit] add-on registered; d3tool NOT found (set its path in the "
          "add-on preferences)")


def unregister():
    for funcs, menu in ((_menu_import, bpy.types.TOPBAR_MT_file_import),
                        (_menu_export, bpy.types.TOPBAR_MT_file_export)):
        try:
            menu.remove(funcs)
        except (ValueError, AttributeError):
            pass
    for cls in (D3UNIT_PT_panel,) + tuple(reversed(CLASSES)):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
    for prop in ("d3unit_weapon_src", "d3unit_weapon_ref",
                 "d3unit_weapon_roll", "d3unit_weapon_flip"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


if __name__ == "__main__":
    register()
