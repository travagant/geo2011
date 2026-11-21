# -*- coding: utf-8 -*-
"""Assemble leader parts and experimentally export their original .g slots.

The normal unit exporter remains blocked. The leader ZIP writer is restricted
and deliberately does not export animations, materials, scenes, or topology.
"""
import os
import re
import tempfile
import hashlib
import json

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from . import bridge, importer, unitprops


def _resolve(root, ref):
    """Resolve a game resource path in an extracted tree, case-insensitively.

    The repo also has Characters' contents at its root. Do not search arbitrary
    subtrees or guess by basename: two files can legitimately share one name.
    """
    pieces = [p for p in re.split(r'[/\\]+', ref.strip()) if p]
    if not pieces or any(p in ('.', '..') or ':' in p for p in pieces):
        raise ValueError('Недопустимый путь ресурса: ' + ref)
    if pieces[0].lower() == 'resources':
        pieces = pieces[1:]
    variants = [pieces]
    if pieces and pieces[0].lower() == 'characters':
        variants.append(pieces[1:])
    found = []
    for variant in variants:
        current = root
        for piece in variant:
            if not os.path.isdir(current):
                break
            matches = [n for n in os.listdir(current) if n.lower() == piece.lower()]
            if len(matches) > 1:
                raise ValueError('Неоднозначный путь ресурса: ' + ref)
            if not matches:
                break
            current = os.path.join(current, matches[0])
        else:
            if os.path.isfile(current):
                found.append(current)
    found = list(dict.fromkeys(found))
    if len(found) != 1:
        raise FileNotFoundError('Не найден единственный ресурс: ' + ref)
    return found[0]


def _texture(value, folders, tmp):
    if not value:
        return None
    texture = bridge.module('texture')
    stem = texture.clean_stem(value)
    for folder in dict.fromkeys(folders):
        if not os.path.isdir(folder):
            continue
        names = os.listdir(folder)
        for ext in ('.dds', '.t'):
            hits = [n for n in names if n.lower() == (stem + ext).lower()]
            if len(hits) > 1:
                raise ValueError('Неоднозначная текстура: ' + stem)
            if hits:
                src = os.path.join(folder, hits[0])
                dst = os.path.join(tmp, stem + '.dds')
                with open(src, 'rb') as fh:
                    raw = fh.read()
                payload = raw if ext == '.dds' else texture.t_to_dds(raw, os.path.basename(src))
                if os.path.exists(dst):
                    with open(dst, 'rb') as fh:
                        if fh.read() != payload:
                            raise ValueError('Разные текстуры с одинаковым именем: ' + stem)
                else:
                    with open(dst, 'wb') as fh:
                        fh.write(payload)
                return os.path.basename(dst)
    return None


def build_reference(profile_path, number, tmp):
    from copy import deepcopy
    if number not in ('1', '2', '3'):
        raise ValueError('Комплект должен быть 1, 2 или 3')
    profile_path = os.path.abspath(profile_path)
    if os.path.basename(os.path.dirname(profile_path)).lower() != 'profiles':
        raise ValueError('Выберите профиль .char внутри папки Profiles')
    root = os.path.dirname(os.path.dirname(profile_path))
    ch, scenes, gfile, model = (bridge.module(n) for n in ('char','scene','gfile','model'))
    cli, out = bridge.module('cli'), bridge.module('gltf_out')
    profile = ch.parse_char(open(profile_path, 'rb').read())
    blocks = [i for i,b in enumerate(profile.blocks) if b.name.lower() == 'set' and b.argument.strip() == number]
    if len(blocks) != 1 or not profile.unit_scene:
        raise ValueError('В профиле нет единственного комплекта ' + number + ' или основной сцены')
    slots = profile.slots(blocks[0])
    if not slots:
        raise ValueError('Комплект не содержит частей')
    scene_paths = [_resolve(root, profile.unit_scene)] + [_resolve(root, ref) for ref in slots.values()]
    scene_docs = [scenes.parse_scene(open(p, 'r', encoding='utf-8-sig', errors='replace').read()) for p in scene_paths]
    nodes = scene_docs[0].find_all('bones')
    ac_refs = [n.props.get('file', '') for n in nodes if n.props.get('file')]
    if len(set(ac_refs)) != 1:
        raise ValueError('В основной сцене не найден единственный .ac скелета')
    ac_path = _resolve(root, ac_refs[0])
    ac = bridge.module('ac').parse_ac(open(ac_path, 'r', encoding='utf-8-sig', errors='replace').read())
    anim_refs = list(dict.fromkeys(st.file for st in ac.states if st.file))
    if len(anim_refs) != 1:
        raise ValueError('Пока поддерживается лидер с одним потоком анимации; найдено: ' + str(len(anim_refs)))
    anim_path = _resolve(root, anim_refs[0])
    a = bridge.module('anim').parse_anim(open(anim_path, 'rb').read())
    if a.raw or not a.bones:
        raise ValueError('Не удалось прочитать скелет и анимацию')
    bones = {b.name for b in a.bones}
    parts, textures, missing, files, cache, sources = [], {}, [], [], {}, []
    for scene_path, doc in zip(scene_paths, scene_docs):
        gobjs = doc.find_all('gobj')
        if not gobjs:
            raise ValueError('В сцене нет геометрии: ' + scene_path)
        for node in gobjs:
            refs = [re.search(r'"([^"\r\n]+\.g)"\s+(\d+)', line, re.I)
                    for line in node.items if isinstance(line, str)]
            refs = [m for m in refs if m]
            if len(refs) != 1:
                raise ValueError('Нужна одна ссылка на .g без LOD: ' + node.name)
            gpath = _resolve(root, refs[0].group(1))
            index = int(refs[0].group(2))
            if gpath not in cache:
                gm = gfile.parse_geometry_file(open(gpath, 'rb').read())
                if gm.parse_error:
                    raise ValueError('Ошибка чтения .g: ' + gpath)
                cache[gpath] = list(gfile.iter_parts(gm))
                files.append(gpath)
            if index >= len(cache[gpath]):
                raise ValueError('Индекс части за пределами .g: ' + node.name)
            part = cache[gpath][index]
            if not part.indices:
                # Leader Ranger/Thief BaseMesh is a non-rendered helper.
                continue
            unknown = [(i, b.name) for i, b in enumerate(part.bones) if b.name not in bones]
            used = [name for index, name in unknown if any(
                joint == index and weight != 0.0
                for v in part.vertices for weight, joint in zip(v.gltf_weights, v.gltf_joints))]
            if used:
                raise ValueError('Используемые кости части не найдены в анимации: ' + ', '.join(used))
            if not part.bones:
                raise ValueError('Часть без костей требует отдельной обработки: ' + node.name)
            part_name = getattr(part, 'unit_name', '') or part.name or node.name
            if any(p.name == part_name for p in parts):
                raise ValueError('Повторяется имя части: ' + part_name)
            p = model.MeshPart(name=part_name, vertices=part.vertices, indices=part.indices,
                bones=part.bones, vertex_count=part.vertex_count, tri_count=part.tri_count,
                weights_on_vertex=part.weights_on_vertex, material_diffuse=part.material_diffuse,
                lm_uv=part.lm_uv, lightmap=part.lightmap, morph=part.morph)
            parts.append(p)
            resource = refs[0].group(1).replace('\\', '/')
            if resource.lower().startswith('resources/'):
                resource = resource[len('resources/'):]
            sources.append({'path': gpath, 'resource': resource,
                            'index': index, 'name': part_name})
            value = p.material_diffuse
            if value:
                uri = _texture(value, [os.path.dirname(gpath), os.path.dirname(scene_path), os.path.dirname(scene_paths[0])], tmp)
                if uri:
                    textures[p.name] = uri
                else:
                    missing.append(value)
    if len(parts) < 2:
        raise ValueError('Недостаточно частей для сборки')
    first = parts[0]
    combined = model.SkinnedMesh(name=first.name, unit_name=first.name, vertices=first.vertices,
        indices=first.indices, bones=first.bones, vertex_count=first.vertex_count,
        tri_count=first.tri_count, weights_on_vertex=first.weights_on_vertex,
        material_diffuse=first.material_diffuse, lm_uv=first.lm_uv, lightmap=first.lightmap,
        morph=first.morph, parts=parts[1:])
    gltf = os.path.join(tmp, 'leader_reference.gltf')
    out.write_gltf_to(gltf, combined, a, textures=textures)
    importer._prune_missing_images(gltf)
    importer._prune_dangling_channels(gltf)
    return gltf, files, missing, profile.id or os.path.basename(profile_path), anim_path, sources


def _file_hash(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _rig_hash(arm):
    """Reject changed rest bones or animation: calibration needs the import action."""
    if not arm.animation_data or not arm.animation_data.action:
        raise ValueError('У скелета нет исходной анимации')
    if any(not track.mute for track in arm.animation_data.nla_tracks) or arm.animation_data.drivers or arm.constraints:
        raise ValueError('Активные NLA, драйверы и ограничения скелета не поддерживаются')
    if any(b.constraints for b in arm.pose.bones):
        raise ValueError('Ограничения костей не поддерживаются')
    bones = [(b.name, b.parent.name if b.parent else '',
              [round(float(v), 8) for row in b.matrix_local for v in row])
             for b in arm.data.bones]
    action = arm.animation_data.action
    curves = []
    for curve in action.fcurves:
        if curve.modifiers or curve.driver:
            raise ValueError('Модификаторы кривых анимации не поддерживаются')
        curves.append((curve.data_path, curve.array_index,
                       [(tuple(p.co), tuple(p.handle_left), tuple(p.handle_right),
                         p.interpolation) for p in curve.keyframe_points]))
    return hashlib.sha256(json.dumps([bones, curves], sort_keys=True).encode()).hexdigest()


def _mesh_layout_hash(ob):
    mesh = ob.data
    layout = [len(mesh.vertices), [list(p.vertices) + [p.material_index] for p in mesh.polygons],
              [(layer.name, [tuple(round(float(x), 7) for x in loop.uv)
                              for loop in layer.data]) for layer in mesh.uv_layers],
              [round(float(x), 7) for row in ob.matrix_basis for x in row],
              [round(float(x), 7) for row in ob.matrix_parent_inverse for x in row],
              ob.parent_type, ob.parent_bone]
    return hashlib.sha256(json.dumps(layout).encode()).hexdigest()


def _mesh_content_hash(ob):
    """Detect edits before retaining an original file with omitted faces."""
    mesh = ob.data
    content = [[(tuple(v.co), [(g.group, float(g.weight)) for g in v.groups])
                for v in mesh.vertices],
               [tuple(loop.normal) for loop in mesh.loops],
               [g.name for g in ob.vertex_groups]]
    return hashlib.sha256(json.dumps(content).encode()).hexdigest()


def import_leader(context, path, number='1'):
    bridge.require(importer._get_pref_path(context))
    path = os.path.abspath(path)
    before = set(bpy.data.objects)
    with tempfile.TemporaryDirectory(prefix='d3leader_') as tmp:
        gltf, files, missing, name, anim_path, sources = build_reference(path, number, tmp)
        try:
            result = importer.import_unit(context, gltf, pack=False, quiet=True)
            if len(result['armatures']) != 1:
                raise ValueError('Ожидался один общий скелет; импорт отменён')
            arm = result['armature']
            for obj in result['meshes']:
                if not any(m.type == 'ARMATURE' and m.object == arm for m in obj.modifiers):
                    raise ValueError('Часть не привязана к общему скелету: ' + obj.name)
            packed = importer._pack_images(context, tmp, True)
            by_name = {ob.name: ob for ob in result['meshes']}
            mapping_ok = (len(by_name) == len(sources) and
                          all(s['name'] in by_name for s in sources))
            if mapping_ok:
                for source in sources:
                    ob = by_name[source['name']]
                    ob['d3_leader_part'] = source['name']
                    source['layout'] = _mesh_layout_hash(ob)
                    source['content'] = _mesh_content_hash(ob)
                    source['sha256'] = _file_hash(source['path'])
            info = result['info']
            info.update({'base': name + '_set' + number, 'g': '',
                         'dir': os.path.dirname(path), 'gltf': '',
                         'leader_reference_only': True, 'textures': packed,
                         'leader_sources': sources if mapping_ok else [],
                         'leader_anim_path': anim_path, 'leader_anim_hash': _file_hash(anim_path),
                         'leader_rig_hash': _rig_hash(arm),
                         'leader_arm_matrix': [round(float(x), 7) for row in arm.matrix_basis for x in row],
                         'anims': [os.path.basename(anim_path)],
                         'warnings': ['Экспорт частей лидера экспериментальный; проверка в игре обязательна.']})
            if not mapping_ok:
                info['warnings'].append('Имена мешей неоднозначны; экспорт недоступен. Импортируйте в пустую сцену.')
            if missing:
                info['warnings'].append('Не найдены текстуры: ' + ', '.join(missing))
            unitprops.set_info(arm, info)
            for obj in result['meshes']:
                obj[unitprops.OWNER_PROP] = info['base']
            result['files'], result['missing_textures'] = files, missing
            return result
        except Exception:
            for ob in set(bpy.data.objects) - before:
                bpy.data.objects.remove(ob, do_unlink=True)
            raise


def export_leader(context, path):
    """Export existing leader parts to a new ZIP; never overwrite game assets.

    This deliberately supports vertex positions and weights only. The scene,
    animation, materials, UVs and topology are not exported.
    """
    from copy import copy, deepcopy
    import zipfile
    from . import exporter, alignment
    bridge.require(importer._get_pref_path(context))
    root = unitprops.find_unit_root(context)
    info = unitprops.get_info(root) if root else {}
    if not root or root.type != 'ARMATURE' or not info.get('leader_reference_only'):
        raise ValueError('Выберите импортированного лидера')
    if alignment.WORK_PROP in root:
        raise ValueError('Перед экспортом верните исходное положение юнита')
    if root.parent or root.mode != 'OBJECT':
        raise ValueError('Скелет должен быть без родителя и в режиме объекта')
    if [round(float(x), 7) for row in root.matrix_basis for x in row] != list(info.get('leader_arm_matrix') or []):
        raise ValueError('Трансформация скелета изменена; верните исходную')
    sources = info.get('leader_sources') or []
    if not sources:
        raise ValueError('Нет данных о частях; импортируйте лидера заново в пустую сцену')
    if _rig_hash(root) != info.get('leader_rig_hash'):
        raise ValueError('Скелет или исходная анимация изменены; экспорт отменён')
    if _file_hash(info['leader_anim_path']) != info.get('leader_anim_hash'):
        raise ValueError('Исходный файл анимации изменён')
    path = os.path.abspath(path)
    if not path.lower().endswith('.zip'):
        raise ValueError('Выберите путь нового ZIP-файла')
    if os.path.lexists(path):
        raise FileExistsError('Файл уже существует: ' + path)
    if not os.path.isdir(os.path.dirname(path)):
        raise ValueError('Папка экспорта не существует')
    objects = exporter._collect_unit(root)
    meshes = [ob for ob in objects if ob.type == 'MESH']
    by_name = {ob.get('d3_leader_part'): ob for ob in meshes if ob.get('d3_leader_part')}
    if len(meshes) != len(sources) or len(by_name) != len(sources):
        raise ValueError('Части удалены, продублированы или добавлены: экспорт отменён')
    for source in sources:
        name = source['name']
        ob = by_name.get(name)
        if ob is None or ob.name != name:
            raise ValueError('Часть отсутствует или переименована: ' + name)
        if ob.mode != 'OBJECT' or ob.hide_render or ob.data.shape_keys or ob.constraints:
            raise ValueError('Часть скрыта, в режиме редактирования, имеет shape keys или constraints: ' + name)
        if (len(ob.modifiers) != 1 or ob.modifiers[0].type != 'ARMATURE'
                or ob.modifiers[0].object != root or not ob.modifiers[0].show_viewport
                or ob.parent != root):
            raise ValueError('Изменена привязка или модификаторы части: ' + name)
        if _mesh_layout_hash(ob) != source['layout']:
            raise ValueError('Изменены топология, UV или трансформация части: ' + name)
        if exporter._influence_report(ob)[0]:
            raise ValueError('Более четырёх весов на вершину: ' + name)
        if _file_hash(source['path']) != source['sha256']:
            raise ValueError('Исходный .g изменён: ' + source['path'])
    gltfmod, gfile = bridge.module('gltf'), bridge.module('gfile')
    animation = bridge.module('anim').parse_anim(open(info['leader_anim_path'], 'rb').read())
    groups = {}
    for s in sources:
        key = s['path']
        groups.setdefault(key, []).append(s)
    staged, original_files = {}, []
    saved = (context.scene.render.fps, context.scene.render.fps_base,
             context.scene.frame_current, context.view_layer.objects.active,
             list(context.selected_objects))
    try:
        context.scene.render.fps = 30
        context.scene.render.fps_base = 1.0
        with tempfile.TemporaryDirectory(prefix='d3leader_export_') as tmp:
            gltf_path = os.path.join(tmp, 'leader.gltf')
            with alignment.original_for_export(root):
                if exporter._export_gltf(context, gltf_path, [root] + meshes, True, 'leader') != {'FINISHED'}:
                    raise ValueError('Blender не сохранил промежуточный glTF')
            model = gltfmod.load_gltf(gltf_path)
            if not model.blender_layout:
                raise ValueError('Неизвестная структура промежуточного glTF')
            models = [model] + model.submodels
            by_gltf_name = {m.mesh_name: m for m in models}
            if len(by_gltf_name) != len(sources) or set(by_gltf_name) != set(by_name):
                raise ValueError('Не удалось однозначно сопоставить меши glTF с частями')
            for donor_path, records in groups.items():
                donor = gfile.parse_geometry_file(open(donor_path, 'rb').read())
                if donor.parse_error:
                    raise ValueError('Ошибка чтения донора: ' + donor_path)
                slots = list(gfile.iter_parts(donor))
                records = sorted(records, key=lambda r: r['index'])
                if [r['index'] for r in records] != list(range(len(slots))):
                    raise ValueError('В .g есть несобранные или пустые части: ' + donor_path)
                if any(slot.morph or slot.lm_uv or any(v.diffuse != 0xffffffff for v in slot.vertices)
                       or int((slot.attrs or {}).get('materials_num', '1')) != 1
                       for slot in slots):
                    raise ValueError('Morph, несколько материалов, lightmap UV или цвет вершин пока не поддерживаются: ' + donor_path)
                resource = records[0]['resource'].replace('\\', '/')
                if any(r['resource'].lower() != resource.lower() for r in records):
                    raise ValueError('Неоднозначный путь игрового ресурса: ' + donor_path)
                pieces = resource.split('/')
                if any(p in ('', '.', '..') or ':' in p for p in pieces):
                    raise ValueError('Недопустимый путь внутри архива: ' + resource)
                if resource.lower() in {p.lower() for p in staged}:
                    raise ValueError('Повтор пути в архиве: ' + resource)
                saved_attrs = [(deepcopy(slot.attrs), deepcopy(slot.attr_items),
                                [b.name for b in slot.bones]) for slot in slots]
                # Force exact slot-to-object matching. The temporary name is
                # never serialized: original attribute blocks are restored below.
                for slot, record in zip(slots, records):
                    slot.attrs = dict(slot.attrs or {})
                    slot.attrs['name'] = record['name']
                chosen = [copy(by_gltf_name[r['name']]) for r in records]
                if any(len(slot.indices) != len(sub.indices) for slot, sub in zip(slots, chosen)):
                    # Blender can discard duplicate triangles at import (Ranger
                    # set 3 weapon). Only an entirely unedited file may be
                    # kept as original; otherwise never silently lose edits.
                    if any(_mesh_content_hash(by_name[r['name']]) != r.get('content') for r in records):
                        raise ValueError('Изменена часть файла, где Blender потерял треугольники: ' + donor_path)
                    staged[resource] = open(donor_path, 'rb').read()
                    original_files.append(resource)
                    continue
                for sub in chosen:
                    sub.material_image = sub.material_override = ''
                    sub.material_slots = {}
                    sub.material_triangles = {}
                part = chosen[0]
                part.nodes = model.nodes
                part.anim_channels = model.anim_channels
                part.anim_times = model.anim_times
                part.blender_layout = True
                part.submodels = chosen[1:]
                rebuilt = gltfmod.mesh_to_skinned(part, donor=donor,
                                                   anim_donors=[animation], inverse_blend=True)
                new_slots = list(gfile.iter_parts(rebuilt))
                if len(new_slots) != len(slots):
                    raise ValueError('Изменилось число частей в .g: ' + donor_path)
                for new, (attrs, attr_items, bone_names) in zip(new_slots, saved_attrs):
                    if [b.name for b in new.bones] != bone_names:
                        raise ValueError('Новые кости в части не поддерживаются: ' + donor_path)
                    new.attrs, new.attr_items = attrs, attr_items
                raw = gfile.write_geometry_file(rebuilt, rebuilt.attrs)
                verify = gfile.parse_geometry_file(raw)
                if verify.parse_error or gfile.count_mismatches(verify):
                    raise ValueError('Проверка созданного .g не пройдена: ' + donor_path)
                if len(list(gfile.iter_parts(verify))) != len(slots):
                    raise ValueError('Проверка числа частей не пройдена: ' + donor_path)
                staged[resource] = raw
        # Exclusive creation avoids overwrites/races. Remove only our own
        # incomplete output if ZIP writing fails.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(fd, 'wb') as fh, zipfile.ZipFile(fh, 'w', zipfile.ZIP_DEFLATED) as z:
                for resource, raw in sorted(staged.items()):
                    z.writestr(resource, raw)
                z.writestr('LEADER_EXPORT_README.txt',
                    'Experimental geometry-only export. Original .scene, .a, .ac and textures are not included.\n'
                    'Install only in a separate test mod/copy. Verify all sets and LODs in game.\n'
                    'Files kept byte-for-byte because Blender omitted faces: ' +
                    (', '.join(original_files) if original_files else 'none') + '\n')
        except BaseException:
            os.unlink(path)
            raise
        return {'path': path, 'files': len(staged), 'original_files': original_files}
    finally:
        scene = context.scene
        scene.render.fps, scene.render.fps_base = saved[:2]
        scene.frame_set(saved[2])
        for ob in context.selected_objects:
            ob.select_set(False)
        for ob in saved[4]:
            ob.select_set(True)
        context.view_layer.objects.active = saved[3]


class D3UNIT_OT_export_leader(bpy.types.Operator, ExportHelper):
    bl_idname = 'd3unit.export_leader'
    bl_label = 'Экспортировать части лидера в ZIP'
    bl_description = 'Экспериментальный экспорт геометрии и весов; исходные файлы не перезаписываются'
    filename_ext = '.zip'
    filter_glob: StringProperty(default='*.zip', options={'HIDDEN'})

    def execute(self, context):
        try:
            result = export_leader(context, self.filepath)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, 'Экспорт лидера не выполнен: ' + str(exc))
            return {'CANCELLED'}
        if result['original_files']:
            self.report({'WARNING'}, 'Сохранены оригиналы без изменений: ' + ', '.join(result['original_files']))
        self.report({'WARNING'}, 'Создан ZIP: %d .g. Обязательно проверьте в игре.' % result['files'])
        return {'FINISHED'}


class D3UNIT_OT_import_leader(bpy.types.Operator, ImportHelper):
    bl_idname = 'd3unit.import_leader'
    bl_label = 'Открыть лидера целиком'
    bl_description = 'Собрать комплект из профиля на одном скелете для работы в Blender'
    bl_options = {'UNDO'}
    filename_ext = '.char'
    filter_glob: StringProperty(default='*.char', options={'HIDDEN'})
    kit: EnumProperty(name='Комплект', items=[('1','Комплект 1',''),('2','Комплект 2',''),('3','Комплект 3','')], default='1')

    def execute(self, context):
        try:
            r = import_leader(context, self.filepath, self.kit)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, 'Импорт лидера не выполнен: ' + str(exc))
            return {'CANCELLED'}
        self.report({'WARNING'}, 'Экспорт геометрии лидера экспериментальный; требуется проверка в игре')
        if r['missing_textures']:
            self.report({'WARNING'}, 'Не найдены текстуры: ' + ', '.join(r['missing_textures']))
        return {'FINISHED'}
