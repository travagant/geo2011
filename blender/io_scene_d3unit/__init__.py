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
    "version": (1, 1, 79),
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

from . import (alignment, bridge, exporter, importer, index_ui, leader, new_leader_ui, new_unit_ui, preferences,  # noqa: E402
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
        return False, "Сначала выберите меш"
    name, path = find_part_texture(context, mesh)
    if not name:
        return False, (f"Для {mesh.name} не задано имя текстуры — укажите его на панели")
    if not path:
        folders = ", ".join(texture_folders(context, mesh)) or "папки не найдены"
        return False, f"Текстура {name} не найдена. Проверены папки: {folders}; проверьте файл .t"
    try:
        image = _blender_image(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"Не удалось прочитать {os.path.basename(path)}: {exc}"
    used = _assign_image(mesh, image)
    try:
        image.pack()
    except Exception:  # noqa: BLE001 - packing is a convenience, not a must
        pass
    if not used:
        return False, (f"{os.path.basename(path)} загружен, но у {mesh.name} нет узла изображения в материале")
    for area in getattr(getattr(context, "screen", None), "areas", ()) or ():
        if area.type == "VIEW_3D":
            area.tag_redraw()
    return True, f"{mesh.name} -> {os.path.basename(path)}"


class D3UNIT_OT_apply_texture(bpy.types.Operator):
    """Show the named texture on the part (viewport = what the export writes)"""

    bl_idname = "d3unit.apply_texture"
    bl_label = "Показать текстуру на модели"
    bl_description = ("Загрузить файл по заданному имени в узлы изображения материалов; "
                      "если прямого подключения к Base Color нет, изменится первый узел изображения")
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
SELF_TEST_LOG = "d3unit donor check"
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
    tb.label(text=f"Меш: {mesh.name}", icon="TEXTURE")
    row = tb.row(align=True)
    own = unitprops.DIFFUSE_PROP in mesh
    if own:
        row.prop(mesh, '["' + unitprops.DIFFUSE_PROP + '"]', text="Имя текстуры")
    else:
        row.label(text="Имя не задано")
    op = row.operator("d3unit.name_texture", text="Задать", icon="GREASEPENCIL")
    op.value = str(mesh.get(unitprops.DIFFUSE_PROP) or "")
    tb.operator("d3unit.add_texture",
                text="Добавить файл текстуры…",
                icon="FILE_IMAGE")
    folders = texture_folders(context, mesh)
    if folders:
        tb.label(text="Папка для нового .t:")
        for line in __import__('textwrap').wrap(folders[0], width=45, break_long_words=True, break_on_hyphens=False):
            tb.label(text=line)
    else:
        tb.label(text="Папка для текстуры пока не определена", icon="INFO")
    tb.operator("d3unit.number_materials",
                text="Пронумеровать материалы")
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
            hint.label(text="Из материала: " + os.path.basename(from_material),
                       icon="CHECKMARK")
            hint.label(text="Имя может использоваться при экспорте")
        else:
            inherited = inherited_texture(context, mesh)
            if inherited:
                hint.label(text="Будет сохранено: " + os.path.basename(inherited),
                           icon="INFO")
                hint.label(text="Изменить имя можно выше")
            else:
                hint.label(text="Имя не определено: проверьте экспорт", icon="INFO")
        _mats = [getattr(getattr(sl, "material", None), "name", "")
                 for sl in (getattr(mesh, "material_slots", ()) or ())]
        _mats = [m for m in _mats if m]
        if _mats:
            hint.label(text="Материалы: " + ", ".join(_mats), icon="MATERIAL")
        return
    _name, _path = find_part_texture(context, mesh)
    row2 = tb.row(align=True)
    row2.enabled = bool(_name)
    row2.operator("d3unit.apply_texture", text="Показать текстуру на модели",
                  icon="IMAGE_DATA")
    if _name and _path:
        _size = ""
        try:
            _size = bridge.module("texture").describe(_path, True)
            _size += f", {max(1, os.path.getsize(_path) // 1024)} KB"
        except Exception:  # noqa: BLE001
            _size = ""
        hint.label(text="Файл: " + os.path.basename(_path)
                        + (f"  ({_size})" if _size else ""), icon="CHECKMARK")
    elif _name:
        hint.label(text="Не найден файл: "
                        + os.path.splitext(os.path.basename(_name))[0] + ".t",
                   icon="ERROR")
    else:
        hint.label(text="Имя текстуры пока не задано", icon="INFO")


class D3UNIT_OT_number_materials(bpy.types.Operator):
    """Renumber the unit's materials material0, material1, … in part order"""

    bl_idname = "d3unit.number_materials"
    bl_label = "Пронумеровать материалы"
    bl_description = ("Назначить материалам имена material0, material1 и далее; "
                      "общие материалы будут переименованы и у других объектов")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = unitprops.find_unit_root(context)
        meshes = [m for m in (unitprops.unit_meshes(arm) if arm else [])
                  if m.type == "MESH"]
        if not meshes:
            ob = getattr(context, "object", None)
            meshes = [ob] if ob is not None and ob.type == "MESH" else []
        if not meshes:
            self.report({"ERROR"}, "Выберите меш или импортированного юнита")
            return {"CANCELLED"}
        rows = unitprops.normalise_material_names(meshes)
        summary = "; ".join(f"{name}: {', '.join(slots) or '(none)'}"
                            for name, slots in rows)
        self.report({"INFO"}, summary or "Нет материалов для нумерации")
        return {"FINISHED"}


class D3UNIT_OT_open_log(bpy.types.Operator):
    """Open the text block the last export (or self-test) wrote"""

    bl_idname = "d3unit.open_log"
    bl_label = "Открыть журнал"
    bl_description = "Показать журнал последнего экспорта или проверки в открытом Text Editor"
    bl_options = {"REGISTER"}

    log_name: StringProperty(default=EXPORT_LOG, options={'HIDDEN'})

    def execute(self, context):
        block = bpy.data.texts.get(self.log_name)
        if block is None:
            self.report({"WARNING"}, "Журнал пока пуст: сначала выполните экспорт или проверку")
            return {"CANCELLED"}
        for area in getattr(getattr(context, "screen", None), "areas", ()) or ():
            if area.type != "TEXT_EDITOR":
                continue
            area.spaces[0].text = block
            return {"FINISHED"}
        self.report({"INFO"}, f"Откройте Text Editor и выберите текст: {self.log_name}")
        return {"FINISHED"}


class D3UNIT_OT_name_texture(bpy.types.Operator):
    """Name the texture this part wears in the game"""

    bl_idname = "d3unit.name_texture"
    bl_label = "Задать имя текстуры"
    bl_description = ("Задать имя текстуры для этой части; пустое значение удаляет явное имя. "
                      "Файл должен находиться рядом с юнитом")
    bl_options = {"REGISTER", "UNDO"}

    value: StringProperty(
        name="Имя текстуры",
        description="Например, sword_diffuse.tga; рядом с юнитом ищется файл sword_diffuse.t",
        default="")  # type: ignore[valid-type]

    def invoke(self, context, event):
        ob = getattr(context, "object", None)
        if ob is not None and ob.type == "MESH":
            self.value = str(ob.get(unitprops.DIFFUSE_PROP) or "")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob = getattr(context, "object", None)
        if ob is None or ob.type != "MESH":
            self.report({"ERROR"}, "Сначала выберите меш")
            return {"CANCELLED"}
        name = self.value.strip()
        try:
            if name:
                ob[unitprops.DIFFUSE_PROP] = name
            elif unitprops.DIFFUSE_PROP in ob:
                del ob[unitprops.DIFFUSE_PROP]
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Не удалось сохранить имя: {exc}")
            return {"CANCELLED"}
        # the name alone changes nothing on screen: load the file it points at
        # so the viewport agrees with what the export is about to write
        if name:
            ok, message = apply_part_texture(context, ob)
            self.report({"INFO"} if ok else {"WARNING"},
                        f"{ob.name}: {name} — {message}")
        else:
            self.report({"INFO"}, f"{ob.name}: имя удалено")
        return {"FINISHED"}


class D3UNIT_OT_add_texture(bpy.types.Operator, ImportHelper):
    """Put a texture file into the unit folder and give it to this part"""

    bl_idname = "d3unit.add_texture"
    bl_label = "Добавить файл текстуры"
    bl_description = ("Создать .t в показанной папке юнита и назначить имя выбранному мешу. "
                      "Файл с совпадающим именем и другим содержимым не перезаписывается")
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
            self.report({"ERROR"}, "Сначала выберите меш")
            return {"CANCELLED"}
        folders = texture_folders(context, ob)
        if not folders:
            self.report({"ERROR"},
                        "Папка для текстуры не найдена: импортируйте юнита или сохраните .blend рядом с ним")
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
            self.report({"ERROR"}, f"Файл создан, но имя текстуры сохранить не удалось: {exc}")
            return {"CANCELLED"}
        ok, message = apply_part_texture(context, ob)
        self.report({"INFO"} if ok else {"WARNING"},
                    f"{ob.name}: файл {os.path.basename(dst)} "
                    f"({max(1, os.path.getsize(dst) // 1024)} КБ), имя {name} — {message}")
        return {"FINISHED"}


class D3UNIT_OT_self_test(bpy.types.Operator):
    """Re-export the imported unit without changing anything and diff it"""

    bl_idname = "d3unit.self_test"
    bl_label = "Проверить повторное преобразование донора"
    bl_description = ("Проверить преобразование исходных файлов юнита через временную папку. "
                      "Текущие изменения сцены и работа в игре не проверяются")
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
        block = bpy.data.texts.get(SELF_TEST_LOG)
        if block is None:
            block = bpy.data.texts.new(SELF_TEST_LOG)
        block.clear()
        block.write("Проверка файлов донора (изменения сцены не проверяются)\n" + text + "\n")
        for line in lines[:8]:
            print(f"[d3unit] {line}")
        if bad == 0:
            self.report({"INFO"}, "Проверенные файлы донора совпали побайтно; изменения сцены не проверялись")
            return {"FINISHED"}
        if bad < 0:
            self.report({"WARNING"}, f"Проверка не завершилась; откройте журнал {SELF_TEST_LOG}")
        else:
            self.report({"WARNING"}, f"Файлов с отличиями: {bad}; откройте журнал {SELF_TEST_LOG}")
        return {"FINISHED"}


CLASSES = (
    preferences.D3UNIT_PT_preferences,
    preferences.D3UNIT_OT_check_tool,
    importer.D3UNIT_OT_import,
    importer.D3UNIT_OT_import_batch,
    leader.D3UNIT_OT_import_leader,
    leader.D3UNIT_OT_export_leader,
    exporter.D3UNIT_OT_export,
    index_ui.D3UNIT_OT_update_index,
    new_unit_ui.D3UNIT_OT_stage_profile,
    new_unit_ui.D3UNIT_OT_stage_empire_lists,
    new_unit_ui.D3UNIT_OT_open_profile_report,
    new_leader_ui.D3UNIT_OT_stage_leader_visual,
    new_leader_ui.D3UNIT_OT_bundle_leader_test,
    weapon.D3UNIT_OT_place_weapon,
    alignment.D3UNIT_OT_align_model,
    alignment.D3UNIT_OT_work_placement,
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
        scene = context.scene
        ob = getattr(context, "active_object", None)
        root = unitprops.find_unit_root(context)
        info = unitprops.get_info(root) if root else {}

        box = layout.box()
        box.label(text="1. Импорт и экспорт", icon="IMPORT")
        box.label(text="Импортируйте .g, затем редактируйте модель")
        box.operator(importer.D3UNIT_OT_import.bl_idname,
                     text="Открыть юнита (.g)…", icon="IMPORT")
        box.operator(leader.D3UNIT_OT_import_leader.bl_idname,
                     text="Лидер целиком (.char)…", icon="ARMATURE_DATA")
        if info:
            box.label(text="Юнит: " + str(info.get("base", "?")), icon="ARMATURE_DATA")
            box.label(text="Потоков анимации: " + str(len(info.get("anims") or [])))
            folder = str(info.get("dir") or "")
            if folder:
                box.label(text="Исходная папка:")
                for line in __import__('textwrap').wrap(folder, width=45, break_long_words=True, break_on_hyphens=False):
                    box.label(text=line)
            if info.get("leader_reference_only"):
                box.label(text="Экспорт лидера: эксперимент", icon="ERROR")
                box.operator(leader.D3UNIT_OT_export_leader.bl_idname,
                             text="Экспорт частей лидера (.zip)…", icon="EXPORT")
                box.label(text="Только геометрия и веса; игра не проверена")
            else:
                export = box.operator(exporter.D3UNIT_OT_export.bl_idname,
                                      text="Экспортировать юнита…", icon="EXPORT")
                export.filepath = info.get("g", "")
                box.label(text="Внимание: проверьте папку сохранения", icon="ERROR")
                box.label(text="Можно перезаписать исходные файлы")
                box.label(text="При ошибке часть файлов может быть записана")
                box.operator("d3unit.self_test", text="Проверить файлы донора", icon="CHECKMARK")
                box.label(text="Изменения сцены при этом не проверяются")
        else:
            box.label(text="Выберите импортированного юнита для экспорта", icon="INFO")
        notes = str(scene.get(NOTES_PROP) or "")
        if notes:
            log = box.box()
            log.label(text="Последний экспорт: сообщения", icon="INFO")
            lines = []
            for message in notes.splitlines():
                lines.extend(__import__('textwrap').wrap(message, width=45,
                             break_long_words=True, break_on_hyphens=False) or [''])
            for line in lines[:6]:
                log.label(text=line)
            if len(lines) > 6:
                log.label(text="Остальные сообщения — в журнале")
        if bpy.data.texts.get(EXPORT_LOG):
            box.operator("d3unit.open_log", text="Журнал экспорта", icon="TEXT")
        if bpy.data.texts.get(SELF_TEST_LOG):
            op = box.operator("d3unit.open_log", text="Журнал проверки донора", icon="TEXT")
            op.log_name = SELF_TEST_LOG

        box = layout.box()
        row = box.row()
        row.prop(scene, 'd3unit_show_placement', text="2. Размещение модели",
                 icon='TRIA_DOWN' if scene.d3unit_show_placement else 'TRIA_RIGHT', emboss=False)
        if scene.d3unit_show_placement:
            box.label(text="Совместить новую модель с образцом")
            box.prop(scene, "d3unit_align_source", text="Новая модель")
            box.prop(scene, "d3unit_align_reference", text="Меш-образец")
            box.operator("d3unit.align_model", text="Переместить к образцу", icon="ORIENTATION_GLOBAL")
            box.label(text="Только перенос: не привязывает к скелету", icon="INFO")
            box.label(text="Масштаб и поворот настройте вручную")
            box.separator()
            active_work = root and alignment.WORK_PROP in root
            box.label(text="Временное положение для редактирования")
            if not active_work:
                box.prop(scene, "d3unit_work_angle", text="Доп. поворот (°)")
            box.operator("d3unit.work_placement", text=("Вернуть исходное положение" if active_work else "Удобно расположить юнита"))
            box.label(text="Выберите скелет; режим объекта", icon="INFO")
            if active_work:
                box.label(text="При экспорте используется исходное положение")

        box = layout.box()
        row = box.row()
        row.prop(scene, 'd3unit_show_texture', text="3. Текстуры и материалы",
                 icon='TRIA_DOWN' if scene.d3unit_show_texture else 'TRIA_RIGHT', emboss=False)
        if scene.d3unit_show_texture:
            box.label(text="Выберите меш для настройки текстуры")
            if ob is not None and ob.type == "MESH":
                _draw_texture_box(box, context, ob)
            else:
                box.label(text="Сейчас меш не выбран", icon="INFO")
            box.label(text="Прямая картинка Base Color: автоэкспорт")
            box.label(text="Сложные схемы материалов проверьте вручную")

        box = layout.box()
        row = box.row()
        row.prop(scene, 'd3unit_show_weapon', text="4. Размещение оружия",
                 icon='TRIA_DOWN' if scene.d3unit_show_weapon else 'TRIA_RIGHT', emboss=False)
        if scene.d3unit_show_weapon:
            box.label(text="Совместить оружие с мешем-образцом")
            box.prop(scene, "d3unit_weapon_src", text="Новое оружие")
            box.prop(scene, "d3unit_weapon_ref", text="Оружие-образец")
            opts = box.row(align=True)
            opts.prop(scene, "d3unit_weapon_roll", text="Поворот °")
            opts.prop(scene, "d3unit_weapon_flip", text="Перевернуть")
            box.prop(scene, "d3unit_weapon_match_parent", text="Привязать к кости образца")
            src = scene.d3unit_weapon_src or (ob if ob is not None and ob.type == "MESH" else None)
            row = box.row()
            row.enabled = src is not None
            op = row.operator(weapon.D3UNIT_OT_place_weapon.bl_idname,
                              text="Разместить по образцу", icon="SNAP_ON")
            op.roll = scene.d3unit_weapon_roll
            op.flip = scene.d3unit_weapon_flip
            op.match_parent = scene.d3unit_weapon_match_parent
            op.source = scene.d3unit_weapon_src.name if scene.d3unit_weapon_src else ""
            box.label(text="Точка хвата: origin или выбранная вершина", icon="INFO")
            box.label(text="Для исходной позы скелета выберите Rest Position")

        box = layout.box()
        row = box.row()
        row.prop(scene, 'd3unit_show_new_profile', text="5. Новый юнит — эксперимент",
                 icon='TRIA_DOWN' if scene.d3unit_show_new_profile else 'TRIA_RIGHT', emboss=False)
        if scene.d3unit_show_new_profile:
            box.label(text="Только подготовка проекта, не установка", icon="ERROR")
            box.label(text="Выберите .char из распакованных Profiles")
            box.prop(scene, 'd3unit_profile_donor', text="Профиль-донор")
            box.prop(scene, 'd3unit_profile_workspace', text="Папка для проекта")
            box.prop(scene, 'd3unit_profile_id', text="Новый ID")
            box.label(text="ID: 2–64 символа; первая — латинская буква")
            box.label(text="Далее: строчные латинские буквы, цифры, _")
            hints = new_unit_ui.form_hints(scene)
            for hint in hints:
                box.label(text=hint, icon="INFO")
            if not hints:
                project_path = os.path.join(bpy.path.abspath(scene.d3unit_profile_workspace),
                                            scene.d3unit_profile_id.strip())
                box.label(text="Будет создана новая папка:", icon="FILE_FOLDER")
                for line in __import__('textwrap').wrap(project_path, width=45, break_long_words=True, break_on_hyphens=False):
                    box.label(text=line)
                box.label(text="Пути и уникальность проверяются при создании")
            box.prop(scene, 'd3unit_profile_copy_grid', text="Отдельная сетка навыков")
            box.prop(scene, 'd3unit_profile_warrior_no_sets')
            if scene.d3unit_profile_warrior_no_sets:
                box.label(text='Warrior: модель отдельно, LOD донора', icon='ERROR')
            box.operator('d3unit.stage_profile', text="Создать проект профиля", icon="FILE_NEW")
            box.separator()
            box.label(text="Только Empire: копии списков найма")
            box.operator('d3unit.stage_empire_lists', text="Списки обычного юнита (unit)")
            op = box.operator('d3unit.stage_empire_lists', text="Списки лидера (leader)")
            op.leader = True
            box.label(text="Заменяют целые скрипты, возможен конфликт", icon="ERROR")
            box.label(text="Наём и игра ещё не проверены")
            box.separator()
            box.label(text="Лидер: экспериментальные визуальные файлы")
            box.label(text="Выделите скелет и привязанные меши")
            box.label(text="Для модели ID не длиннее 40 символов")
            box.prop(scene, 'd3unit_visual_scene', text="Сцена донора")
            box.operator('d3unit.stage_leader_visual', text="Подготовить визуальные файлы")
            box.label(text="Не мод: комплектов и LOD нет", icon="ERROR")
            box.prop(scene, 'd3unit_leader_icons')
            box.operator('d3unit.bundle_leader_test', text="Собрать тестовый архив")
            box.label(text="Только после проекта, списков и визуала", icon="INFO")
            status = str(scene.get('d3unit_profile_status', ''))
            if status:
                for line in __import__('textwrap').wrap(status, width=45, break_long_words=True, break_on_hyphens=False):
                    box.label(text=line, icon="INFO")
            result = str(scene.get('d3unit_profile_result', ''))
            if result:
                box.label(text="Последний созданный проект:", icon="FILE_FOLDER")
                for line in __import__('textwrap').wrap(result, width=45, break_long_words=True, break_on_hyphens=False):
                    box.label(text=line)
            if bpy.data.texts.get(new_unit_ui.REPORT):
                box.operator('d3unit.open_profile_report', text="Открыть отчёт проекта", icon="TEXT")

        box = layout.box()
        row = box.row()
        row.prop(scene, 'd3unit_show_index', text="6. Индекс игры — дополнительно",
                 icon='TRIA_DOWN' if scene.d3unit_show_index else 'TRIA_RIGHT', emboss=False)
        if scene.d3unit_show_index:
            box.label(text="Меняет индекс распакованных файлов игры", icon="ERROR")
            box.label(text="Это не индекс внутри .vdpack")
            box.label(text="Сначала сделайте копию файлов игры")
            box.prop(scene, 'd3unit_index_path', text="Файл index")
            box.prop(scene, 'd3unit_index_folder', text="Папка юнита в игре")
            box.prop(scene, 'd3unit_index_auto', text="Обновлять после экспорта")
            if scene.d3unit_index_auto:
                box.label(text="Автообновление без отдельного подтверждения", icon="ERROR")
            box.prop(scene, 'd3unit_index_add_auto', text="Добавлять авто-текстуры")
            box.label(text="Добавление текстур ещё не проверено в игре")
            box.operator('d3unit.update_index', text="Обновить индекс", icon="FILE_TICK")
            if bpy.data.texts.get(index_ui.REPORT):
                op = box.operator('d3unit.open_log', text="Отчёт по индексу", icon="TEXT")
                op.log_name = index_ui.REPORT
            result = str(scene.get('d3unit_index_result', ''))
            if result:
                for line in __import__('textwrap').wrap(result, width=45, break_long_words=True, break_on_hyphens=False):
                    box.label(text=line)
        layout.operator("d3unit.check_tool", text="Проверить d3tool", icon="CHECKMARK")


# --------------------------------------------------------------------------- #
#  registration
# --------------------------------------------------------------------------- #
def register():
    alignment.unregister_properties()
    alignment.register_properties()
    index_ui.unregister_properties()
    index_ui.register_properties()
    new_unit_ui.unregister_properties()
    new_unit_ui.register_properties()
    new_leader_ui.unregister_properties()
    new_leader_ui.register_properties()
    for name, label, default in (
        ('d3unit_show_placement', 'Размещение модели', True),
        ('d3unit_show_texture', 'Текстуры и материалы', True),
        ('d3unit_show_weapon', 'Размещение оружия', False),
        ('d3unit_show_new_profile', 'Новый юнит', False),
        ('d3unit_show_index', 'Индекс игры', False),
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
        setattr(bpy.types.Scene, name, BoolProperty(name=label, default=default))
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
                 "d3unit_weapon_roll", "d3unit_weapon_flip", "d3unit_weapon_match_parent"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    bpy.types.Scene.d3unit_weapon_src = PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, ob: ob.type == "MESH",
        name="Новое оружие",
        description="Меш для размещения; если поле пусто, используется выбранный меш")
    bpy.types.Scene.d3unit_weapon_ref = PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, ob: ob.type == "MESH",
        name="Оружие-образец",
        description="Меш-образец для совмещения точки хвата и длинной оси")
    bpy.types.Scene.d3unit_weapon_roll = FloatProperty(
        name="Поворот",
        default=0.0,
        description="Дополнительный поворот вокруг продольной оси образца в градусах")
    bpy.types.Scene.d3unit_weapon_flip = BoolProperty(
        name="Перевернуть",
        default=False,
        description="Развернуть оружие на пол-оборота, если клинок направлен не в ту сторону")
    bpy.types.Scene.d3unit_weapon_match_parent = BoolProperty(
        name="Привязать к кости образца", default=True,
        description="Если образец привязан как дочерний объект к кости, сменить родителя оружия на ту же кость; группы весов не меняются")

    # a cheap self-check, visible in the console at start-up
    mod = bridge.load()
    print(f"[d3unit] add-on registered; {bridge.version_hint()}"
          if mod is not None else
          "[d3unit] add-on registered; d3tool NOT found (set its path in the "
          "add-on preferences)")


def unregister():
    for name in ('d3unit_show_placement', 'd3unit_show_texture', 'd3unit_show_weapon',
                 'd3unit_show_new_profile', 'd3unit_show_index'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    alignment.unregister_properties()
    index_ui.unregister_properties()
    new_unit_ui.unregister_properties()
    new_leader_ui.unregister_properties()
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
                 "d3unit_weapon_roll", "d3unit_weapon_flip", "d3unit_weapon_match_parent"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


if __name__ == "__main__":
    register()
