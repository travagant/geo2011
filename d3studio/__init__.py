"""d3studio — our own asset studio for the d3tool pipeline.

Concept image → (3D later) → glTF/GLB → Disciples 3 `.g`/`.scene`/`.ac`/`.a`
through :mod:`d3tool`.  Standard library only, like the rest of the repository:
the studio is a local web app (``python -m d3studio serve``) and nothing leaves
the machine.

Where the ideas come from: the author's AstraForge studio was *read* for
behaviour — what a job is, how a queue shows progress, how a checkpoint is
chosen, how a licence is recorded — but no code, no data format and no naming
is taken from it.  Everything here is written against two public contracts:
the ComfyUI HTTP API (``/prompt``, ``/history``, ``/view``) and our own
:mod:`d3tool` command line.
"""

__version__ = "0.1.0"
