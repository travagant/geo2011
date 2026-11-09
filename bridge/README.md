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
| `ping` | the cheapest answer: build number, folder, port. Touches no config, no studio — this is what the probes use |
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

Transport order — one is not a plan, trycloudflare answers 530 often enough:
cloudflared QUIC, cloudflared HTTP/2, `ssh -p 443 -R0:…` to pinggy,
`ssh -R 80:…` to localhost.run. `ssh` is taken from PATH or from
`C:\Windows\System32\OpenSSH\ssh.exe`, which is where Windows keeps it when it
is not on PATH.

Four failure modes this link was bitten by, and what kills them:

* **`local: not reachable (HTTP 403)`.** `HTTPServer` binds with `SO_REUSEADDR`,
  which on Windows *succeeds* on a port another process is listening on — the
  older listener keeps the traffic. A fresh run then talked to the stale server of
  another unzipped copy (another `.link/token.txt`), got 403, and stopped before
  any tunnel. Ports are now checked by **connecting**, a busy one is skipped, and
  the bind is always verified with `op=ping`; on Windows `allow_reuse_address` is
  off, so a hijack cannot happen in the first place.
* **The address came from a previous run.** `url_from_log` reads the newest URL in
  the tunnel log, and that log survives between runs: until the fresh cloudflared
  printed its own address, the probe was knocking on a dead hostname — Cloudflare
  answers `1033` (`tunnel not found`) or `530`, and every transport looked broken
  while it was fine. The log is truncated before the client starts.
* **The probe used `hello`.** It reads the config and the asset folder; a missing
  key answered 400, and a probe that treated "not our JSON" as "dead tunnel"
  threw away a working one. Probes now use `ping`; `ours` in the probe result says
  whether the token was accepted, so 403 (another copy) and 400 (our server, this
  op failed) are no longer the same thing. `hello` itself tolerates a thin config.
* **"I did not see the fix."** Two unzipped copies, one double-clicked. `BUILD.txt`
  is printed in the banner, written to `last-attempt.txt` (`сборка:` / `папка:`),
  and returned by `op=ping`, so the number and the folder travel with every run;
  a copy sitting *inside* the studio folder is called out as a warning.

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

## CUDA: why the studio says "no kernel image is available"

`CUDA error: no kernel image is available for execution on the device` means the
CUDA binary that was called has no compiled code for the GPU's compute
capability. A GTX 1080 Ti is Pascal, `sm_61`; the card is fine, a wheel was built
without it. Two wheels can be at fault, and they fail at different moments:

| who | why | when it dies |
| --- | --- | --- |
| **torch** | `cu128` wheels ship `sm_70…sm_120`; `cu126` is the last line that still carries Pascal. `torch.cuda.is_available()` keeps saying `True`, so the studio starts and looks healthy | the first tensor on the GPU — the *shape* never appears |
| **Hunyuan3D's `custom_rasterizer_kernel`** | precompiled wheels are built for Ampere and newer | the model is made, the *texture* step dies inside `PyInit_custom_rasterizer_kernel` |

Where that torch comes from is the studio's own installer, not the user:
`installer/Install.ps1` (`InstallComfyDeps`) asks one question — is there a GPU —
and answers with the cu128 index for all of them:

```powershell
$torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { '.../cpu' }
uv pip install --python <venv> torch torchvision torchaudio --index-url $torchIndex
```

so a 1080 Ti gets a torch that cannot run on it. Two details decide where the fix
has to go:

* the studio runs Hunyuan3D in **its own** ComfyUI, `<app>/runtime/ComfyUI`, python
  `runtime/ComfyUI/.venv/Scripts/python.exe` (`server/storage.mjs`: `comfyDir`,
  which `data/config.json` can point elsewhere) — other interpreters are idle;
* that venv is made by `uv venv`, so it has **no pip**; installing goes through
  the studio's `runtime\uv\uv.exe pip install --python <exe>`;
* Windows locks a loaded DLL, so `torch_cuda.dll` cannot be replaced while
  ComfyUI runs: the fix calls the studio's `POST /api/shutdown` (which stops the
  child ComfyUI too) and waits up to 45 s for ports 3210/8190/8191/8192 to close.

`CUDA-check.cmd` walks every interpreter under the studio (the ComfyUI venv
first), and runs a real kernel (`torch.zeros(8, device="cuda") + 1`, then a
matmul) — the arch list alone proves nothing, and `sm_60` kernels legitimately run
on a `sm_61` card, so the verdict is based on the kernel, not on the list. It also
reports whether the rasterizer imports and whether `nvcc` exists. The report lands
next to the script as `cuda-report.txt`.

`CUDA-fix-torch.cmd` installs `torch torchvision torchaudio` from
`https://download.pytorch.org/whl/cu126` through uv, and is built to be honest at
every step: it probes first and leaves healthy environments alone (`--all` to
touch them too, `--yes` to skip the question), tries the newest cu126 build and
then the known-good `torch==2.8.0 / torchvision==0.23.0 / torchaudio==2.8.0` set,
**re-runs the kernel after each attempt**, and only calls it fixed when a kernel
actually executes. It writes `cuda-fix-report.txt` with `fixed:` / `failed:` per
interpreter. Re-running the studio installer with a ComfyUI wipe would put cu128
back; a normal start does not (the installer only installs torch when
`import torch` fails).

If torch is fixed and only texture generation still fails, the rasterizer itself
was built for the wrong architecture: `cuda_check.py fix-rasterizer --python
"<venv>\python.exe"` rebuilds it with `TORCH_CUDA_ARCH_LIST=6.1`, provided the
`setup.py` sources are on disk and CUDA Toolkit 12.x + MSVC are installed. The
studio's own path does not use it — it takes geometry from `AstraForgeMesh`
(Marching Cubes) and paints in Blender (`paint_from_concept`); the rasterizer
belongs to a separately installed `ComfyUI-Hunyuan3DWrapper`.

## The studio's "Connections" window is not the control channel

`provider` is `ollama | openai | anthropic` (`server/schema.mjs`), and the keys
from that window are used by exactly one place — `server/llm.mjs`, the in-studio
chat assistant — which posts them to
`https://api.openai.com/v1/chat/completions` or `api.anthropic.com`. Generation,
checkpoints, ComfyUI and this bridge do not touch them (`server/jobs.mjs` wants an
installed SDXL checkpoint for text prompts, nothing else). Keys are stored with
Windows DPAPI (`server/vault.mjs`) or read from `OPENAI_API_KEY` /
`ANTHROPIC_API_KEY` / `TRIPO_API_KEY`.

So an invented key buys `LLM unavailable (401)`, and a real one buys a paid
account plus "messages and the scene description are sent to the provider". The
right choice on a local machine is the `ollama` provider with the model that is
already installed.

Access for a session comes from the link, and its key is the **token** printed by
`Start studio link.cmd`: 24 random bytes in `bridge/.link/token.txt` (git
ignored), stable across runs — only the tunnel address changes. A user-chosen
token is accepted if it is at least 16 characters; it is weaker than the random
one, so prefer it for convenience only. Everything without that token is 403.

## Blender requirement

The add-on declares `blender: (3, 0, 0)`. Blender 5.0 (`runtime\blender\…`) is
newer than anything this add-on has been tested against; if the import or the
export operator fails there, point `blender` at a 4.2 LTS install in
`config.json` instead of changing the studio's runtime.
