"""Writer for the Disciples 3 scene description file (`.scene`).

The `.scene` is the editor/game scene that references a `.g` geometry and a
`.ac` animation config, positioning them under a "Scene Root" group.  This
module generates a minimal but valid scene that mirrors the structure emitted
by dis3tool.
"""
from __future__ import annotations

from typing import Dict


def _attr(k: str, v: str) -> str:
    return f'attrib #{k}' if False else f'\tattr "{k}" "{v}"'


def write_scene(
    mesh_name: str,
    base: str,
    res_root: str,
    attrs: Dict[str, str],
) -> str:
    """Render a `.scene` file for a skinned character.

    ``res_root`` is the relative resource directory, e.g.
    ``resources\\characters\\neutrals\\airelemental``.
    """
    geometry_file = f"{res_root}\\{base}.g"
    ac_file = f"{res_root}\\{base}.ac"
    lines = [
        "globalsettings ",
        "{",
        "\tfov 1.100000;",
        "\tfarplane 90000.000000;",
        "\tnearplane 0.100000;",
        "\twaterlevel 0.000000;",
        "\tshadowint 0.750000;",
        "\ttoolsscale 0.010000;",
        "\tambcolor -13684945;",
        "\tshadowcolor -16777216;",
        "\tportalrm 0;",
        "\tpostprocess 0;",
        "\tpostprocess_glow 0;",
        "\tpostprocess_blur 0;",
        "\tfogparams 1,2634320,0.660000,300.000000,5500.000000;",
        "\tload_counter 1507;",
        "}",
        "",
        'group "Scene Root" ',
        "{",
        "\tisrotate 0, 0, 0;",
        "\trotate 0.000000, 0.000000, 0.000000;",
        "\tapllyfluctuationtochildren 0;",
        "\tisfluctuation 0, 0, 0;",
        "\tfluctuation 0.000000, 0.000000, 0.000000;",
        "\tuid 181 40875272",
        "\tcoords 0.000000,0.000000,0.000000,0.000000,0.000000,0.000000",
        "\tdwflags 1",
        "\tplvl 512",
        "\tlodgroup 1",
        f'\tchild bones "{mesh_name}" ',
        "{",
        "\t\tisrotate 0, 0, 0;",
        "\t\trotate 0.000000, 0.000000, 0.000000;",
        "\t\tapllyfluctuationtochildren 0;",
        "\t\tisfluctuation 0, 0, 0;",
        "\t\tfluctuation 0.000000, 0.000000, 0.000000;",
        "\t\tuid 182 41514848",
        "\t\tcoords 0.000000,0.000000,0.000000,0.000000,0.000000,0.000000",
        "\t\tdwflags 1048576",
        f'\t\tfile "{ac_file}"',
        "\t\tplvl 512",
        "\t\tlooktocamera 0;",
        f"\t\tchild gobj \"{mesh_name}\" ",
        "\t\t{",
        "\t\t\tisrotate 0, 0, 0;",
        "\t\t\trotate 0.000000, 0.000000, 0.000000;",
        "\t\t\tapllyfluctuationtochildren 0;",
        "\t\t\tisfluctuation 0, 0, 0;",
        "\t\t\tfluctuation 0.000000, 0.000000, 0.000000;",
        "\t\t\tuid 209 108716040",
        "\t\t\tdwflags 1053761",
        "\t\t\ttech \"model\"",
        "\t\t\tbonesuid 182 41514848",
        "\t\t\tmesh_lods 1",
        f'\t\t\t"{geometry_file}" 0 0.000000',
        "\t\t\tcoords 0.000000,2.833831,0.005191,0.000000,0.000000,0.000000",
        '\t\t\tfragmentshader "system\\fragment\\default.fr"',
        '\t\t\ttransparentshader "system\\xshader\\transparent.xshader";',
        "\t\t\talphainrendertoshadowmap 0;",
        '\t\t\tblockshader "system\\xshader\\dif_light2x.xshader"',
        "\t\t\tlights_num 4",
        "\t\t\tplvl 512",
    ]
    for k, v in attrs.items():
        lines.append(f'\t\t\tattr "{k}" "{v}"')
    lines.append('\t\t\tattr "vdshader" "dif_light2x"')
    lines += [
        "\t\t}",
        "\t}",
        "}",
        "",
    ]
    return "\n".join(lines)
