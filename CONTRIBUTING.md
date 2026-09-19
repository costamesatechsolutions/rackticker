# Contributing to RackTicker

RackTicker welcomes focused fixes, new screens, providers, output drivers and
mounting adapters. Open an issue before a large architectural change so the
work can stay compatible with the fixed 128×32 framebuffer and plugin API.

## Development setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -t .
python -m app
```

Keep render functions deterministic and free of network calls. Providers own
external I/O and return normalized snapshots. Outputs receive the canonical
RGB frame and must not mutate it. New screens belong in a plugin, not the core:
start with `python -m app.dev new`, check it with `python -m app.dev check`,
and either keep it in your own repository or add it under `community/` with an
entry in `community/index.json`. See `docs/plugins.md` and `AGENTS.md`.

For enclosure changes, keep every individual STL inside the 180 × 180 mm A1
Mini volume. Commit the parametric builder and generated STL together, document
the verified physical dimensions, and slice the file before submitting it.

Run the complete test suite and `git diff --check` before opening a pull request.
Describe the hardware or scenario used for any claim that depends on a physical
panel, Raspberry Pi, receiver or third-party service.

Contributions are accepted under the repository's AGPL-3.0-only license.
