"""Stage a separate profile without changing game files."""
import os
import re
import bpy
from bpy.props import BoolProperty, StringProperty
from . import bridge


REPORT = 'd3unit new profile'


def form_hints(scene):
    """Cheap, non-blocking hints; the core still validates paths and conflicts."""
    hints = []
    if not scene.d3unit_profile_donor.strip():
        hints.append('Выберите профиль-донор (.char)')
    if not scene.d3unit_profile_workspace.strip():
        hints.append('Выберите существующую папку для проекта')
    new_id = scene.d3unit_profile_id.strip()
    if not new_id:
        hints.append('Введите новый ID')
    elif not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', new_id):
        hints.append('ID: 2–64 символа, первая — латинская буква')
        hints.append('Далее только строчные латинские буквы, цифры и _')
    return hints


def _record(scene, action, detail, *, clear=False, status=''):
    """Keep a labelled attempt history; never hide the inputs used by a step."""
    block = bpy.data.texts.get(REPORT) or bpy.data.texts.new(REPORT)
    if clear:
        block.clear()
    else:
        block.cursor_set(len(block.lines) - 1, character=len(block.lines[-1].body))
    donor = scene.d3unit_profile_donor or '(не указан)'
    workspace = scene.d3unit_profile_workspace or '(не указана)'
    new_id = scene.d3unit_profile_id.strip() or '(не указан)'
    block.write(f'\n--- {action} ---\nПрофиль-донор: {donor}\n'
                f'Папка для проекта: {workspace}\nID: {new_id}\n{detail}\n')
    scene['d3unit_profile_status'] = status or action


class D3UNIT_OT_stage_profile(bpy.types.Operator):
    bl_idname = 'd3unit.stage_profile'
    bl_label = 'Создать проект профиля'
    bl_description = 'Создать копии профиля в новой папке проекта; не регистрирует, не упаковывает и не устанавливает юнита'

    def execute(self, context):
        scene = context.scene
        try:
            donor = bpy.path.abspath(scene.d3unit_profile_donor) if scene.d3unit_profile_donor else ''
            workspace = bpy.path.abspath(scene.d3unit_profile_workspace) if scene.d3unit_profile_workspace else ''
            new_id = scene.d3unit_profile_id.strip()
            if not donor or not workspace or not new_id:
                raise ValueError('укажите профиль-донора, папку проекта и новый ID')
            bridge.require()
            result = bridge.module('new_unit').create_profile_project(
                os.path.dirname(donor), donor, new_id, os.path.join(workspace, new_id),
                copy_skill_grid=scene.d3unit_profile_copy_grid,
                warrior_visual_no_sets=scene.d3unit_profile_warrior_no_sets)
        except Exception as exc:
            message = 'Проект профиля не создан: ' + str(exc)
            _record(scene, 'Ошибка создания проекта', message,
                    status='Ошибка создания проекта — см. отчёт')
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        text = (f"Папка проекта: {result['project']}\n"
                f"ID донора: {result['donor_id']}\nНовый ID: {result['new_id']}\n"
                f"Скопированы разделы: {', '.join(result['copied'])}\n\n"
                f"Сетка навыков: {'отдельная копия' if result['skill_grid_copied'] else 'общая с донором'}\n"
                f"Профиль лидера без комплектов: {'да; визуальные файлы необходимо добавить отдельно' if result['warrior_visual_no_sets'] else 'нет; модель донора'}\n"
                "LOD, ключи локализации, навыки и другие ресурсы донора остаются общими.\n"
                "Экипировка и сохранения для варианта без комплектов не проверены.\n"
                "Юнит НЕ зарегистрирован, списки найма не изменены, мод не создан и не установлен.\n"
                "Проверьте ID в архивах и других модах. Совместимость с игрой не подтверждена.\n")
        _record(scene, 'Проект профиля создан', text, clear=True,
                status='Проект создан; юнит не зарегистрирован в игре')
        scene['d3unit_profile_result'] = result['project']
        self.report({'WARNING'}, 'Проект создан, но юнит не зарегистрирован в игре (см. d3unit new profile)')
        return {'FINISHED'}


class D3UNIT_OT_stage_empire_lists(bpy.types.Operator):
    bl_idname = 'd3unit.stage_empire_lists'
    bl_label = 'Подготовить списки Empire (эксперимент)'
    bl_description = ('Скопировать списки Empire в проект и добавить ID; '
                      'в игре не проверено, целые скрипты могут конфликтовать с другими модами')
    leader: BoolProperty(default=False, options={'HIDDEN'})

    def execute(self, context):
        scene = context.scene
        try:
            donor = bpy.path.abspath(scene.d3unit_profile_donor) if scene.d3unit_profile_donor else ''
            workspace = bpy.path.abspath(scene.d3unit_profile_workspace) if scene.d3unit_profile_workspace else ''
            new_id = scene.d3unit_profile_id.strip()
            if not donor or not workspace or not new_id:
                raise ValueError('укажите профиль-донора, папку проекта и новый ID')
            bridge.require()
            donor_id = None
            if self.leader:
                with open(donor, 'rb') as source:
                    donor_id = bridge.module('char').parse_char(source.read()).id
                if not donor_id:
                    raise ValueError('у донора нет ID лидера')
            files = bridge.module('new_unit').stage_empire_recruitment(
                os.path.dirname(donor), os.path.join(workspace, new_id), new_id,
                kind='leader' if self.leader else 'unit', donor_id=donor_id)
        except Exception as exc:
            message = 'Списки Empire не подготовлены: ' + str(exc)
            _record(scene, 'Ошибка подготовки списков Empire', message,
                    status='Ошибка подготовки списков — см. отчёт')
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        _record(scene, 'Списки Empire подготовлены',
                'Папка проекта: ' + os.path.join(workspace, new_id) +
                '\nТип записи: ' + ('leader' if self.leader else 'unit') +
                '\nЭкспериментальные копии списков:\n' + '\n'.join(files) +
                '\nПри включении в мод копии заменяют целые скрипты и могут конфликтовать с другими модами.\n'
                'Регистрация и наём в игре НЕ подтверждены. Файлы не установлены.',
                status='Списки подготовлены; в игре не проверено')
        self.report({'WARNING'}, 'Списки подготовлены; в игре не проверено (см. d3unit new profile)')
        return {'FINISHED'}


class D3UNIT_OT_open_profile_report(bpy.types.Operator):
    bl_idname = 'd3unit.open_profile_report'
    bl_label = 'Открыть отчёт проекта'
    bl_description = 'Показать отчёт в открытом Text Editor; если его нет, указать имя текста'

    def execute(self, context):
        block = bpy.data.texts.get('d3unit new profile')
        if block is None:
            self.report({'WARNING'}, 'Отчёта ещё нет: сначала создайте проект')
            return {'CANCELLED'}
        screen = getattr(context, 'screen', None)
        for area in getattr(screen, 'areas', ()) or ():
            if area.type == 'TEXT_EDITOR':
                area.spaces.active.text = block
                return {'FINISHED'}
        self.report({'INFO'}, 'Откройте Text Editor и выберите текст d3unit new profile')
        return {'FINISHED'}


def register_properties():
    bpy.types.Scene.d3unit_profile_donor = StringProperty(name='Профиль-донор', subtype='FILE_PATH', description='Выберите .char непосредственно в распакованной папке Profiles; рядом должны быть desc, stats и sound')
    bpy.types.Scene.d3unit_profile_workspace = StringProperty(name='Папка для проекта', subtype='DIR_PATH', description='Существующая папка вне игровых ресурсов; внутри создаётся новая папка с ID юнита')
    bpy.types.Scene.d3unit_profile_id = StringProperty(name='Новый ID', description='Уникальный ID из 2–64 символов: строчные латинские буквы, цифры и подчёркивание; первая — буква')
    bpy.types.Scene.d3unit_profile_warrior_no_sets = BoolProperty(name='Warrior: новая модель без комплектов', default=False, description='Эксперимент: ссылка на новую сцену, удаление известных комплектов; визуальные файлы нужно подготовить отдельно, LOD донора остаётся')
    bpy.types.Scene.d3unit_profile_copy_grid = BoolProperty(name='Отдельная сетка навыков', default=False, description='Скопировать сетку в проект и связать её с новым профилем; определения способностей остаются общими')


def unregister_properties():
    for name in ('d3unit_profile_donor', 'd3unit_profile_workspace', 'd3unit_profile_id', 'd3unit_profile_copy_grid', 'd3unit_profile_warrior_no_sets'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
