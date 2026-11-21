"""Opt-in maintenance of the loose-file game index."""
import os
import bpy
from bpy.props import BoolProperty, StringProperty
from . import bridge


def update(context, export_dir=None):
    scene = context.scene
    index = bpy.path.abspath(scene.d3unit_index_path) if scene.d3unit_index_path else ''
    folder = bpy.path.abspath(scene.d3unit_index_folder) if scene.d3unit_index_folder else ''
    if not index or not folder:
        raise ValueError('сначала выберите файл index и папку юнита в игре')
    if export_dir and os.path.normcase(os.path.realpath(export_dir)) != os.path.normcase(os.path.realpath(folder)):
        raise ValueError('папка экспорта не совпадает с выбранной папкой юнита; скопируйте файлы и обновите индекс вручную')
    bridge.require()
    return bridge.module('index_update').update_folder(
        index, folder, add_auto_textures=scene.d3unit_index_add_auto)


REPORT = 'd3unit index report'


def summary(result):
    text = 'Индекс: обновлено %d, совпало %d, добавлено %d' % (
        len(result['changed']), len(result['matched']), len(result.get('added', [])))
    if result['backup']:
        text += '; резервная копия: ' + result['backup']
    elif not result['changed'] and not result.get('added'):
        text += '; изменений не было, копия не создавалась'
    if result['skipped']:
        text += '; нет в индексе: ' + ', '.join(result['skipped'])
    return text


def record(context, message, result=None, automatic=False):
    """Record the last attempt, including skipped names and the backup path."""
    scene = context.scene
    scene['d3unit_index_result'] = message
    lines = ['Последняя попытка: ' + ('автоматически после экспорта' if automatic else 'вручную'),
             'Файл index: ' + (scene.d3unit_index_path or '(не указан)'),
             'Папка юнита: ' + (scene.d3unit_index_folder or '(не указана)'),
             'Добавление авто-текстур: ' + ('включено' if scene.d3unit_index_add_auto else 'выключено'),
             '', message]
    if result is not None:
        for key, label in (('matched', 'Найдены в индексе'), ('changed', 'Обновлены'),
                           ('added', 'Добавлены'), ('skipped', 'Нет в индексе')):
            lines.append('\n' + label + ':')
            lines.extend(result.get(key) or ['нет'])
    try:
        block = bpy.data.texts.get(REPORT) or bpy.data.texts.new(REPORT)
        block.clear()
        block.write('\n'.join(lines) + '\n')
    except Exception as exc:  # noqa: BLE001 - report failure must not misstate index outcome
        print(f'[d3unit] Не удалось записать отчёт по индексу: {exc}')


class D3UNIT_OT_update_index(bpy.types.Operator):
    bl_idname = 'd3unit.update_index'
    bl_label = 'Обновить индекс игры'
    bl_description = 'Обновить выбранный index по файлам из игровой папки юнита; перед изменением создаётся резервная копия'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        import textwrap
        layout = self.layout
        scene = context.scene
        layout.label(text='Вы собираетесь изменить индекс распакованной игры', icon='ERROR')
        layout.label(text='Файл index:')
        for line in textwrap.wrap(scene.d3unit_index_path or '(не указан)', width=65,
                                  break_long_words=True, break_on_hyphens=False):
            layout.label(text=line)
        layout.label(text='Папка юнита:')
        for line in textwrap.wrap(scene.d3unit_index_folder or '(не указана)', width=65,
                                  break_long_words=True, break_on_hyphens=False):
            layout.label(text=line)
        layout.label(text=('Добавление авто-текстур: включено (эксперимент)' if
                           scene.d3unit_index_add_auto else 'Добавление авто-текстур: выключено'))
        layout.label(text='При изменении будет создана резервная копия index')
        layout.label(text='Проверьте пути и нажмите OK либо отмените операцию')

    def execute(self, context):
        try:
            result = update(context)
        except Exception as exc:
            message = 'Индекс не обновлён: ' + str(exc)
            record(context, message)
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        message = summary(result)
        record(context, message, result)
        self.report({'WARNING'} if result['skipped'] else {'INFO'}, message)
        return {'FINISHED'}


def register_properties():
    bpy.types.Scene.d3unit_index_path = StringProperty(name='Файл index', subtype='FILE_PATH', description='Существующий индекс распакованных файлов игры; это не индекс внутри vdpack')
    bpy.types.Scene.d3unit_index_folder = StringProperty(name='Папка юнита в игре', subtype='DIR_PATH', description='Папка юнита в игровых resources; хеши вычисляются по файлам в ней')
    bpy.types.Scene.d3unit_index_auto = BoolProperty(name='Обновлять после экспорта', default=False, description='Только при экспорте непосредственно в указанную игровую папку юнита; по умолчанию выключено')
    bpy.types.Scene.d3unit_index_add_auto = BoolProperty(name='Добавлять авто-текстуры', default=False, description='Экспериментально добавлять в index используемые авто-текстуры d3unit; в игре ещё не проверено')


def unregister_properties():
    for name in ('d3unit_index_path', 'd3unit_index_folder', 'd3unit_index_auto', 'd3unit_index_add_auto'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
