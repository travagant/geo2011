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
        raise ValueError('select the game index and game unit folder first')
    if export_dir and os.path.normcase(os.path.realpath(export_dir)) != os.path.normcase(os.path.realpath(folder)):
        raise ValueError('export folder is not the selected game unit folder; copy files there and update index manually')
    bridge.require()
    return bridge.module('index_update').update_folder(
        index, folder, add_auto_textures=scene.d3unit_index_add_auto)


def summary(result):
    text = 'Index: %d updated, %d matched, %d added' % (len(result['changed']), len(result['matched']), len(result.get('added', [])))
    if result['backup']:
        text += '; backup: ' + result['backup']
    if result['skipped']:
        text += '; not in index: ' + ', '.join(result['skipped'])
    return text


class D3UNIT_OT_update_index(bpy.types.Operator):
    bl_idname = 'd3unit.update_index'
    bl_label = 'Update Game Index'
    bl_description = 'Update existing entries for files already in the selected game unit folder; make a backup'

    def execute(self, context):
        try:
            result = update(context)
        except Exception as exc:
            self.report({'ERROR'}, 'Index not updated: ' + str(exc))
            return {'CANCELLED'}
        message = summary(result)
        context.scene['d3unit_index_result'] = message
        self.report({'WARNING'} if result['skipped'] else {'INFO'}, message)
        return {'FINISHED'}


def register_properties():
    bpy.types.Scene.d3unit_index_path = StringProperty(name='Game index', subtype='FILE_PATH', description='Existing loose index file, not a vdpack')
    bpy.types.Scene.d3unit_index_folder = StringProperty(name='Game unit folder', subtype='DIR_PATH', description='Actual unit folder inside game resources; hashes are calculated from these files')
    bpy.types.Scene.d3unit_index_auto = BoolProperty(name='Update after export', default=False, description='Only when exporting directly into the selected game unit folder')
    bpy.types.Scene.d3unit_index_add_auto = BoolProperty(name='Add referenced auto textures (experimental)', default=False, description='Add index entries for d3unit textures referenced by geometry; not yet verified in game')


def unregister_properties():
    for name in ('d3unit_index_path', 'd3unit_index_folder', 'd3unit_index_auto', 'd3unit_index_add_auto'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
