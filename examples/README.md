# examples

Two plugins, kept for two different jobs. Both run and render fine
(`python -m app.dev check examples/starter` / `examples/hello-plugin`) — neither is
installed on a rack by default; they exist to be copied or read.

- **starter/** — the template `python -m app.dev new my_plugin` copies to scaffold a new
  plugin. It's a working screen (where the ISS is right now, from wheretheiss.at) chosen
  because it touches every part of the plugin API in one small file: a Provider doing
  network I/O, a Module drawing an animated frame, and settings the web page can edit.
  Its `AGENTS.md` is the instructions an AI coding agent follows while building your new
  plugin in the copied folder.

- **hello-plugin/** — a minimal *installable* plugin (`pyproject.toml`, `LICENSE`,
  package layout) showing what a plugin repo looks like when you publish it on GitHub for
  others to install through the plugin store. Read this one when you're ready to ship,
  not while you're still building.

See `docs/plugins.md` for the full plugin API reference.
