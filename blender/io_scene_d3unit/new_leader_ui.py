"""Create a non-installable experimental visual staging folder in Blender."""
import os
import re
import tempfile
import bpy
from bpy.props import StringProperty
from . import auto_texture, bridge, exporter, importer, new_unit_ui


def _same_folder_file(directory, reference, extension):
    pieces = re.split(r'[/\\]+', reference)
    if not pieces or any(p in ('.', '..') or ':' in p for p in pieces):
        raise ValueError('Недопустимая ссылка донора: ' + reference)
    basename = pieces[-1]
    if not basename.lower().endswith(extension):
        raise ValueError('Неподдерживаемая ссылка донора: ' + reference)
    names = [x for x in os.listdir(directory) if x.lower() == basename.lower()]
    if len(names) != 1 or os.path.islink(os.path.join(directory, names[0])):
        raise ValueError('Рядом со сценой отсутствует единственный ' + basename)
    path = os.path.join(directory, names[0])
    if not os.path.isfile(path):
        raise ValueError('Ресурс не является файлом: ' + path)
    return path


def stage_selected(context, donor_scene, workspace, new_id):
    """Export selected objects to a fresh folder; this is not a leader mod."""
    bridge.require(importer._get_pref_path(context))
    selected = list(context.selected_objects)
    rigs = [ob for ob in selected if ob.type == 'ARMATURE']
    meshes = [ob for ob in selected if ob.type == 'MESH']
    if len(rigs) != 1 or not meshes or len(rigs) + len(meshes) != len(selected):
        raise ValueError('Выделите один скелет и нужные меши; остальные объекты снимите с выделения')
    rig = rigs[0]
    if rig.mode != 'OBJECT' or rig.parent or rig.constraints:
        raise ValueError('Скелет должен быть без родителя и ограничений, в режиме Object')
    if not rig.animation_data or not rig.animation_data.action:
        raise ValueError('У скелета должна быть исходная анимация донора')
    for ob in meshes:
        if ob.mode != 'OBJECT' or ob.hide_render or ob.constraints or ob.data.shape_keys:
            raise ValueError('Проверьте режим, видимость, ограничения и shape keys: ' + ob.name)
        bound = [m for m in ob.modifiers if m.type == 'ARMATURE' and m.object == rig and m.show_viewport and m.show_render]
        if len(bound) != 1 or any(m.type != 'ARMATURE' or (m.object is not None and m.object != rig) for m in ob.modifiers):
            raise ValueError('Нужен один действующий Armature modifier на выбранный скелет: ' + ob.name)
        if exporter._influence_report(ob)[0]:
            raise ValueError('Более четырёх влияний на вершину: ' + ob.name)
        if len(ob.material_slots) != 1 or not ob.material_slots[0].material:
            raise ValueError('Нужен ровно один материал на меш: ' + ob.name)
    if not donor_scene or not workspace or not new_id:
        raise ValueError('Укажите сцену донора, существующую папку и новый ID')
    donor_scene = os.path.abspath(donor_scene)
    if os.path.islink(donor_scene) or not donor_scene.lower().endswith('.scene') or not os.path.isfile(donor_scene):
        raise ValueError('Выберите файл .scene донора')
    folder = os.path.dirname(donor_scene)
    with open(donor_scene, 'rb') as f:
        scene_bytes = f.read()
    scenemod = bridge.module('scene')
    nodes = scenemod.parse_scene(scene_bytes.decode('latin-1')).find_all('bones')
    if len(nodes) != 1 or not nodes[0].prop('file'):
        raise ValueError('Сцена должна содержать одну ссылку на .ac донора')
    ac_path = _same_folder_file(folder, nodes[0].prop('file'), '.ac')
    with open(ac_path, 'rb') as f:
        cfg = bridge.module('ac').parse_ac(f.read().decode('latin-1'))
    refs = {state.file.lower(): state.file for state in cfg.states if state.file}
    if not cfg.states or len(refs) != 1 or any(not state.file for state in cfg.states):
        raise ValueError('Поддерживается один поток анимации в конфигурации донора')
    anim_path = _same_folder_file(folder, next(iter(refs.values())), '.a')
    with open(anim_path, 'rb') as f:
        animation = bridge.module('anim').parse_anim(f.read())
    derived, textures, warnings, failed, per_slot = auto_texture.prepare(meshes)
    if warnings or failed or per_slot or set(derived) != {ob.name for ob in meshes}:
        raise ValueError('Не удалось однозначно извлечь изображение из каждого материала: ' + '; '.join(warnings))
    saved = (context.scene.render.fps, context.scene.render.fps_base,
             context.scene.frame_current, context.scene.frame_subframe,
             context.view_layer.objects.active, selected)
    try:
        context.scene.render.fps = 30
        context.scene.render.fps_base = 1.0
        with tempfile.TemporaryDirectory(prefix='d3leader_visual_') as tmp:
            path = os.path.join(tmp, 'visual.gltf')
            if exporter._export_gltf(context, path, [rig] + meshes, True, 'visual') != {'FINISHED'}:
                raise ValueError('Blender не сохранил промежуточный glTF')
            model = bridge.module('gltf').load_gltf(path)
            result = bridge.module('new_leader_staging').stage_visual_assets(
                model, animation, scene_bytes, new_id, derived, textures,
                os.path.join(workspace, new_id + '_visual_experiment'))
    finally:
        context.scene.render.fps, context.scene.render.fps_base = saved[:2]
        context.scene.frame_set(saved[2], subframe=saved[3])
        for ob in context.view_layer.objects:
            try:
                ob.select_set(False)
            except RuntimeError:
                pass
        for ob in saved[5]:
            try:
                ob.select_set(True)
            except RuntimeError:
                pass
        context.view_layer.objects.active = saved[4]
    return result


class D3UNIT_OT_stage_leader_visual(bpy.types.Operator):
    bl_idname = 'd3unit.stage_leader_visual'
    bl_label = 'Подготовить визуальные файлы лидера'
    bl_description = 'Эксперимент: создаёт .g, .scene и текстуры в новой папке; это не мод и не установка'

    def execute(self, context):
        sc = context.scene
        try:
            donor = bpy.path.abspath(sc.d3unit_visual_scene) if sc.d3unit_visual_scene else ''
            workspace = bpy.path.abspath(sc.d3unit_profile_workspace) if sc.d3unit_profile_workspace else ''
            name = sc.d3unit_profile_id.strip()
            info = stage_selected(context, donor, workspace, name)
        except Exception as exc:
            message = 'Визуальные файлы не подготовлены: ' + str(exc)
            new_unit_ui._record(sc, 'Ошибка подготовки лидера', message, status='Ошибка подготовки лидера — см. отчёт')
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        destination = os.path.join(workspace, name + '_visual_experiment')
        new_unit_ui._record(sc, 'Экспериментальные файлы лидера',
            'Папка: ' + destination + '\nЧастей: ' + str(len(info['parts'])) +
            '\nНЕ УСТАНАВЛИВАТЬ: это не мод. Профиля, комплектов и LOD нет.\n'
            'Анимация и эффекты ссылаются на донора; в игре не проверено.\n'
            'Текстура взята из материала, но его визуальный результат не проверен.',
            status='Визуальные файлы подготовлены; это не мод')
        self.report({'WARNING'}, 'Экспериментальные файлы готовы; не устанавливать (см. отчёт)')
        return {'FINISHED'}



class D3UNIT_OT_bundle_leader_test(bpy.types.Operator):
    bl_idname = 'd3unit.bundle_leader_test'
    bl_label = 'Собрать тестовый архив лидера'
    bl_description = 'Эксперимент: объединить проект и визуальную папку в новый тестовый архив, не устанавливая его'

    def execute(self, context):
        sc = context.scene
        try:
            donor = bpy.path.abspath(sc.d3unit_profile_donor) if sc.d3unit_profile_donor else ''
            workspace = bpy.path.abspath(sc.d3unit_profile_workspace) if sc.d3unit_profile_workspace else ''
            name = sc.d3unit_profile_id.strip()
            if not donor or not workspace or not name:
                raise ValueError('Укажите профиль-донора, папку и ID')
            bridge.require()
            info = bridge.module('new_leader_bundle').build_test_bundle(
                os.path.join(workspace, name), os.path.join(workspace, name + '_visual_experiment'),
                os.path.dirname(donor), name, os.path.join(workspace, name + '_test_bundle'),
                icon_png_dir=bpy.path.abspath(sc.d3unit_leader_icons) if sc.d3unit_leader_icons else None)
        except Exception as exc:
            message = 'Архив не создан: ' + str(exc)
            new_unit_ui._record(sc, 'Ошибка сборки лидера', message, status='Ошибка сборки — см. отчёт')
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        location = os.path.join(workspace, name + '_test_bundle', info['archive'])
        new_unit_ui._record(sc, 'Тестовый архив лидера',
            'Архив: ' + location + '\nSHA-256: ' + info['archive_sha256'] +
            '\nРесурсов: ' + str(info['resource_count']) +
            '\nPNG иконки: ' + ('да, конвертация не проверена в игре' if info['custom_icons'] else 'нет, остаются донорские') +
            '\nТолько тест: в игре не проверен, не установлен.\n'
            'LOD и зависимости донора сохраняются. Экипировка и сохранения не проверены.\n'
            'Полные копии скриптов Empire могут конфликтовать с другими модами.\n'
            'Не включайте одновременно другие пакеты с тем же ID.',
            status='Тестовый архив собран; в игре не проверен')
        self.report({'WARNING'}, 'Тестовый архив собран; не установлен (см. отчёт)')
        return {'FINISHED'}

def register_properties():
    bpy.types.Scene.d3unit_leader_icons = StringProperty(name='Папка PNG иконок (необязательно)', subtype='DIR_PATH', description='Шесть PNG точного размера: icon, subicon, layout_icon, capital_icon, dialog_icon, leader_icon; преобразование экспериментальное')
    bpy.types.Scene.d3unit_visual_scene = StringProperty(name='Сцена лидера-донора', subtype='FILE_PATH',
        description='Основной .scene донора; рядом должны находиться его .ac и .a')


def unregister_properties():
    if hasattr(bpy.types.Scene, 'd3unit_leader_icons'):
        del bpy.types.Scene.d3unit_leader_icons
    if hasattr(bpy.types.Scene, 'd3unit_visual_scene'):
        del bpy.types.Scene.d3unit_visual_scene
