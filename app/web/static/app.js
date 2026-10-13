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
let names = {}, updates = {};

function markDirty() {
  dirty = true;
  paintSaveState();
}

function clean() {
  dirty = false;
  paintSaveState();
}

// One place says whether there is something to save: the sheet's own buttons
// while it is open, the bar along the bottom otherwise.
function paintSaveState() {
  $('savebar').hidden = !dirty || $('sheet').open;
  if ($('sheet').open) paintSheetFoot();
}

async function save() {
  // Only settings are checked: the message and install boxes are separate forms.
  for (const input of document.querySelectorAll('#tab-settings input, #sheet input')) {
    if (!input.checkValidity()) {
      const panel = input.closest('[role=tabpanel]');
      if (panel) showTab(panel.id.slice(4));
      input.closest('details')?.setAttribute('open', '');
      input.reportValidity();
      return false;
    }
  }
  config = await api('config', 'PUT', config);
  saved = clone(config);
  clean();
  catalog = await api('catalog');
  renderAll();
  return true;
}

function discard() {
  config = clone(saved);
  clean();
  renderAll();
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

const TABS = ['display', 'plugins', 'settings'];

function showTab(name) {
  if (!TABS.includes(name)) name = 'display';
  for (const button of document.querySelectorAll('[data-tab]')) {
    const on = button.dataset.tab === name;
    button.setAttribute('aria-selected', on);
    $(`tab-${button.dataset.tab}`).hidden = !on;
  }
  if (name === 'plugins') loadPlugins();
  if (name === 'settings') renderSoftware();
  history.replaceState(null, '', `#${name}`);
  scrollTo(0, 0);
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
    s.kind === 'interrupt' ? 'Taking over' : 'Now showing';
  $('now-settings').hidden = !s.module || s.module === 'test_pattern';
  $('now-progress').style.width = `${s.duration ? Math.min(100, s.elapsed / s.duration * 100) : 0}%`;
  $('pause').textContent = s.paused ? 'Play' : 'Pause';
  $('back-row').hidden = !(s.kind === 'preview' || s.paused);
  $('back-note').textContent = s.paused ? 'Paused on this screen.' : `Staying on ${names[s.module] || 'this screen'}.`;
  for (const row of document.querySelectorAll('#screens .item')) row.classList.toggle('playing', row.dataset.module === s.module);
  $('power').checked = state.power !== false;
  if (document.activeElement !== $('quick-brightness')) {
    $('quick-brightness').value = config.display.brightness;
    $('quick-brightness-value').textContent = state.power === false ? 'Off' : `${config.display.brightness}%`;
  }
  renderHaStatus();
  renderLog();
  if (brightnessChanged) draw();
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
  // The screen's own settings first; a plugin that feeds it data (scores for the
  // sportsbook) under its own heading after.
  const own = [], fed = [];
  if (BUILTIN_FIELDS[module]) own.push(h('div', {class: 'fields'}, BUILTIN_FIELDS[module](config.modules[module])));
  const feed = DATA_FROM[module] || module;
  for (const plugin of catalog.plugins) {
    if (plugin.name !== module && plugin.provider_for !== feed) continue;
    const fields = pluginFields(plugin);
    if (!fields) continue;
    if (plugin.name === module) own.push(fields);
    else fed.push(h('h3', {text: `${plugin.label} (data)`}), fields);
  }
  if (own.length && fed.length) own.unshift(h('h3', {text: 'Settings'}));
  return own.length || fed.length ? [...own, ...fed] : [h('p', {class: 'hint', text: 'Nothing to set up. It just works.'})];
}

// --- on your sign ------------------------------------------------------------------
// The playlist, without having to build one: every screen is a switch. Order and
// time on screen have good defaults and sit behind "Change order and timing".

// How long a screen stays up at least when it is switched on. It stays longer
// while it is mid-story, so these are floors, not cut-offs.
const DEFAULT_SECONDS = {clock: 8, weather: 10, finance: 20, sportsbook: 24, news: 30, tixclock: 15, flight: 12,
  town: 20, sports: 10, message: 10, system_status: 6};
const ABOUT = {clock: 'The time and date', tixclock: 'The time in coloured squares', flight: 'Planes flying overhead',
  sports: 'Live scores for your teams', message: 'A message you write', system_status: 'How the rack is doing'};

const icon = (path) => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.setAttribute('viewBox', '0 0 24 24');
  node.setAttribute('aria-hidden', 'true');
  node.innerHTML = path;
  return node;
};
const CHEVRON = '<path d="M9 5l7 7-7 7"/>';

function about(module) {
  const plugin = (pluginList.plugins || []).find((item) => item.id === module);
  const text = plugin?.description || ABOUT[module] || '';
  // "Pixel Town: a tiny living city" reads as "A tiny living city" under its own name.
  const label = names[module] || '';
  return label && text.toLowerCase().startsWith(`${label.toLowerCase()}: `) ?
    text.slice(label.length + 2).replace(/^./, (c) => c.toUpperCase()) : text;
}

// Where each screen is listed. A plugin says so in its plugin.json, a community one in
// the store's index; the built-in screens are here.
const CATEGORIES = [['clocks', 'Clocks'], ['around', 'Around you'], ['sports', 'Sports'], ['news', 'News and money'],
  ['space', 'Space'], ['games', 'Games'], ['ambient', 'Ambient and art'], ['rack', 'This rack'], ['more', 'More']];
const BUILTIN_CATEGORY = {clock: 'clocks', tixclock: 'clocks', flight: 'around', sports: 'sports', message: 'rack',
  system_status: 'rack'};
let community = {plugins: []};

function categoryOf(id) {
  const known = CATEGORIES.map(([key]) => key);
  const choices = [(pluginList.plugins || []).find((item) => item.id === id)?.category,
    (community.plugins || []).find((entry) => entry.id === id)?.category, BUILTIN_CATEGORY[id]];
  return choices.find((key) => known.includes(key)) || 'more';
}

function grouped(items, id) {
  return CATEGORIES.map(([key, label]) => ({key, label, items: items.filter((item) => categoryOf(id(item)) === key)}))
    .filter((group) => group.items.length);
}

const entriesFor = (module) => config.playlist.filter((entry) => entry.module === module);
const isOn = (module) => entriesFor(module).some((entry) => entry.enabled);

function signScreens() {
  const hasBook = catalog.modules.some((item) => item.name === 'sportsbook' && item.available);
  const order = [...new Set(config.playlist.map((entry) => entry.module))];
  const offered = catalog.modules.filter((item) => item.available && !item.demo && item.name !== 'test_pattern'
    && config.modules[item.name]?.enabled !== false
    // The message screen and the plain scoreboard the sportsbook replaces only
    // show up here if they are already in use.
    && (order.includes(item.name) || !['message', ...(hasBook ? ['sports'] : [])].includes(item.name)));
  const rank = (item) => order.includes(item.name) ? order.indexOf(item.name) : 1000;
  return offered.sort((a, b) => rank(a) - rank(b) || byName(a.label, b.label));
}

function setScreen(module, on) {
  const entries = entriesFor(module);
  if (on && !entries.length) {
    config.playlist.push({id: `${module}-${Date.now().toString(36)}`, module, duration: DEFAULT_SECONDS[module] || 12,
      enabled: true, mode: 'normal'});
  }
  for (const entry of entries) entry.enabled = on;
  if (!config.playlist.some((entry) => entry.enabled)) throw new Error('Keep at least one screen on');
}

function screenRow(item) {
  const on = isOn(item.name);
  const toggle = h('input', {type: 'checkbox', checked: on, 'aria-label': `Show ${item.label}`,
    onchange: async () => {
      // A switch is the whole decision: it saves straight away.
      try { setScreen(item.name, toggle.checked); await save(); toast(toggle.checked ? `${item.label} is on` : `${item.label} is off`); }
      catch (error) { config = clone(saved); renderScreens(); throw error; }
    }});
  const row = h('div', {class: `item${on ? '' : ' off'}`, 'data-module': item.name},
    h('button', {type: 'button', class: 'open', onclick: () => openSheet({type: 'screen', module: item.name})},
      h('span', {class: 'text'}, h('span', {class: 'name'}, item.label, h('span', {class: 'live-tag', text: 'Live'})),
        h('span', {class: 'desc', text: about(item.name)})),
      icon(CHEVRON)),
    h('span', {class: 'switch'}, toggle));
  row.querySelector('.open svg').classList.add('chev');
  return row;
}

async function setGroup(group, on) {
  try {
    for (const item of group.items) if (isOn(item.name) !== on) setScreen(item.name, on);
    await save();
    toast(`${group.label}: ${on ? 'all on' : 'all off'}`);
  } catch (error) { config = clone(saved); renderScreens(); throw error; }
}

function renderScreens() {
  const screens = signScreens();
  $('screens-count').textContent = `${screens.filter((item) => isOn(item.name)).length} of ${screens.length} on`;
  // One group per kind of screen, each with its own all on / all off.
  $('screens').replaceChildren(...grouped(screens, (item) => item.name).map((group) => {
    const count = group.items.filter((item) => isOn(item.name)).length;
    const all = count === group.items.length;
    return h('section', {class: 'group'},
      h('div', {class: 'group-head'}, h('h3', {text: group.label}),
        h('span', {class: 'count', text: `${count} of ${group.items.length}`}), h('span', {class: 'grow'}),
        group.items.length > 1 ? h('button', {type: 'button', class: 'link-btn small', text: all ? 'All off' : 'All on',
          onclick: () => setGroup(group, !all)}) : null),
      h('div', {class: 'list'}, group.items.map(screenRow)));
  }));
  if (state) onState(state);
}

// --- the sheet: settings for one thing, wherever you tapped it -------------------

let sheetView = null;

function openSheet(view) {
  sheetView = view;
  renderSheet();
  if (!$('sheet').open) $('sheet').showModal();
  $('sheet-body').scrollTop = 0;
  paintSaveState();
}

function closeSheet() {
  if ($('sheet').open) $('sheet').close();
}

const SHEETS = {screen: screenSheet, plugin: pluginSheet, order: orderSheet};

function renderSheet() {
  if (!sheetView) return;
  const view = SHEETS[sheetView.type](sheetView);
  if (!view) { closeSheet(); return; }
  $('sheet-title').textContent = view.title;
  $('sheet-sub').textContent = view.sub || '';
  $('sheet-body').replaceChildren(...view.body.flat(Infinity).filter(Boolean));
  sheetView.extra = view.extra || [];
  paintSheetFoot();
}

function paintSheetFoot() {
  const extra = sheetView?.extra || [];
  $('sheet-foot').replaceChildren(...extra, h('span', {class: 'grow'}),
    ...(dirty ? [h('button', {type: 'button', class: 'btn', text: 'Cancel', onclick: () => { discard(); closeSheet(); }}),
      h('button', {type: 'button', class: 'btn primary', text: 'Save', onclick: async () => {
        if (await save()) { closeSheet(); toast('Saved'); }
      }})] : [h('button', {type: 'button', class: 'btn', text: 'Done', onclick: closeSheet})]));
}

function screenSheet({module}) {
  const label = names[module] || module;
  const entries = entriesFor(module);
  const on = isOn(module);
  const toggle = h('input', {type: 'checkbox', checked: on, 'aria-label': `Show ${label}`,
    onchange: () => { try { setScreen(module, toggle.checked); } catch (error) { toggle.checked = !toggle.checked; throw error; }
      markDirty(); renderSheet(); }});
  const timing = entries.map((entry, index) => field(entries.length > 1 ? `Time on screen (${index + 1} of ${entries.length})` : 'Time on screen',
    h('div', {class: 'row'}, h('input', {type: 'number', min: 1, max: 3600, value: entry.duration, required: true, 'aria-label': 'Seconds',
      oninput: (event) => { entry.duration = Number(event.target.value); markDirty(); }}), h('span', {class: 'hint', text: 'seconds at least'}))));
  const shipped = (pluginList.plugins || []).some((item) => item.id === module && item.kind !== 'package');
  return {
    title: label,
    sub: state?.scheduler.module === module ? 'On the panel now' : on ? 'On your sign' : 'Off',
    body: [
      shipped ? preview(`/api/plugins/${encodeURIComponent(module)}/preview`, label) : null,
      about(module) ? h('p', {class: 'about', text: about(module)}) : null,
      h('div', {class: 'line-field'}, h('span', {class: 'label', text: 'Show on my sign'}), h('span', {class: 'switch'}, toggle)),
      screenSettings(module),
      timing.length ? h('details', {class: 'advanced'}, h('summary', {text: 'Timing'}),
        h('div', {class: 'fields'}, timing,
          h('span', {class: 'hint', text: 'A screen that is mid-story (a crawl, a game update) finishes first, so this is a minimum.'}))) : null,
    ],
    extra: [h('button', {type: 'button', class: 'btn', onclick: () => control('preview', {module})}, icon('<path d="M7 5l12 7-12 7z"/>'), 'Show now')],
  };
}

function orderSheet() {
  const list = config.playlist;
  const count = (module) => list.filter((entry) => entry.module === module).length;
  const rows = list.map((entry, index) => {
    const move = (delta) => { [list[index], list[index + delta]] = [list[index + delta], list[index]]; markDirty(); renderSheet(); };
    return h('div', {class: `order-row${entry.enabled ? '' : ' off'}`},
      h('div', {class: 'arrows'},
        h('button', {type: 'button', class: 'btn quiet icon-btn small', 'aria-label': 'Move up', disabled: index === 0, onclick: () => move(-1)},
          icon('<path d="M6 15l6-6 6 6"/>')),
        h('button', {type: 'button', class: 'btn quiet icon-btn small', 'aria-label': 'Move down', disabled: index === list.length - 1,
          onclick: () => move(1)}, icon('<path d="M6 9l6 6 6-6"/>'))),
      h('span', {class: 'name', text: names[entry.module] || entry.module}),
      h('input', {type: 'number', min: 1, max: 3600, value: entry.duration, required: true, 'aria-label': 'Seconds',
        oninput: (event) => { entry.duration = Number(event.target.value); markDirty(); }}),
      h('span', {class: 'hint', text: 's'}),
      count(entry.module) > 1 ? h('button', {type: 'button', class: 'btn quiet icon-btn small', 'aria-label': 'Remove this repeat',
        title: 'Remove this repeat', onclick: () => { list.splice(index, 1); markDirty(); renderSheet(); }},
      icon('<path d="M6 6l12 12M18 6L6 18"/>')) : h('span', {class: 'icon-btn small'}));
  });
  const choices = signScreens();
  const pick = h('select', {'aria-label': 'Screen to repeat', class: 'grow'},
    choices.map((item) => h('option', {value: item.name, text: item.label})));
  return {
    title: 'Order and timing',
    sub: 'Optional. The defaults work fine.',
    body: [
      h('p', {class: 'hint', text: 'Screens play top to bottom, each for at least the seconds shown (longer if it is mid-story). Switched-off screens are crossed out and skipped.'}),
      h('div', {class: 'list'}, rows),
      h('div', {class: 'row'}, pick, h('button', {type: 'button', class: 'btn small', text: 'Play it twice', onclick: () => {
        list.push({id: `${pick.value}-${Date.now().toString(36)}`, module: pick.value, duration: DEFAULT_SECONDS[pick.value] || 12,
          enabled: true, mode: 'normal'});
        markDirty(); renderSheet();
      }})),
      h('span', {class: 'hint', text: 'A screen can appear more than once, the way the clock comes back between the others.'}),
    ],
  };
}

// --- plugins -------------------------------------------------------------------

async function loadPlugins() {
  pluginList = await api('plugins');
  renderPlugins();
  checkUpdates().catch(() => {});
  community = await api('plugins/community').catch(() => community);
  renderCommunity(community.plugins || []);
}

// Asked when the page opens and on the Plugins tab. The device keeps GitHub's
// answer for half an hour; "Check for updates" asks again.
let updatesProblem = '';
async function checkUpdates(fresh = false) {
  const result = await api(`plugins/updates${fresh ? '?fresh=1' : ''}`, 'GET', undefined, 40);
  updates = Object.fromEntries(Object.entries(result).filter(([, row]) => row.update).map(([name]) => [name, true]));
  updatesProblem = Object.values(result).find((row) => row.error)?.error || '';
  renderUpdates();
  return Object.keys(updates).length;
}

function renderUpdates() {
  const waiting = Object.keys(updates);
  const label = (id) => (pluginList.plugins || []).find((item) => item.id === id)?.label || id;
  $('plugins-badge').hidden = !waiting.length;
  $('plugins-badge').textContent = waiting.length;
  $('updates-banner').hidden = !waiting.length;
  $('updates-title').textContent = waiting.length === 1 ? 'An update is ready' : `${waiting.length} updates are ready`;
  $('updates-names').textContent = waiting.map(label).join(', ');
  if (!$('tab-plugins').hidden) renderPlugins();
}

async function updateAll() {
  if (dirty) throw new Error('Save or discard your changes first');
  const waiting = Object.keys(updates);
  const button = $('update-all');
  button.disabled = true;
  button.textContent = 'Updating…';
  toast(waiting.length > 1 ? `Updating ${waiting.length} plugins… this can take a minute` : 'Updating…');
  try {
    const result = await api('plugins/update-all', 'POST', {}, 600);
    pluginList = result;
    for (const id of result.updated) delete updates[id];
    [config, catalog] = await Promise.all([api('config'), api('catalog')]);
    saved = clone(config);
    renderAll();
    renderUpdates();
    const failed = Object.entries(result.failed);
    if (failed.length) {
      const [id, why] = failed[0];
      toast(`${result.updated.length ? `Updated ${result.updated.length}. ` : ''}${label(id)} did not update: ${why}`, true);
    } else {
      toast(result.updated.length === 1 ? 'Updated' : `Updated ${result.updated.length} plugins`);
    }
  } finally {
    button.disabled = false;
    button.textContent = 'Update all';
  }
  function label(id) { return (pluginList.plugins || []).find((item) => item.id === id)?.label || id; }
}

async function pluginAction(id, action) {
  if (dirty) throw new Error('Save or discard your changes first');
  if (action === 'remove' && !confirm('Remove this plugin and its settings?')) return;
  if (action === 'update') toast('Updating…');
  pluginList = await api('plugins', 'POST', {id, action}, 120);
  if (action === 'update' || action === 'remove') delete updates[id];
  [config, catalog] = await Promise.all([api('config'), api('catalog')]);
  saved = clone(config);
  renderAll();
  renderUpdates();
  if (action === 'remove') closeSheet();
  toast({enable: 'Switched on', disable: 'Switched off', remove: 'Removed', update: 'Updated', restart: 'Restarted'}[action]);
}

function pluginProblem(item) {
  const status = item.status || {};
  if (!item.enabled) return null;
  if (status.state === 'failed') return h('span', {class: 'pill bad', text: 'Failed'});
  if (status.state === 'restarting') return h('span', {class: 'pill warn', text: 'Restarting'});
  if (status.state === 'missing') return h('span', {class: 'pill bad', text: 'Missing'});
  return null;
}

const sourceTag = (item) => item.kind !== 'installed' ? null : item.source?.kind === 'upload' ? 'Uploaded' : 'Community';
const byName = (a, b) => a.localeCompare(b, undefined, {sensitivity: 'base'});

function pluginSwitch(item) {
  const input = h('input', {type: 'checkbox', checked: item.enabled, 'aria-label': `${item.label || item.name} on`,
    onchange: () => pluginAction(item.id, input.checked ? 'enable' : 'disable').catch((error) => { input.checked = !input.checked; throw error; })});
  return h('span', {class: 'switch'}, input);
}

function renderPlugins() {
  // One A-Z list of everything on this device; where each came from is a tag, not a section.
  const items = [...(pluginList.plugins || [])].sort((a, b) => byName(a.label || a.name || a.id, b.label || b.name || b.id));
  $('installed-count').textContent = `${items.filter((item) => item.enabled).length} on`;
  $('installed').replaceChildren(...items.map((item) => h('div', {class: `item${item.enabled ? '' : ' off'}`},
    h('button', {type: 'button', class: 'open', onclick: () => openSheet({type: 'plugin', id: item.id})},
      h('span', {class: 'text'},
        h('span', {class: 'name'}, item.label || item.name, sourceTag(item) ? h('span', {class: 'tag', text: sourceTag(item)}) : null),
        h('span', {class: 'desc', text: item.description || ''})),
      icon(CHEVRON)),
    pluginProblem(item),
    updates[item.id] ? h('button', {type: 'button', class: 'btn small primary', text: 'Update', onclick: () => pluginAction(item.id, 'update')}) : null,
    pluginSwitch(item))));
  for (const svg of $('installed').querySelectorAll('.open svg')) svg.classList.add('chev');
}

function pluginSheet({id}) {
  const item = (pluginList.plugins || []).find((entry) => entry.id === id);
  if (!item) return null;
  const status = item.status || {};
  const source = item.source || {};
  const plugin = catalog.plugins.find((entry) => entry.name === id);
  const meta = [item.author, source.kind === 'github' ? `${source.owner}/${source.repo}@${(source.commit || '').slice(0, 7)}` : null,
    status.render_ms != null ? `${status.render_ms} ms a frame` : null, status.memory_mb != null ? `${status.memory_mb} MB` : null].filter(Boolean);
  let settings = null;
  if (item.enabled && plugin && Object.keys(plugin.defaults).length) {
    settings = item.screen ? screenSettings(id) : pluginFields(plugin);
  }
  return {
    title: item.label || item.name,
    sub: [sourceTag(item) || 'Built in', item.version && `v${item.version}`, item.screen ? 'Screen' : 'Data source'].filter(Boolean).join(' · '),
    body: [
      item.kind !== 'package' ? preview(`/api/plugins/${encodeURIComponent(id)}/preview`, item.label || item.name) : null,
      item.description ? h('p', {class: 'about', text: item.description}) : null,
      h('div', {class: 'line-field'}, h('span', {class: 'label', text: 'On'}), pluginSwitch(item)),
      status.error ? h('div', {class: 'error-text', text: status.error}) : null,
      updates[id] ? h('div', {class: 'banner'}, h('div', {class: 'grow'}, h('b', {text: 'An update is ready'})),
        h('button', {type: 'button', class: 'btn primary small', text: 'Update', onclick: () => pluginAction(id, 'update')})) : null,
      settings ? [h('h3', {text: 'Settings'}), settings] : item.enabled ? null : h('p', {class: 'hint', text: 'Switch it on to set it up.'}),
      meta.length ? h('div', {class: 'meta'}, meta.map((text) => h('span', {text}))) : null,
    ],
    extra: [
      item.homepage ? h('a', {class: 'btn small quiet', href: item.homepage, target: '_blank', rel: 'noopener', text: 'Homepage'}) : null,
      status.sandboxed && item.enabled ? h('button', {type: 'button', class: 'btn small quiet', text: 'Restart', onclick: () => pluginAction(id, 'restart')}) : null,
      item.kind === 'installed' ? h('button', {type: 'button', class: 'btn small quiet danger', text: 'Remove', onclick: () => pluginAction(id, 'remove')}) : null,
    ].filter(Boolean),
  };
}

function preview(src, label) {
  // The plugin playing in its own animation, on a strip of panel black.
  if (!src) return null;
  const img = h('img', {src, alt: `${label} on the panel`, loading: 'lazy', decoding: 'async'});
  img.addEventListener('error', () => img.parentElement?.remove());
  return h('div', {class: 'shot'}, img);
}

function renderCommunity(list) {
  // Only what is not here yet: installed ones are already in the list above.
  const have = new Set((pluginList.plugins || []).map((item) => item.id));
  const more = list.filter((entry) => !have.has(entry.id)).sort((a, b) => byName(a.name || a.id, b.name || b.id));
  if (!more.length) {
    $('community').replaceChildren(h('p', {class: 'hint', text: list.length ? 'You have every community plugin.' :
      'No community plugins listed yet. Yours could be the first: see docs/plugins.md.'}));
    return;
  }
  $('community').replaceChildren(...grouped(more, (entry) => entry.id).map((group) => h('section', {class: 'group'},
    h('div', {class: 'group-head'}, h('h3', {text: group.label}), h('span', {class: 'count', text: String(group.items.length)})),
    h('div', {class: 'store'}, group.items.map((entry) => h('div', {class: 'offer'},
      preview(entry.preview, entry.name || entry.id),
      h('b', {text: entry.name || entry.id}),
      h('p', {text: entry.description || ''}),
      h('div', {class: 'row'}, h('span', {class: 'hint grow', text: entry.author || ''}),
        h('button', {type: 'button', class: 'btn small primary', text: 'Install', onclick: (event) => install(entry.url, event.currentTarget)}))))))));
}

async function install(url, button) {
  if (dirty) throw new Error('Save or discard your changes first');
  for (const node of [$('install-button'), button]) if (node) node.disabled = true;
  if (button) button.textContent = 'Installing…';
  toast('Installing… this can take half a minute');
  try {
    const result = await api('plugins/install', 'POST', {url}, 120);
    pluginList = result;
    [config, catalog] = await Promise.all([api('config'), api('catalog')]);
    saved = clone(config);
    renderAll();
    await loadPlugins();
    toast(`Installed ${names[result.installed] || result.installed}. It is on your sign now.`);
  } finally {
    $('install-button').disabled = false;
    if (button?.isConnected) { button.disabled = false; button.textContent = 'Install'; }
  }
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
  $('uploads').checked = !!config.plugin_uploads;
  $('dev-host').textContent = location.host;
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
  $('settings-badge').hidden = update.hidden;
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
  showTab('display');
  if (place) toast(`RackTicker is set up for ${place.name}.`);
}

// --- wiring ----------------------------------------------------------------------

function renderAll() {
  names = Object.fromEntries(catalog.modules.map((item) => [item.name, item.label]));
  renderScreens();
  renderSettings();
  if (!$('tab-plugins').hidden) renderPlugins();
  if ($('sheet').open) renderSheet();
  draw();
}

async function init() {
  [config, state, catalog, pluginList] = await Promise.all([api('config'), api('state'), api('catalog'),
    api('plugins').catch(() => ({plugins: []}))]);
  saved = clone(config);
  renderAll();
  onState(state);
  connect();
  new ResizeObserver(draw).observe($('window'));
  for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => showTab(button.dataset.tab));
  // Old links (#now, #screens) land on Display.
  showTab(location.hash.slice(1));
  if (needsWelcome()) showWelcome(true);
  // Updates show up by themselves: a number on Plugins, a dot on Settings.
  checkUpdates().catch(() => {});
  api('plugins/community', 'GET', undefined, 12).then((index) => { community = index; renderScreens(); }).catch(() => {});
  api('software', 'GET', undefined, 12).then((info) => {
    $('settings-badge').hidden = !(info.updatable && info.update_available);
  }).catch(() => {});

  $('welcome-save').onclick = guard(() => finishWelcome(welcomePlace));
  $('welcome-skip').onclick = guard(() => finishWelcome(null));
  $('factory-reset').onclick = guard(factoryReset);
  $('restart-display').onclick = guard(restartDisplay);
  $('access-save').onclick = guard(saveAccess);
  $('pause').onclick = guard(() => control(state?.scheduler.paused ? 'resume' : 'pause'));
  $('next').onclick = guard(() => control('next'));
  $('resume').onclick = guard(() => control('resume'));
  $('now-settings').onclick = () => { if (state?.scheduler.module) openSheet({type: 'screen', module: state.scheduler.module}); };
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
    $('message-text').value = '';
    toast(result.interrupt_accepted ? 'On the sign now' : 'Queued: it shows as soon as the current screen finishes');
  });
  $('order-timing').onclick = () => openSheet({type: 'order'});
  $('more-screens').onclick = () => { showTab('plugins'); setTimeout(() => $('community').scrollIntoView({behavior: 'smooth'}), 50); };
  $('sheet-close').onclick = closeSheet;
  // Closing the sheet keeps what was changed in it; the bar along the bottom then offers to save it.
  $('sheet').addEventListener('close', () => { sheetView = null; paintSaveState(); });
  $('sheet').addEventListener('click', (event) => { if (event.target === $('sheet')) closeSheet(); });
  $('install-form').onsubmit = guard(async (event) => { event.preventDefault(); await install($('install-url').value.trim()); $('install-url').value = ''; });
  $('update-all').onclick = guard(updateAll);
  $('check-updates').onclick = guard(async () => {
    $('check-updates').disabled = true;
    try {
      const count = await checkUpdates(true);
      if (updatesProblem && !count) throw new Error(updatesProblem);
      toast(count ? `${count} update${count === 1 ? '' : 's'} ready` : 'Everything is up to date');
    } finally { $('check-updates').disabled = false; }
  });
  $('uploads').onchange = guard(async () => {
    if (dirty) { $('uploads').checked = !$('uploads').checked; throw new Error('Save or discard your changes first'); }
    config.plugin_uploads = $('uploads').checked;
    await save();
  });
  $('fault').onchange = guard(() => control('fault', {enabled: $('fault').checked}));
  $('colour-test').onclick = guard(() => control('preview', {module: 'test_pattern'}));
  $('save').onclick = guard(async () => { if (await save()) toast('Saved'); });
  $('discard').onclick = () => { discard(); toast('Changes discarded'); };
}

window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pagehide', () => { leaving = true; socket?.close(); });
init().catch((error) => { toast(`Could not reach RackTicker: ${error.message}`, true); setLink(false); });
