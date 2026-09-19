# Security policy

RackTicker is designed for a trusted local network.

- **No login.** The control page and its API have no accounts. Anyone who can reach
  the port can change settings and switch plugins on and off. By default it listens
  only on `127.0.0.1`; `--host 0.0.0.0` opens it to your LAN. Never expose it to
  the internet.
- **Plugins are code.** Installing a plugin runs its Python. Installed plugins run in
  their own process with a clean environment, their own data folder and lower
  priority, which contains crashes, hangs and memory growth, not malice. Install
  plugins from people you trust. On the Pi the service account is confined by
  systemd to its own data directory.
- **Uploads are off by default.** "Allow plugin uploads" lets anyone on the network
  install code with `python -m app.dev push`. Switch it on only while developing.
- **Secrets.** The Home Assistant broker password is stored in the local
  configuration file (mode 0600 on the Pi) and is never sent back to the page.
  Plugin settings are shown on the page, so plugins must not ask for API keys there.
- **The panel companion** runs as root to drive the GPIO and only accepts raw frames
  on a local socket.

Report a vulnerability privately through GitHub's **Security → Report a
vulnerability** flow, with the affected version, reproduction steps and impact.
Please avoid a public issue until a fix is available. Security fixes go to the
latest release.
