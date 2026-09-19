'use strict';
// RackTicker control page. No framework and no build step: a few helpers, the
// live LED view, and one render function per tab.

const $ = (id) => document.getElementById(id);

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), guard(value));
    else if (key in node && key !== 'list') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false)
    node.append(child instanceof Node ? child : document.createTextNode(child));
  return node;
}

function guard(handler) {
  return async (event) => { try { await handler(event); } catch (error) { toast(error.message, true); } };
}

async function api(path, method = 'GET', body, seconds = 8) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), seconds * 1000);
  try {
    const response = await fetch(`/api/${path}`, {method, signal: controller.signal,
      headers: {'Content-Type': 'application/json', 'X-RackTicker': '1'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The device took too long to answer');
    throw error;
  } finally { clearTimeout(timer); }
}

let toastTimer;
function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $('toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.hidden = false;
  toastTimer = setTimeout(() => { node.hidden = true; }, error ? 6000 : 3200);
}

const humanize = (key) => key.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
const clone = (value) => structuredClone(value);

// --- state ---------------------------------------------------------------------

let config, saved, state, catalog, pluginList = {plugins: []}, dirty = false;
let names = {}, expanded = new Set();

function markDirty() {
  dirty = true;
  $('savebar').hidden = false;
}

function clean() {
  dirty = false;
  $('savebar').hidden = true;
}

async function save() {
  // Only settings are checked: the message and install boxes are separate forms.
  for (const input of document.querySelectorAll('#tab-screens input, #tab-settings input, #tab-plugins .plugin input')) {
    if (!input.checkValidity()) {
      showTab(input.closest('[role=tabpanel]').id.slice(4));
      input.reportValidity();
      return;
    }
  }
  config = await api('config', 'PUT', config);
  saved = clone(config);
  clean();
  catalog = await api('catalog');
  renderAll();
  toast('Saved');
}

// --- live LED view -------------------------------------------------------------

const raw = Object.assign(document.createElement('canvas'), {width: 128, height: 32});
const rawContext = raw.getContext('2d');
const rawImage = rawContext.createImageData(128, 32);
const canvas = $('matrix');
const context = canvas.getContext('2d', {alpha: false});
const lit = document.createElement('canvas');
const litContext = lit.getContext('2d');
let pixels = new Uint8Array(128 * 32 * 3), ledScale = 0, dots, board;

function ledLayers(size) {
  const width = 128 * size, height = 32 * size, radius = Math.max(.4, (size - 1) / 2);
  const make = (color) => {
    const layer = Object.assign(document.createElement('canvas'), {width, height});
    const ctx = layer.getContext('2d');
    ctx.fillStyle = color;
    ctx.beginPath();
    for (let y = 0; y < 32; y++) for (let x = 0; x < 128; x++) {
      const cx = x * size + size / 2, cy = y * size + size / 2;
      ctx.moveTo(cx + radius, cy); ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    }
    ctx.fill();
    return layer;
  };
  dots = make('#fff');
  board = make('#141412');
  lit.width = width; lit.height = height;
  ledScale = size;
}

function draw() {
  if (!config) return;
  const sim = config.simulator;
  const room = Math.max(128, $('window').clientWidth);
  const scale = sim.zoom === 'fit' ? Math.max(1, Math.floor(room / 128)) : Number(sim.zoom);
  for (let i = 0, j = 0; i < pixels.length; i += 3, j += 4) {
    rawImage.data[j] = pixels[i]; rawImage.data[j + 1] = pixels[i + 1]; rawImage.data[j + 2] = pixels[i + 2];
    rawImage.data[j + 3] = pixels[i] || pixels[i + 1] || pixels[i + 2] ? 255 : 0;
  }
  rawContext.putImageData(rawImage, 0, 0);
  const width = 128 * scale, height = 32 * scale;
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
  // Brightness is PWM duty; eyes see it roughly on a gamma curve.
  const duty = (state?.brightness ?? config.display.brightness) / 100;
  canvas.style.opacity = duty <= 0 ? .08 : .3 + .7 * Math.pow(duty, 1 / 2.2);
  context.imageSmoothingEnabled = false;
  context.fillStyle = '#0c0c0b'; context.fillRect(0, 0, width, height);
  if (sim.mode === 'clean') {
    context.drawImage(raw, 0, 0, width, height);
  } else {
    if (ledScale !== scale) ledLayers(scale);
    context.drawImage(board, 0, 0);
    litContext.globalCompositeOperation = 'copy';
    litContext.imageSmoothingEnabled = false;
    litContext.drawImage(raw, 0, 0, width, height);
    litContext.globalCompositeOperation = 'destination-in';
    litContext.drawImage(dots, 0, 0);
    litContext.globalCompositeOperation = 'source-over';
    context.drawImage(lit, 0, 0);
  }
  $('scale').textContent = `${scale}× ${sim.mode === 'clean' ? 'pixels' : 'LEDs'}`;
}

let socket, reconnect, leaving = false;
function connect() {
  clearTimeout(reconnect);
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  socket.binaryType = 'arraybuffer';
  socket.onopen = () => { setLink(true); };
  socket.onmessage = (event) => {
    if (typeof event.data === 'string') {
      try { onState(JSON.parse(event.data)); } catch (error) { console.error(error); }
    } else if (event.data.byteLength === 12288) { pixels = new Uint8Array(event.data); draw(); }
  };
  socket.onclose = () => { setLink(false); if (!leaving) reconnect = setTimeout(connect, 1800); };
  socket.onerror = () => socket.close();
}

function setLink(up) {
  $('link').classList.toggle('offline', !up);
  $('link').querySelector('span').textContent = up ? location.host : 'Disconnected';
  $('offline').hidden = up;
}

// --- tabs ----------------------------------------------------------------------

function showTab(name) {
  for (const button of document.querySelectorAll('[data-tab]')) {
    const on = button.dataset.tab === name;
    button.setAttribute('aria-selected', on);
    $(`tab-${button.dataset.tab}`).hidden = !on;
  }
  if (name === 'plugins') loadPlugins();
  if (name === 'settings') renderSoftware();
  history.replaceState(null, '', `#${name}`);
  draw();
}

// --- now -------------------------------------------------------------------------

function onState(next) {
  if (!next.scheduler) return;
  const brightnessChanged = state?.brightness !== next.brightness;
  state = next;
  const s = state.scheduler;
  $('now-name').textContent = names[s.module] || 'Nothing to show';
  $('now-kind').textContent = s.paused ? 'Paused' : s.kind === 'preview' ? 'Showing on request' :
    s.kind === 'interrupt' ? 'Taking over' : 'Playlist';
  $('now-progress').style.width = `${s.duration ? Math.min(100, s.elapsed / s.duration * 100) : 0}%`;
  $('pause').textContent = s.paused ? 'Play' : 'Pause';
  $('back-row').hidden = !(s.kind === 'preview' || s.paused);
  $('back-note').textContent = s.paused ? 'The playlist is paused.' : `Staying on ${names[s.module] || 'this screen'}.`;
  for (const tile of document.querySelectorAll('.tile')) tile.setAttribute('aria-pressed', tile.dataset.module === s.module);
  for (const row of document.querySelectorAll('.screen')) row.classList.toggle('playing', row.dataset.id === s.entry_id);
  $('power').checked = state.power !== false;
  if (document.activeElement !== $('quick-brightness')) {
    $('quick-brightness').value = config.display.brightness;
    $('quick-brightness-value').textContent = state.power === false ? 'Off' : `${config.display.brightness}%`;
  }
  renderHaStatus();
  renderLog();
  if (brightnessChanged) draw();
}

function renderTiles() {
  // Screens on sample data and the plain scoreboard the sportsbook replaces stay
  // out of the way; they remain in Settings → Developer tools.
  const hasBook = catalog.modules.some((item) => item.name === 'sportsbook' && item.available);
  const screens = catalog.modules.filter((item) => item.available && !item.demo && config.modules[item.name]?.enabled
    && !['test_pattern', 'message'].includes(item.name) && !(hasBook && item.name === 'sports'));
  const inPlaylist = new Set(config.playlist.map((entry) => entry.module));
  $('tiles').replaceChildren(...screens.sort((a, b) => inPlaylist.has(b.name) - inPlaylist.has(a.name)).map((item) =>
    h('button', {class: 'tile', 'data-module': item.name, onclick: () => control('preview', {module: item.name})},
      h('b', {text: item.label}), h('span', {text: inPlaylist.has(item.name) ? 'In playlist' : 'Not in playlist'}))));
}

async function control(action, extra = {}) {
  onState(await api('control', 'POST', {action, ...extra}));
}

// --- setting controls ----------------------------------------------------------

function field(label, control, help) {
  return h('div', {class: 'field'}, h('span', {class: 'label', text: label}), control, help ? h('span', {class: 'hint', text: help}) : null);
}

function toggle(label, get, set, help) {
  const input = h('input', {type: 'checkbox', checked: get(), 'aria-label': label,
    onchange: () => { set(input.checked); markDirty(); }});
  const row = h('div', {class: 'line-field'}, h('span', {class: 'label', text: label}), h('span', {class: 'switch'}, input));
  return help ? h('div', {class: 'field'}, row, h('span', {class: 'hint', text: help})) : row;
}

function segmented(options, get, set) {
  const group = h('div', {class: 'segmented'});
  const paint = () => group.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.value === String(get())));
  for (const [value, text] of options) {
    group.append(h('button', {type: 'button', 'data-value': String(value), text,
      onclick: () => { set(value); paint(); markDirty(); }}));
  }
  paint();
  return group;
}

function slider(min, max, step, unit, get, set) {
  const out = h('output');
  const show = () => { out.textContent = `${get()}${unit ? ' ' + unit : ''}`; };
  const input = h('input', {type: 'range', min, max, step, value: get(),
    oninput: () => { set(Number(input.value)); show(); markDirty(); }});
  show();
  return h('div', {class: 'slider'}, input, out);
}

function numberInput(get, set, attrs = {}) {
  const input = h('input', {type: 'number', step: 'any', value: get(), ...attrs,
    oninput: () => { set(input.value === '' ? null : Number(input.value)); markDirty(); }});
  return input;
}

function textInput(get, set, attrs = {}) {
  const input = h('input', {value: get(), maxLength: 1000, ...attrs, oninput: () => { set(input.value); markDirty(); }});
  return input;
}

const splitList = (value) => String(value || '').split(',').map((part) => part.trim()).filter(Boolean);

function tags(get, set, placeholder = 'Add…') {
  const box = h('div', {class: 'field'});
  const paint = () => {
    const chips = splitList(get()).map((item) => h('span', {class: 'chip'}, item,
      h('button', {type: 'button', 'aria-label': `Remove ${item}`, text: '×',
        onclick: () => { set(splitList(get()).filter((other) => other !== item).join(',')); markDirty(); paint(); }})));
    const input = h('input', {placeholder, 'aria-label': placeholder, onkeydown: (event) => {
      if (event.key !== 'Enter' && event.key !== ',') return;
      event.preventDefault();
      const value = input.value.trim().toUpperCase();
      if (value && !splitList(get()).includes(value)) { set([...splitList(get()), value].join(',')); markDirty(); }
      paint();
      box.querySelector('input').focus();
    }});
    box.replaceChildren(h('div', {class: 'chips'}, chips), input);
  };
  paint();
  return box;
}

function multi(options, get, set) {
  const group = h('div', {class: 'chips'});
  for (const option of options) {
    const chip = h('button', {type: 'button', class: 'chip', text: option,
      onclick: () => {
        const now = new Set(splitList(get()));
        now.has(option) ? now.delete(option) : now.add(option);
        set(options.filter((item) => now.has(item)).join(','));
        chip.setAttribute('aria-pressed', now.has(option));
        markDirty();
      }});
    chip.setAttribute('aria-pressed', splitList(get()).includes(option));
    group.append(chip);
  }
  return group;
}

const teamCache = {};
async function teamsFor(league) {
  teamCache[league] ??= api(`teams?league=${encodeURIComponent(league)}`).then((data) => data.teams).catch(() => []);
  return teamCache[league];
}

function teamPicker(get, set, leagues) {
  const box = h('div', {class: 'field'});
  const paint = async () => {
    const lists = await Promise.all(leagues().map(async (league) => (await teamsFor(league)).map((team) => ({...team, league}))));
    const all = lists.flat();
    const known = (abbreviation) => all.filter((team) => team.abbreviation === abbreviation);
    const chips = splitList(get()).map((abbreviation) => {
      const matches = known(abbreviation);
      const text = matches.length ? matches.map((team) => team.short || team.name).join(' / ') : abbreviation;
      return h('span', {class: 'chip', title: matches.map((team) => `${team.name} · ${team.league}`).join('\n')},
        h('span', {class: 'swatch', style: `background:${matches[0]?.color || 'var(--faint)'}`}), text,
        h('button', {type: 'button', text: '×', 'aria-label': `Remove ${text}`,
          onclick: () => { set(splitList(get()).filter((other) => other !== abbreviation).join(',')); markDirty(); paint(); }}));
    });
    const results = h('div', {class: 'results', hidden: true});
    const search = h('input', {placeholder: 'Add a team: Padres, Ducks, Lakers…', 'aria-label': 'Search teams',
      oninput: () => {
        const query = search.value.trim().toLowerCase();
        const hits = query.length < 2 ? [] : all.filter((team) => `${team.name} ${team.abbreviation}`.toLowerCase().includes(query)).slice(0, 12);
        results.replaceChildren(...hits.map((team) => h('button', {type: 'button', onclick: () => {
          if (!splitList(get()).includes(team.abbreviation)) set([...splitList(get()), team.abbreviation].join(','));
          markDirty(); paint();
        }}, h('span', {class: 'chip swatch', style: `background:${team.color};width:12px;height:12px;border-radius:3px;padding:0`}),
          team.name, h('small', {text: `${team.league} · ${team.abbreviation}`}))));
        results.hidden = !hits.length;
      }});
    box.replaceChildren(h('div', {class: 'chips'}, chips.length ? chips : h('span', {class: 'hint', text: 'No teams yet'})),
      h('div', {class: 'picker'}, search, results));
  };
  paint();
  return box;
}

function placePicker(get, set, {allowHome = false} = {}) {
  const box = h('div', {class: 'field'});
  const paint = () => {
    const [latitude, longitude, name] = get();
    const usesHome = allowHome && latitude === 0 && longitude === 0;
    const where = usesHome ? `Your home location${config.location.name ? ` (${config.location.name})` : ''}` :
      latitude === 0 && longitude === 0 ? 'Not set yet' : (name || `${latitude.toFixed(3)}, ${longitude.toFixed(3)}`);
    const results = h('div', {class: 'results', hidden: true});
    let timer;
    const search = h('input', {placeholder: 'Search a city or ZIP code', 'aria-label': 'Search places',
      oninput: () => {
        clearTimeout(timer);
        timer = setTimeout(guard(async () => {
          const query = search.value.trim();
          if (query.length < 2) { results.hidden = true; return; }
          const {places} = await api(`places?q=${encodeURIComponent(query)}`);
          results.replaceChildren(...places.map((place) => h('button', {type: 'button', onclick: () => {
            set(place.latitude, place.longitude, place.name, place.timezone); markDirty(); paint();
          }}, place.name, h('small', {text: `${place.latitude}, ${place.longitude}`}))));
          results.hidden = !places.length;
        }), 300);
      }});
    box.replaceChildren(h('div', {class: 'row'}, h('b', {text: where, class: 'grow'}),
      allowHome && !usesHome ? h('button', {type: 'button', class: 'btn small quiet', text: 'Use home location',
        onclick: () => { set(0, 0, ''); markDirty(); paint(); }}) : null),
    h('div', {class: 'picker'}, search, results));
  };
  paint();
  return box;
}

// Settings for one plugin, from its defaults, choices, help and ui hints.
function pluginFields(plugin) {
  const values = config.plugins[plugin.name];
  if (!values) return null;
  const ui = plugin.ui || {}, choices = plugin.choices || {}, help = plugin.help || {};
  const main = [], advanced = [];
  for (const [key, fallback] of Object.entries(plugin.defaults)) {
    const hint = ui[key] || {};
    if (key === 'longitude' && ui.latitude?.type === 'location') continue;
    const label = hint.label || humanize(key);
    const get = () => values[key], set = (value) => { values[key] = value; };
    let control;
    if (hint.type === 'location' && key === 'latitude') {
      control = field(label, placePicker(() => [values.latitude, values.longitude, ''],
        (lat, lon) => { values.latitude = lat; values.longitude = lon; }, {allowHome: true}), help[key]);
    } else if (choices[key]) {
      const options = choices[key].map((option) => [option, humanize(option)]);
      control = field(label, options.length <= 4 ? segmented(options, get, set) :
        h('select', {onchange: (event) => { set(event.target.value); markDirty(); }},
          ...options.map(([value, text]) => h('option', {value, text, selected: value === get()}))), help[key]);
    } else if (typeof fallback === 'boolean') {
      control = toggle(label, get, set, help[key]);
    } else if (hint.type === 'slider') {
      control = field(label, slider(hint.min ?? 0, hint.max ?? 100, hint.step ?? 1, hint.unit, get, set), help[key]);
    } else if (typeof fallback === 'number') {
      control = field(label, numberInput(get, set), help[key]);
    } else if (hint.type === 'multi') {
      control = field(label, multi(hint.options || [], get, set), help[key]);
    } else if (hint.type === 'tags') {
      control = field(label, tags(get, set), help[key]);
    } else if (hint.type === 'secret') {
      control = field(label, textInput(get, set, {type: 'password', autocomplete: 'off'}), help[key]);
    } else if (hint.type === 'teams') {
      control = field(label, teamPicker(get, set, () => splitList(values[hint.leagues || 'leagues'])), help[key]);
    } else {
      control = field(label, textInput(get, set), help[key]);
    }
    (hint.advanced ? advanced : main).push(control);
  }
  return h('div', {class: 'fields'}, main, advanced.length ?
    h('details', {class: 'advanced'}, h('summary', {text: 'Advanced'}), h('div', {class: 'fields'}, advanced)) : null);
}

// Built-in screens keep their settings in config.modules.
const BUILTIN_FIELDS = {
  clock: (m) => [field('Hours', segmented([['12', '12-hour'], ['24', '24-hour']], () => m.hour_format, (v) => { m.hour_format = v; })),
    field('Style', segmented([['desk', 'Desk neon'], ['classic', 'Classic']], () => m.style, (v) => { m.style = v; })),
    toggle('Show seconds', () => m.show_seconds, (v) => { m.show_seconds = v; })],
  tixclock: (m) => [field('Hours', segmented([['12', '12-hour'], ['24', '24-hour']], () => m.hour_format, (v) => { m.hour_format = v; })),
    field('New pattern every', slider(1, 60, 1, 's', () => m.update_interval, (v) => { m.update_interval = v; })),
    toggle('Show the TIX label', () => m.show_label, (v) => { m.show_label = v; })],
  flight: (m) => [field('Show planes within', slider(.5, 50, .5, 'mi', () => m.max_distance_miles, (v) => { m.max_distance_miles = v; })),
    field('Layout', segmented([['route', 'Route'], ['detail', 'Route + speed'], ['minimal', 'Distance']], () => m.layout, (v) => { m.layout = v; }))],
  message: (m) => [field('Title', textInput(() => m.title, (v) => { m.title = v; }, {maxLength: 32})),
    field('Text', textInput(() => m.text, (v) => { m.text = v; })), toggle('Scroll', () => m.scrolling, (v) => { m.scrolling = v; })],
  system_status: (m) => [field('Temperature', segmented([['F', '°F'], ['C', '°C']], () => m.temperature_unit, (v) => { m.temperature_unit = v; }))],
};
// Screens that draw data supplied by another plugin show that plugin's settings too.
const DATA_FROM = {sportsbook: 'sports'};

function screenSettings(module) {
  const parts = [];
  if (BUILTIN_FIELDS[module]) parts.push(h('div', {class: 'fields'}, BUILTIN_FIELDS[module](config.modules[module])));
  const feed = DATA_FROM[module] || module;
  for (const plugin of catalog.plugins) {
    if (plugin.name !== module && plugin.provider_for !== feed) continue;
    const fields = pluginFields(plugin);
    if (fields) parts.push(h('h3', {text: plugin.name === module ? 'Settings' : `Data · ${plugin.label}`}), fields);
  }
  return parts.length ? parts : [h('p', {class: 'hint', text: 'Nothing to set up for this screen.'})];
}

// --- screens -------------------------------------------------------------------

function renderScreens() {
  const list = config.playlist;
  $('screens-count').textContent = `${list.filter((entry) => entry.enabled).length} of ${list.length} on`;
  $('screens').replaceChildren(...list.map((entry, index) => {
    const open = expanded.has(entry.id);
    const move = (delta) => { [list[index], list[index + delta]] = [list[index + delta], list[index]]; markDirty(); renderScreens(); };
    const available = catalog.modules.find((item) => item.name === entry.module)?.available;
    const enabled = h('input', {type: 'checkbox', checked: entry.enabled, 'aria-label': `${names[entry.module]} on`,
      onchange: () => { entry.enabled = enabled.checked; markDirty(); renderScreens(); }});
    const seconds = h('input', {type: 'number', min: 1, max: 3600, value: entry.duration, 'aria-label': 'Seconds',
      oninput: () => { entry.duration = Number(seconds.value); markDirty(); }});
    return h('div', {class: `screen${entry.enabled ? '' : ' off'}`, 'data-id': entry.id},
      h('div', {class: 'screen-head'},
        h('div', {class: 'order'}, h('button', {type: 'button', text: '▲', disabled: index === 0, 'aria-label': 'Move up', onclick: () => move(-1)}),
          h('button', {type: 'button', text: '▼', disabled: index === list.length - 1, 'aria-label': 'Move down', onclick: () => move(1)})),
        h('span', {class: 'switch'}, enabled),
        h('div', {class: 'screen-name'}, h('b', {text: names[entry.module] || entry.module}),
          h('span', {text: available === false ? 'Not available (plugin off or missing)' : ''})),
        h('label', {class: 'seconds'}, seconds, 's'),
        h('button', {type: 'button', class: 'expand', 'aria-expanded': open, text: open ? 'Close' : 'Settings',
          onclick: () => { open ? expanded.delete(entry.id) : expanded.add(entry.id); renderScreens(); }})),
      open ? h('div', {class: 'screen-body'}, screenSettings(entry.module),
        h('div', {class: 'row'}, h('span', {class: 'grow'}),
          h('button', {type: 'button', class: 'btn small', text: 'Show it now', onclick: () => control('preview', {module: entry.module})}),
          h('button', {type: 'button', class: 'btn small danger', text: 'Remove from playlist', disabled: list.length === 1,
            onclick: () => { list.splice(index, 1); markDirty(); renderScreens(); }}))) : null);
  }));
  const choices = catalog.modules.filter((item) => item.available && !item.demo && item.name !== 'test_pattern');
  $('add-screen').replaceChildren(...choices.map((item) => h('option', {value: item.name, text: item.label})));
  if (state) onState(state);
}

// --- plugins -------------------------------------------------------------------

async function loadPlugins() {
  pluginList = await api('plugins');
  renderPlugins();
  const community = await api('plugins/community').catch(() => ({plugins: []}));
  renderCommunity(community.plugins || []);
}

function pluginStatus(item) {
  const status = item.status || {};
  if (!item.enabled) return h('span', {class: 'pill', text: 'Off'});
  if (status.state === 'failed') return h('span', {class: 'pill bad', text: 'Failed'});
  if (status.state === 'restarting') return h('span', {class: 'pill warn', text: 'Restarting'});
  if (status.state === 'missing') return h('span', {class: 'pill bad', text: 'Missing'});
  return h('span', {class: 'pill ok', text: status.sandboxed ? 'Running · sandboxed' : 'On'});
}

async function pluginAction(id, action) {
  if (dirty) throw new Error('Save or discard your changes first');
  if (action === 'remove' && !confirm('Remove this plugin and its settings?')) return;
  pluginList = await api('plugins', 'POST', {id, action}, 90);
  [config, catalog] = await Promise.all([api('config'), api('catalog')]);
  saved = clone(config);
  renderAll();
  toast({enable: 'Switched on', disable: 'Switched off', remove: 'Removed', update: 'Updated', restart: 'Restarted'}[action]);
}

function pluginCard(item) {
  const status = item.status || {};
  const plugin = catalog.plugins.find((entry) => entry.name === item.id);
  const source = item.source || {};
  const open = expanded.has(`plugin:${item.id}`);
  const onoff = h('input', {type: 'checkbox', checked: item.enabled, 'aria-label': `${item.label} on`,
    onchange: () => pluginAction(item.id, onoff.checked ? 'enable' : 'disable').catch((error) => { onoff.checked = !onoff.checked; throw error; })});
  const meta = [item.version && `v${item.version}`, item.author, source.kind === 'github' ? `${source.owner}/${source.repo}@${(source.commit || '').slice(0, 7)}` :
    source.kind === 'upload' ? 'uploaded' : null, status.render_ms != null ? `${status.render_ms} ms/frame` : null,
  status.memory_mb != null ? `${status.memory_mb} MB` : null].filter(Boolean);
  return h('div', {class: 'plugin'},
    h('div', {class: 'plugin-head'},
      h('div', {class: 'grow'}, h('b', {text: item.label || item.name}), item.description ? h('p', {text: item.description}) : null),
      pluginStatus(item), h('span', {class: 'switch'}, onoff)),
    meta.length ? h('div', {class: 'meta'}, meta.map((text) => h('span', {text}))) : null,
    status.error ? h('div', {class: 'error-text', text: status.error}) : null,
    h('div', {class: 'row'},
      plugin && Object.keys(plugin.defaults).length ? h('button', {class: 'btn small', text: open ? 'Hide settings' : 'Settings',
        onclick: () => { open ? expanded.delete(`plugin:${item.id}`) : expanded.add(`plugin:${item.id}`); renderPlugins(); }}) : null,
      item.homepage ? h('a', {class: 'btn small quiet', href: item.homepage, target: '_blank', rel: 'noopener', text: 'Homepage'}) : null,
      h('span', {class: 'grow'}),
      source.kind === 'github' ? h('button', {class: 'btn small', text: 'Update', onclick: () => pluginAction(item.id, 'update')}) : null,
      status.sandboxed && item.enabled ? h('button', {class: 'btn small', text: 'Restart', onclick: () => pluginAction(item.id, 'restart')}) : null,
      item.kind === 'installed' ? h('button', {class: 'btn small danger', text: 'Remove', onclick: () => pluginAction(item.id, 'remove')}) : null),
    open && plugin ? pluginFields(plugin) : null);
}

function renderPlugins() {
  const items = pluginList.plugins || [];
  const installed = items.filter((item) => item.kind === 'installed');
  $('installed').replaceChildren(...(installed.length ? installed.map(pluginCard) :
    [h('p', {class: 'hint', text: 'Nothing installed yet. Add one from a GitHub link or the community list.'})]));
  $('builtin').replaceChildren(...items.filter((item) => item.kind !== 'installed').map(pluginCard));
  $('uploads').checked = !!config.plugin_uploads;
  $('dev-host').textContent = location.host;
}

function renderCommunity(list) {
  const have = new Set((pluginList.plugins || []).map((item) => item.id));
  $('community-card').hidden = false;
  $('community').replaceChildren(...(list.length ? list.map((entry) => h('div', {class: 'plugin'},
    h('div', {class: 'plugin-head'}, h('div', {class: 'grow'}, h('b', {text: entry.name || entry.id}),
      h('p', {text: entry.description || ''})),
    have.has(entry.id) ? h('span', {class: 'pill ok', text: 'Installed'}) :
      h('button', {class: 'btn small primary', text: 'Install', onclick: () => install(entry.url)})),
    h('div', {class: 'meta'}, entry.author ? h('span', {text: entry.author}) : null))) :
    [h('p', {class: 'hint', text: 'No community plugins listed yet. Yours could be the first: see docs/plugins.md.'})]));
}

async function install(url) {
  if (dirty) throw new Error('Save or discard your changes first');
  $('install-button').disabled = true;
  toast('Installing… this can take half a minute');
  try {
    const result = await api('plugins/install', 'POST', {url}, 120);
    pluginList = result;
    [config, catalog] = await Promise.all([api('config'), api('catalog')]);
    saved = clone(config);
    renderAll();
    await loadPlugins();
    toast(`Installed ${result.installed} and added it to the playlist`);
  } finally { $('install-button').disabled = false; }
}

// --- settings --------------------------------------------------------------------

const TRANSITIONS = [['auto', 'Mix'], ['cut', 'Cut'], ['slide_left', 'Slide'], ['slide_up', 'Roll'], ['wipe', 'Wipe'],
  ['dissolve', 'Dissolve'], ['drop', 'Drop'], ['ticker', 'Ticker']];

function renderSettings() {
  const d = config.display, ha = config.home_assistant;
  $('home-location').replaceChildren(placePicker(() => [config.location.latitude, config.location.longitude, config.location.name],
    (lat, lon, name) => { config.location = {name: name || '', latitude: lat, longitude: lon}; }));
  const bind = (id, get, set, show) => {
    const input = $(id);
    if (input.type === 'checkbox') input.checked = get(); else input.value = get();
    input.oninput = input.onchange = () => { set(input.type === 'checkbox' ? input.checked : input.type === 'number' || input.type === 'range' ? Number(input.value) : input.value); if (show) show(); markDirty(); };
    if (show) show();
  };
  bind('brightness', () => d.brightness, (v) => { d.brightness = v; }, () => { $('brightness-value').textContent = `${d.brightness}%`; });
  bind('night-mode', () => d.night_mode, (v) => { d.night_mode = v; $('night-fields').hidden = !v; });
  $('night-fields').hidden = !d.night_mode;
  bind('night-start', () => d.night_start, (v) => { d.night_start = v; });
  bind('night-end', () => d.night_end, (v) => { d.night_end = v; });
  bind('night-brightness', () => d.night_brightness, (v) => { d.night_brightness = v; }, () => { $('night-brightness-value').textContent = `${d.night_brightness}%`; });
  $('transition').replaceWith(Object.assign(segmented(TRANSITIONS, () => d.transition, (v) => { d.transition = v; }), {id: 'transition'}));
  bind('scroll-speed', () => d.scroll_speed, (v) => { d.scroll_speed = v; });
  bind('transition-seconds', () => d.transition_seconds, (v) => { d.transition_seconds = v; });
  bind('fps', () => d.fps, (v) => { d.fps = v; });
  bind('ha-enabled', () => ha.enabled, (v) => { ha.enabled = v; $('ha-fields').hidden = !v; });
  $('ha-fields').hidden = !ha.enabled;
  bind('ha-host', () => ha.host, (v) => { ha.host = v.trim(); });
  bind('ha-port', () => ha.port, (v) => { ha.port = v; });
  bind('ha-username', () => ha.username, (v) => { ha.username = v; });
  bind('ha-password', () => ha.password, (v) => { ha.password = v; });
  bind('ha-name', () => ha.name, (v) => { ha.name = v; });
  $('sim-mode').replaceWith(Object.assign(segmented([['led', 'LEDs'], ['clean', 'Pixels']], () => config.simulator.mode,
    (v) => { config.simulator.mode = v; draw(); }), {id: 'sim-mode'}));
  bind('sim-zoom', () => config.simulator.zoom, (v) => { config.simulator.zoom = v; draw(); });
  bind('animation-speed', () => config.simulator.animation_speed, (v) => { config.simulator.animation_speed = v; },
    () => { $('animation-speed-value').textContent = `${config.simulator.animation_speed}×`; });
  renderScenarios();
}

// --- software: version, updates from GitHub, factory reset -------------------------

let softwareTimer, softwareFrom;
async function renderSoftware() {
  clearTimeout(softwareTimer);
  let info;
  try { info = await api('software', 'GET', undefined, 12); } catch (error) {
    $('software-version').textContent = 'Could not reach the device';
    softwareTimer = setTimeout(renderSoftware, 5000);  // it is restarting after an update
    return;
  }
  const short = (sha) => (sha || '').slice(0, 7);
  const status = info.status || {};
  const working = info.queued || ['checking', 'installing', 'healing'].includes(status.stage);
  if (softwareFrom && info.revision && info.revision !== softwareFrom && !working) {
    toast('Updated. Reloading…');
    setTimeout(() => location.reload(), 1200);
  }
  $('software-version').textContent = `RackTicker ${info.version}` + (info.revision ? ` (${short(info.revision)})` : '');
  const pill = $('software-status');
  const update = $('software-update');
  update.hidden = !(info.updatable && info.update_available) || working;
  let note = '';
  if (working) {
    pill.className = 'pill'; pill.textContent = 'Updating';
    note = info.queued ? 'Starting the update…' : status.message;
  } else if (status.stage === 'error' && Date.now() / 1000 - status.at < 3600) {
    pill.className = 'pill bad'; pill.textContent = 'Update failed';
    note = status.message;
  } else if (info.update_available) {
    pill.className = 'pill'; pill.textContent = 'Update available';
    note = info.latest ? `New: ${info.latest.message}` : '';
    if (!info.updatable) note += ' · this copy updates with git (it was not installed by the Pi installer)';
  } else if (info.latest) {
    pill.className = 'pill ok'; pill.textContent = 'Up to date';
  } else {
    pill.className = 'pill'; pill.textContent = '';
    note = info.error || '';
  }
  $('software-note').textContent = note;
  if (info.timezone) loadTimezones(info.timezone).catch(() => {});
  renderResetChoices(info.resets);
  update.onclick = guard(async () => {
    if (!confirm('Install the update? The display restarts, and goes back to this version by itself if the new one does not start.')) return;
    softwareFrom = info.revision;
    await api('software/update', 'POST', {commit: info.latest.commit});
    renderSoftware();
  });
  if (working || softwareFrom) softwareTimer = setTimeout(renderSoftware, 3000);
}

async function loadTimezones(current) {
  const select = $('timezone');
  if (select.dataset.loaded) { select.value = current; return; }
  const {zones} = await api('timezones', 'GET', undefined, 12);
  select.replaceChildren(...zones.map((zone) => h('option', {value: zone, text: zone.replaceAll('_', ' ')})));
  select.dataset.loaded = '1';
  select.value = current;
  $('timezone-save').onclick = guard(async () => {
    await api('timezone', 'POST', {timezone: select.value});
    toast('Time zone set. The display restarts in a moment.');
  });
}

async function saveAccess() {
  const password = $('access-password').value;
  const result = await api('access', 'POST', {password});
  $('access-password').value = '';
  toast(result.password_set ? 'Password set. Your browser will ask for it (any user name).' : 'Password removed');
  if (result.password_set) setTimeout(() => location.reload(), 1500);
}

// Each reset goes one step further than the last; the deeper ones need a typed yes.
const RESET_CONFIRM = {
  network: 'Forget every Wi-Fi network? RackTicker drops off this network and opens its own "RackTicker-Setup" network, which you join to point it somewhere else. This page will stop responding.',
  everything: 'Reset everything? Settings, plugins, the control page password and Wi-Fi are all cleared, and RackTicker opens its setup network again.',
  ship: 'Prepare this RackTicker to pass on? It clears everything, forgets what makes this device itself (its name, keys and logs), and powers off. The next time it is switched on it starts as a new device.',
};

function renderResetChoices(choices) {
  const picker = $('reset-scope');
  if (!picker || !choices?.length) return;
  const chosen = picker.value;
  picker.innerHTML = '';
  for (const {scope, what} of choices) {
    const option = document.createElement('option');
    option.value = scope;
    option.textContent = {settings: 'Settings and plugins', network: 'Wi-Fi only',
                          everything: 'Everything', ship: 'Ready to pass on'}[scope] || scope;
    option.title = what;
    picker.append(option);
  }
  picker.value = chosen && choices.some((c) => c.scope === chosen) ? chosen : 'settings';
  const note = () => { $('reset-note').textContent = choices.find((c) => c.scope === picker.value)?.what || ''; };
  picker.onchange = note;
  note();
}

async function restartDisplay() {
  if (!confirm('Restart the display? The panel goes dark for a few seconds and this page reconnects by itself.')) return;
  await api('software/restart', 'POST', {});
  toast('Restarting the display…');
}

async function factoryReset() {
  const scope = $('reset-scope')?.value || 'settings';
  const question = RESET_CONFIRM[scope]
    || 'Reset RackTicker to a fresh install? Settings and the playlist go back to the defaults and installed plugins are removed. A backup of your settings is kept.';
  if (!confirm(question)) return;
  if (scope !== 'settings' && prompt('This cannot be undone. Type RESET to go ahead.') !== 'RESET') return;
  const result = await api('software/reset', 'POST', {scope});
  if (scope === 'settings') {
    toast(`Reset. Your old settings are saved as ${result.backup}. Reloading…`);
    setTimeout(() => location.reload(), 4000);
  } else if (scope === 'ship') {
    toast('Clearing this device and powering off. Wait for the panel to go dark before unplugging it.');
  } else {
    toast('Forgetting Wi-Fi. Join the "RackTicker-Setup" network to set it up again.');
  }
}

function renderHaStatus() {
  const status = state?.home_assistant;
  const pill = $('ha-status');
  pill.className = 'pill ' + (status?.state === 'connected' ? 'ok' : status?.state === 'error' ? 'bad' : '');
  pill.textContent = status?.state === 'connected' ? 'Connected' : status?.state === 'error' ? status.error : 'Off';
}

const SCENARIOS = {
  flight: [['united', 'United jet nearby'], ['southwest', 'Low Southwest jet'], ['high', 'High overhead'], ['none', 'Empty sky']],
  sports: [['pregame', 'Pregame'], ['live', 'Live game'], ['final', 'Final'], ['goal', 'Goal!']],
  system_status: [['normal', 'Normal'], ['ha_offline', 'Home Assistant offline'], ['internet_offline', 'Internet offline'], ['rack_hot', 'Rack hot']],
};

function renderScenarios() {
  const rows = Object.entries(SCENARIOS).filter(([module]) => config.modules[module] &&
    (module === 'system_status' || state?.scenarios?.[module] !== null)).map(([module, options]) => {
    const select = h('select', {'aria-label': `${names[module]} scenario`}, options.map(([value, text]) => h('option', {value, text})));
    const load = async (interrupt) => {
      const result = await api('scenario', 'POST', {module, scenario: select.value, interrupt});
      onState(result.state);
      if (!interrupt) await control('preview', {module});
    };
    return h('div', {class: 'row'}, h('span', {class: 'grow', text: names[module]}), select,
      h('button', {class: 'btn small', text: 'Show', onclick: () => load(false)}),
      h('button', {class: 'btn small quiet', text: 'Take over', onclick: () => load(true)}));
  });
  $('scenarios').replaceChildren(...(rows.length ? rows : [h('span', {class: 'hint', text: 'Every screen is on live data.'})]));
}

function renderLog() {
  if (!state) return;
  const key = JSON.stringify(state.events);
  if ($('log').dataset.key === key) return;
  $('log').dataset.key = key;
  $('log').replaceChildren(...state.events.slice(0, 30).map((event) => h('div', {class: event.level},
    h('time', {text: event.time}), h('span', {text: `${event.source}: ${event.message}`}))));
  $('providers').textContent = Object.entries(state.providers).map(([name, provider]) =>
    `${names[name] || name}: ${provider.error ? 'unavailable' : provider.stale ? 'stale' : provider.has_data ? 'live' : 'waiting'}`).join(' · ');
  $('fault').checked = state.provider_fault;
}

// --- welcome: shown once, until RackTicker knows where it is ----------------------

let welcomePlace = null;

function needsWelcome() {
  return config && !config.location.latitude && !config.location.longitude
    && localStorage.getItem('welcome-done') !== 'yes';
}

function showWelcome(on) {
  $('welcome').hidden = !on;
  document.querySelector('.tabs').hidden = on;
  for (const panel of document.querySelectorAll('main .page')) {
    if (panel.id !== 'welcome') panel.hidden = on || panel.hidden;
  }
  if (!on) return;
  $('welcome-place').replaceChildren(placePicker(
    () => [welcomePlace?.latitude || 0, welcomePlace?.longitude || 0, welcomePlace?.name || ''],
    (latitude, longitude, name, timezone) => {
      welcomePlace = {latitude, longitude, name, timezone};
      $('welcome-save').disabled = false;
      $('welcome-timezone').hidden = !timezone;
      $('welcome-timezone').textContent = timezone ? `The clock will be set to ${timezone}.` : '';
    }));
}

async function finishWelcome(place) {
  if (place) {
    config.location = {name: place.name, latitude: place.latitude, longitude: place.longitude};
    config = await api('config', 'PUT', config);
    saved = clone(config);
    clean();
    // The place search knows its time zone, so the clock is right without being asked.
    if (place.timezone) await api('timezone', 'POST', {timezone: place.timezone}).catch(() => {});
  }
  localStorage.setItem('welcome-done', 'yes');
  showWelcome(false);
  renderAll();
  showTab('now');
  if (place) toast(`RackTicker is set up for ${place.name}.`);
}

// --- wiring ----------------------------------------------------------------------

function renderAll() {
  names = Object.fromEntries(catalog.modules.map((item) => [item.name, item.label]));
  renderTiles();
  renderScreens();
  renderSettings();
  if (!$('tab-plugins').hidden) renderPlugins();
  draw();
}

async function init() {
  [config, state, catalog] = await Promise.all([api('config'), api('state'), api('catalog')]);
  saved = clone(config);
  renderAll();
  onState(state);
  connect();
  new ResizeObserver(draw).observe($('window'));
  for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => showTab(button.dataset.tab));
  showTab(['now', 'screens', 'plugins', 'settings'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'now');
  if (needsWelcome()) showWelcome(true);
  $('welcome-save').onclick = guard(() => finishWelcome(welcomePlace));
  $('welcome-skip').onclick = guard(() => finishWelcome(null));
  $('factory-reset').onclick = guard(factoryReset);
  $('restart-display').onclick = guard(restartDisplay);
  $('access-save').onclick = guard(saveAccess);
  $('pause').onclick = guard(() => control(state?.scheduler.paused ? 'resume' : 'pause'));
  $('next').onclick = guard(() => control('next'));
  $('resume').onclick = guard(() => control('resume'));
  $('power').onchange = guard(() => control('power', {enabled: $('power').checked}));
  $('quick-brightness').oninput = () => { $('quick-brightness-value').textContent = `${$('quick-brightness').value}%`; };
  $('quick-brightness').onchange = guard(async () => {
    const value = Number($('quick-brightness').value);
    await control('brightness', {value});
    config.display.brightness = saved.display.brightness = value;
    renderSettings();
  });
  $('message-form').onsubmit = guard(async (event) => {
    event.preventDefault();
    const result = await api('scenario', 'POST', {module: 'message', interrupt: true,
      message: {title: 'MESSAGE', body: $('message-text').value, scrolling: true}});
    onState(result.state);
    toast(result.interrupt_accepted ? 'On the sign now' : 'Queued: it shows as soon as the current screen finishes');
  });
  $('add-screen-button').onclick = guard(() => {
    if (config.playlist.length >= 32) throw new Error('A playlist holds up to 32 screens');
    const module = $('add-screen').value;
    config.playlist.push({id: `${module}-${Date.now().toString(36)}`, module, duration: 10, enabled: true, mode: 'normal'});
    markDirty(); renderScreens();
  });
  $('install-form').onsubmit = guard(async (event) => { event.preventDefault(); await install($('install-url').value.trim()); $('install-url').value = ''; });
  $('check-updates').onclick = guard(async () => {
    const result = await api('plugins/updates', 'GET', undefined, 30);
    const waiting = Object.entries(result).filter(([, row]) => row.update).map(([name]) => name);
    toast(waiting.length ? `Updates available: ${waiting.join(', ')}` : 'Everything is up to date');
  });
  $('uploads').onchange = guard(async () => {
    if (dirty) { $('uploads').checked = !$('uploads').checked; throw new Error('Save or discard your changes first'); }
    config.plugin_uploads = $('uploads').checked;
    await save();
  });
  $('fault').onchange = guard(() => control('fault', {enabled: $('fault').checked}));
  $('colour-test').onclick = guard(() => control('preview', {module: 'test_pattern'}));
  $('save').onclick = guard(save);
  $('discard').onclick = () => { config = clone(saved); clean(); renderAll(); toast('Changes discarded'); };
}

window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pagehide', () => { leaving = true; socket?.close(); });
init().catch((error) => { toast(`Could not reach RackTicker: ${error.message}`, true); setLink(false); });
