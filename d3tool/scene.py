"""Writer for the Disciples 3 scene description file (`.scene`).

The `.scene` is the editor/game scene that references a `.g` geometry and a
`.ac` animation config, positioning them under a "Scene Root" group.  This
module writes a scene that mirrors the structure emitted by dis3tool for a
skinned character.

The particle emitters present in the original `.scene` files (e.g. the 9
"child particles" blocks on the AirElemental, 1 on the Zombie) are *authoring
data* that is not present in the geometry/glTF, so they cannot be reconstructed
from a reverse export.  When the source unit folder already contains a
`.scene`, the exporter reuses it verbatim so those emitters (and the GUI
camera) are preserved; otherwise a faithful, particle-free scene is generated.
"""
from __future__ import annotations

from typing import Dict, Optional


def _node_header(ind: str, name: str, kind: str = "bones") -> list:
    """Common header lines for a `child <kind> "<name>"` block."""
    return [
        f"{ind}child {kind} \"{name}\" ",
        f"{ind}{{",
        f"{ind}\tisrotate 0, 0, 0;",
        f"{ind}\trotate 0.000000, 0.000000, 0.000000;",
        f"{ind}\tapllytransformtochildren 0;",
        f"{ind}\tisfluctuation 0, 0, 0;",
        f"{ind}\tfluctuation 0.000000, 0.000000, 0.000000;",
        f"{ind}\tfluctuationamplitude 1.000000, 1.000000, 1.000000;",
        f"{ind}\tapllyfluctuationtochildren 0;",
    ]


def write_scene(
    mesh_name: str,
    base: str,
    res_root: str,
    attrs: Dict[str, str],
    *,
    gobj_name: Optional[str] = None,
    obb: Optional[str] = None,
) -> str:
    """Render a `.scene` file for a skinned character.

    ``res_root`` is the relative resource directory, e.g.
    ``resources\\characters\\neutrals\\airelemental``.  ``base`` is the geometry
    basename (used for the `bones` node and the `.g`/`.ac` file paths);
    ``gobj_name`` is the display name used for the `gobj` node (defaults to
    ``mesh_name``).  ``obb`` is an optional 6-value OBB box.
    """
    geometry_file = f"{res_root}\\{base}.g"
    ac_file = f"{res_root}\\{base}.ac"
    gobj = gobj_name or mesh_name
    if obb is None:
        obb = "-0.010000,-0.010000,-0.010000,0.010000,0.010000,0.010000"

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
        "\tguicamera -2.918151,1.822160,4.188418,-2.458722,1.717352,3.306410,1.315115,0.856448,-3.938563,-2.918151,1.822160,4.188418,0.105000,-9.904998,0.000000,14.319616;",
        "\tblur_burn 1.000000;",
        "\tglow_power 1.000000;",
        "\tglow_offset 1.000000;",
        "\tglow_color 1.000000,1.000000,1.000000;",
        "\tload_counter 1507;",
        "}",
        "",
        'group "Scene Root" ',
        "{",
        "\tisrotate 0, 0, 0;",
        "\trotate 0.000000, 0.000000, 0.000000;",
        "\tapllytransformtochildren 0;",
        "\tisfluctuation 0, 0, 0;",
        "\tfluctuation 0.000000, 0.000000, 0.000000;",
        "\tfluctuationamplitude 1.000000, 1.000000, 1.000000;",
        "\tapllyfluctuationtochildren 0;",
        "\tuid 181 40875272",
        "\tcoords 0.000000,0.000000,0.000000,0.000000,0.000000,0.000000",
        f"\tobbdata {obb}",
        "\tdwflags 1",
        "\tplvl 512",
        "\tlodgroup 1",
    ]

    # ---- child bones ----
    lines += _node_header("\t", base, "bones")
    lines += [
        "\t\tuid 182 41514848",
        "\t\tcoords 0.000000,0.000000,0.000000,0.000000,0.000000,0.000000",
        "\t\tdwflags 1048576",
        f'\t\tfile "{ac_file}"',
        "\t\tplvl 512",
        "\t\tlooktocamera 0;",
    ]

    # ---- child gobj ----
    lines += _node_header("\t\t", gobj, "gobj")
    lines += [
        "\t\t\tuid 209 108716040",
        "\t\t\tdwflags 1053761",
        '\t\t\ttech "model"',
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
