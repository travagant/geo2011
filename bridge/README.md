# AstraForge → Disciples III bridge

The studio ([AstraForge](https://github.com/Snodrod/AstraForge)) makes a rigged
**GLB** from a picture or a prompt. Disciples III does not read GLB: a unit is
`.g` geometry plus a `.scene` graph, `.ac`/`.a` animation streams and `.t`
textures, and the loader only accepts files an `index` inside a `.vdpack`
describes (`docs/FORMATS.md` §6f, §6g, §6h — a loose file cannot replace one a
pack already carries).

This folder is the missing link: a small CLI plus a link server, not a fork of
the studio. The studio stays stock and can be updated; nothing in
`D:\AstraForge` is patched.

There are two ways to use it, and they share every line of the export path:

- **A session drives your PC.** `Start studio link.cmd` runs `link_up.py` on your
  machine (localhost only), opens a temporary cloudflared tunnel, **proves the
  address answers** (a quick tunnel prints it before it is routable, and on a
  network that drops QUIC it never is), and prints a URL + token. Paste that single line into the chat and the session can list
  assets, start generations, read logs, run the Blender export and pack the mod
  — the studio stays on your GPU, nothing is uploaded anywhere else.
- **You drive it.** `astra_d3.py run` on your machine, same steps, no tunnel.

```
AstraForge        <asset>/exports/*.glb
    │  build      Blender headless + io_scene_d3unit
    ▼
<donor>\          character_x.g  .scene  .ac  .a  .t
    │  pack       d3tool vdpack pack --like … -o Mods/<name>.vdpack
    ▼
Mods\<name>.vdpack   gated by `d3tool vdpack scan --strict`
```

## Files

| file | role |
| --- | --- |
| `astra_d3.py` | the CLI: `doctor`, `list`, `build`, `pack`, `run`, `watch`, `config` |
| `studio_link.py` | the link server the session talks to: `serve`, `selftest`, `token` |
| `link_up.py` | what the launcher runs: server in-process, cloudflared, the probe, the printed line, `last-attempt.txt` |
| `Start studio link.cmd` | the thing to double-click (ASCII wrapper, `pause` on every exit) |
| `build_d3.py` | runs inside Blender (`--python build_d3.py -- …`); imports the GLB, prepares it, calls `export_unit`, writes a JSON report |
| `config.example.json` | every knob; copy to `config.json` (git-ignored) and edit `donor`/`mods` |

The bridge never imports Blender or the add-on: it builds command lines, runs
them, and reads their reports. That is why it is testable without a GPU
(`tests/test_d3tool.py::test_bridge_finds_exports_and_builds_the_right_commands`).

## The link (how a session connects)

```
your PC                                        Arena session
AstraForge  127.0.0.1:3210  ◀──┐
studio_link 127.0.0.1:3211     ├── cloudflared ──▶ https://….trycloudflare.com
                               │   (started here)        GET /cmd?token=…&op=…
```

Every request is a plain GET and every answer is JSON, because that is what a
session can do: it cannot POST, and it cannot open a connection into your
machine. `/cmd?op=api&path=/api/forge/assets/<id>/export&method=POST&body={…}`
is how a POST reaches the studio — the link performs it locally.

| op | what it does |
| --- | --- |
| `hello` | paths, versions, whether the studio answers, newest GLB |
| `doctor` | the health report below (torch arch, Blender, donor, Mods) |
| `list` | GLBs the studio produced, newest first |
| `assets`, `studio_jobs`, `studio_job&id=` | the studio's own API, read side |
| `api&path=/api/…[&method=POST][&body=…]` | any studio endpoint |
| `read&path=…[&max=…]`, `ls&path=…` | files, inside the allowed roots only |
| `write&path=…&b64=…` | small text files (config, notes) |
| `d3tool&args=vdpack scan …` | the toolkit, JSON back |
| `build`, `pack`, `run` | Blender export / pack / both — return a job id |
| `job&id=`, `jobs_local` | what a background job has done so far |

Safety: the server binds `127.0.0.1`, needs the token for every request (403
otherwise), refuses any path outside the repository, the studio folder, the
donor unit and `Mods`, caps reads and writes, and writes an audit line per
request to `bridge/.link/log.txt`. `bridge/.link/` (token, tunnel log, job
records) is git-ignored. Stop the window and the tunnel dies with it.

## Usage

```bat
py bridge\astra_d3.py config            :: write bridge\config.json, then edit it
py bridge\astra_d3.py doctor            :: what is installed, will it run here
py bridge\astra_d3.py list              :: the GLBs the studio produced, newest first
py bridge\astra_d3.py run               :: newest GLB -> .g -> Mods\<name>.vdpack
py bridge\astra_d3.py watch --interval 10
```

`run` is `build` then `pack`; `pack` ends with the toolkit's own verdict and
**refuses to report success** if `vdpack scan` finds a bad hash, so a pack that
the game would reject never gets called done.

### Config

Only three values really need typing; the rest is discovered:

- `donor` — the vanilla unit folder to read `.ac`/`.a`/`.scene` donors from and
  to write the new unit into (e.g. an unpacked
  `resources\characters\empire\inquisitor`). Required for `build` and `pack`.
- `mods` — the game's `Mods` folder. Auto-guessed only when it already holds a
  `.vdpack`.
- `reference_pack` — a pack whose index layout `vdpack pack --like` copies.

`assets`, `blender` and `comfy_python` are derived from `studio`
(`D:\AstraForge`): assets from `data\library\assets`, Blender from
`runtime\blender\**\blender.exe`, the ComfyUI interpreter from
`runtime\comfy\.venv\Scripts\python.exe`.

## What the bridge checks (GTX 1080 Ti, sm_61)

`doctor` runs the ComfyUI interpreter and asks torch for
`torch.cuda.get_arch_list()`. Pascal is **only** in the `cu121`/`cu126` wheels;
a `cu128` build imports fine and then fails at the first kernel with "no kernel
image is available for execution on the device". If `sm_61` is missing the
bridge says so before a single image is generated. It also prints the driver
(`nvidia-smi`), the detected Blender, and whether the donor and `Mods` folders
exist.

What the bridge cannot fix, and does not paper over: 11 GB is enough for SDXL at
832–896 px, not for Pixal3D (RTX 30/40/50 + 10 GB, sm_61 unsupported); use fp16,
`--use-split-cross-attention`, no `xformers`/`flash-attn`/`torch.compile` on
Pascal. Hunyuan3D-2 is under the Tencent Community Licence, which does not apply
in the EU/UK/South Korea — the licence question is the user's, not the bridge's.

## Blender requirement

The add-on declares `blender: (3, 0, 0)`. Blender 5.0 (`runtime\blender\…`) is
newer than anything this add-on has been tested against; if the import or the
export operator fails there, point `blender` at a 4.2 LTS install in
`config.json` instead of changing the studio's runtime.
