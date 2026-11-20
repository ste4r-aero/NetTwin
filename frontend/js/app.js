/* ============================================================
   NetTwin — фронтенд (vanilla JS + Cytoscape.js)
   ============================================================ */
'use strict';

/* ---------------- Утилиты ---------------- */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (init.body && typeof init.body !== 'string') {
    init.body = JSON.stringify(init.body);
    init.headers['Content-Type'] = 'application/json';
  }
  const r = await fetch('/api' + path, init);
  if (!r.ok) {
    let detail = '';
    try { const j = await r.json(); detail = j.detail || ''; } catch (e) { /* пусто */ }
    throw new Error(detail || ('HTTP ' + r.status));
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : null;
}

function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function fmtDT(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  });
}
function ago(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return s + ' с назад';
  if (s < 3600) return Math.floor(s / 60) + ' мин назад';
  if (s < 86400) return Math.floor(s / 3600) + ' ч назад';
  return Math.floor(s / 86400) + ' дн назад';
}

/* ---------------- Тосты ---------------- */
function toast(msg, type = 'ok', ms = 4200) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  const ico = { ok: '✅', warn: '⚠️', err: '⛔', info: 'ℹ️' }[type] || 'ℹ️';
  el.innerHTML = '<span class="t-ico">' + ico + '</span><span>' + esc(msg) + '</span>';
  box.appendChild(el);
  setTimeout(() => { el.classList.add('hide'); setTimeout(() => el.remove(), 350); }, ms);
}

/* ---------------- Модальные окна ---------------- */
let modalResolve = null;

function openModal(title, bodyHtml, okText = 'ОК', okClass = 'primary') {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHtml;
  const ok = $('#modal-ok');
  ok.textContent = okText;
  ok.className = 'btn ' + okClass;
  $('#modal').hidden = false;
  const first = $('#modal-body input, #modal-body select, #modal-body textarea');
  if (first) setTimeout(() => first.focus(), 50);
  return new Promise((res) => { modalResolve = res; });
}
function closeModal(val) {
  $('#modal').hidden = true;
  if (modalResolve) { modalResolve(val); modalResolve = null; }
}
$('#modal-ok').addEventListener('click', () => {
  const fields = {};
  $$('#modal-body [data-field]').forEach((i) => { fields[i.dataset.field] = i.value; });
  closeModal({ ok: true, fields });
});
$('#modal-cancel').addEventListener('click', () => closeModal({ ok: false, fields: {} }));
$('#modal').addEventListener('click', (e) => { if (e.target === $('#modal')) closeModal({ ok: false, fields: {} }); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#modal').hidden) closeModal({ ok: false, fields: {} });
  if (e.key === 'Enter' && !$('#modal').hidden && e.target.tagName !== 'TEXTAREA') $('#modal-ok').click();
});

function confirmModal(title, text, danger = true) {
  return openModal(title, '<p class="muted" style="font-size:13.5px;line-height:1.6">' + esc(text) + '</p>',
    danger ? 'Удалить' : 'ОК', danger ? 'danger' : 'primary');
}
async function promptModal(title, label, value = '', placeholder = '') {
  const body = '<label class="field"><span>' + esc(label) + '</span>' +
    '<input type="text" data-field="value" value="' + esc(value) + '" placeholder="' + esc(placeholder) + '"></label>';
  const r = await openModal(title, body, 'Сохранить');
  return r.ok ? (r.fields.value || '').trim() : null;
}

/* ---------------- Состояние ---------------- */
const state = {
  interfaces: [],
  stats: {},
  hosts: [],
  nodes: [], edges: [], rooms: [],
  session: null,
  hostFilter: '',
  hostType: '',
  wsIfOk: false,
  wsEvOk: false,
};

/* ---------------- Вкладки ---------------- */
$('#tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  $$('.tab').forEach((t) => t.classList.toggle('active', t === btn));
  $$('.pane').forEach((p) => p.classList.toggle('active', p.id === 'pane-' + btn.dataset.tab));
  if (btn.dataset.tab === 'map') initMapWhenReady();
});

/* ============================================================
   ВКЛАДКА 1 — Живой тестер кабеля
   ============================================================ */
function ifaceState(i) {
  if (!i.link) return 'bad';
  if (i.speed_mbps >= 1000) return 'good';
  if (i.speed_mbps > 0) return 'warn';
  return 'bad';
}

const HINTS = {
  good: 'Отлично: линк на гигабите — все 8 жил кабеля целы.',
  warn: 'Линк есть, но скорость ниже гигабита. Проверьте обжим всех 8 жил кабеля (или порт свитча).',
  bad: 'Линка нет: кабель не вставлен, повреждён, перепутаны пары или порт свитча выключен.'
};

function renderInterfaceCards() {
  const box = $('#iface-cards');
  if (!state.interfaces.length) {
    box.innerHTML = '<div class="empty-state glass"><div class="empty-ico">🔌</div>' +
      '<div>Проводные адаптеры <b>en*</b> не найдены.</div>' +
      '<div class="muted small" style="margin-top:6px">Воткните USB-Ethernet адаптер — карточка появится сама.</div></div>';
    return;
  }
  box.innerHTML = state.interfaces.map((i) => {
    const st = ifaceState(i);
    const ip = i.ipv4 && i.ipv4.length
      ? i.ipv4.map((a) => esc(a.addr) + '/' + a.prefix).join(', ')
      : '<span class="muted">нет (DHCP не выдал?)</span>';
    const dns = (i.dns || []).length ? i.dns.map(esc).join(', ') : '—';
    const lldp = i.lldp
      ? '<div class="lldp-box">🛰 <b>LLDP-сосед</b> (' + esc(i.lldp.proto || 'lldp') + ', ' +
        Math.max(0, i.lldp.age_sec) + ' с назад)<br>' +
        'Устройство: <b>' + esc(i.lldp.sysname || i.lldp.chassis || '—') + '</b><br>' +
        'Порт: ' + esc(i.lldp.port || '—') +
        (i.lldp.portdesc ? ' (' + esc(i.lldp.portdesc) + ')' : '') +
        (i.lldp.mgmt ? '<br>Управление: ' + esc(i.lldp.mgmt) : '') + '</div>'
      : '';
    const speedText = i.link
      ? (i.speed_mbps + ' Мбит/с')
      : 'Нет линка';
    return '<div class="iface-card glass state-' + st + '" data-iface="' + esc(i.name) + '">' +
      '<div class="card-top">' +
        '<div><h3>' + esc(i.name) + '</h3><div class="usb-chip">' +
          (i.usb ? esc(i.usb) : 'встроенный адаптер') + (i.mac ? ' · ' + esc(i.mac) : '') + '</div></div>' +
        '<span class="state-badge">' + (st === 'good' ? 'гигабит' : st === 'warn' ? 'внимание' : 'нет линка') + '</span>' +
      '</div>' +
      '<div class="speed-big">' + speedText + '</div>' +
      '<div class="speed-sub">' + (i.link ? 'Дуплекс: ' + esc(i.duplex || '—') : 'Кабель не подключён') + '</div>' +
      '<svg class="cable-svg" viewBox="0 0 340 46" preserveAspectRatio="none">' +
        '<path class="cable-path" d="M10,23 C70,10 120,36 170,23 C220,10 270,36 330,23"/>' +
        '<path class="flow-dash" d="M10,23 C70,10 120,36 170,23 C220,10 270,36 330,23"/>' +
      '</svg>' +
      '<div class="info-rows">' +
        '<div class="info-row"><span class="k">IP / маска</span><span class="v">' + ip + '</span></div>' +
        '<div class="info-row"><span class="k">Шлюз</span><span class="v">' + esc(i.gateway || '—') + '</span></div>' +
        '<div class="info-row"><span class="k">DNS</span><span class="v">' + dns + '</span></div>' +
        '<div class="info-row"><span class="k">Состояние</span><span class="v">' + esc(i.operstate || '—') + '</span></div>' +
      '</div>' +
      lldp +
      '<div class="hint-note">' + HINTS[st] + '</div>' +
      '<div class="card-actions">' +
        '<button class="btn small-save" data-save="' + esc(i.name) + '">💾 Сохранить замер</button>' +
        '<span class="activity">трафик: <b>↓' + i.rx_kbps + '</b>/<b>↑' + i.tx_kbps + '</b> Кбит/с</span>' +
      '</div>' +
    '</div>';
  }).join('');

  // обновляем список адаптеров в панели сессий (только если он реально изменился)
  const sel = $('#session-iface');
  const sig = state.interfaces.map((i) => i.name + (i.link ? '1' : '0')).join(',');
  if (sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    const prev = sel.value;
    sel.innerHTML = '<option value="">Все адаптеры</option>' +
      state.interfaces.map((i) =>
        '<option value="' + esc(i.name) + '">' + esc(i.name) +
        (i.link ? ' (линк ' + i.speed_mbps + 'M)' : ' (без линка)') + '</option>').join('');
    if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
}

$('#iface-cards').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-save]');
  if (!btn) return;
  const name = btn.dataset.save;
  const i = state.interfaces.find((x) => x.name === name);
  if (!i) return;
  const note = await promptModal('Сохранить замер', 'Заметка к замеру (номер кабеля, порт, место):',
    '', 'например: кабель №14, патч-панель 2');
  if (note === null) return;
  try {
    await api('/measurements', {
      method: 'POST',
      body: {
        iface: i.name,
        speed: i.link ? (i.speed_mbps + ' Мбит/с') : 'нет линка',
        duplex: i.duplex || '',
        ip: (i.ipv4 || []).map((a) => a.addr + '/' + a.prefix).join(', '),
        gw: i.gateway || '',
        dns: (i.dns || []).join(', '),
        note
      }
    });
    toast('Замер по «' + i.name + '» сохранён');
    loadMeasurements();
  } catch (err) { toast(err.message, 'err'); }
});

async function loadMeasurements() {
  try {
    const d = await api('/measurements');
    const tb = $('#measurements-table tbody');
    $('#measurements-count').textContent = d.items.length ? ('всего: ' + d.items.length) : '';
    if (!d.items.length) {
      tb.innerHTML = '<tr><td colspan="8" class="muted empty-cell">Замеров пока нет — нажмите «Сохранить замер» на карточке адаптера.</td></tr>';
      return;
    }
    tb.innerHTML = d.items.map((m) =>
      '<tr><td>' + fmtDT(m.ts) + '</td><td class="mono">' + esc(m.iface) + '</td>' +
      '<td>' + esc(m.speed) + '</td><td>' + esc(m.duplex || '—') + '</td>' +
      '<td class="mono">' + esc(m.ip || '—') + '</td><td class="mono">' + esc(m.gw || '—') + '</td>' +
      '<td>' + esc(m.note || '') + '</td>' +
      '<td><button class="icon-btn" data-del-measure="' + m.id + '" title="Удалить">🗑</button></td></tr>'
    ).join('');
  } catch (e) { /* тихо */ }
}

$('#measurements-table').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-del-measure]');
  if (!btn) return;
  if (!(await confirmModal('Удалить замер?', 'Запись будет удалена безвозвратно.')).ok) return;
  try {
    await api('/measurements/' + btn.dataset.delMeasure, { method: 'DELETE' });
    loadMeasurements();
  } catch (err) { toast(err.message, 'err'); }
});

/* ============================================================
   ВКЛАДКА 2 — Карта сети (Cytoscape)
   ============================================================ */
const KIND_ICON = { switch: '🔌', pc: '💻', printer: '🖨️', network: '🌐', ap: '📶', camera: '📷', phone: '☎️', unknown: '❓' };
const KIND_COLOR = { switch: '#c47f17', pc: '#30d158', printer: '#bf5af2', network: '#ff9f0a', ap: '#0a84ff', camera: '#64d2ff', phone: '#32ade6', unknown: '#7d8a9c' };
const KIND_RU = { switch: 'Свитч / точка', pc: 'ПК', printer: 'Принтер / МФУ', network: 'Сетевое оборудование', ap: 'Точка доступа', camera: 'Камера', phone: 'Телефон', unknown: 'Неизвестно' };
const EDGE_COLORS = [['', 'По типу'], ['#6b7686', 'Серый'], ['#30d158', 'Зелёный (исправен)'], ['#ffd60a', 'Жёлтый (плохой обжим)'], ['#ff453a', 'Красный (авария)'], ['#0a84ff', 'Синий'], ['#bf5af2', 'Фиолетовый']];

let cy = null;
let mapReady = false;
let connectMode = false;
let connectFrom = null;
const posTimers = {};

function nodeElData(n) {
  if (n.kind === 'room') {
    return {
      id: 'r' + n.id, label: '🏠 ' + (n.label || n.name || 'Комната'),
      kind: 'room', color: n.color || 'rgba(255,255,255,0.5)',
      w: n.w || 460, h: n.h || 340, raw: Object.assign({}, n, { kind: 'room' })
    };
  }
  return {
    id: 'n' + n.id,
    label: (KIND_ICON[n.kind] || '❓') + ' ' + (n.label || '—'),
    kind: n.kind, color: n.color || KIND_COLOR[n.kind] || KIND_COLOR.unknown, raw: n
  };
}

function buildElements() {
  const els = [];
  state.rooms.forEach((r) => els.push({ group: 'nodes', data: nodeElData(r), position: { x: r.x, y: r.y }, classes: 'room', locked: false }));
  state.nodes.forEach((n) => els.push({ group: 'nodes', data: nodeElData(n), position: { x: n.x, y: n.y } }));
  state.edges.forEach((e) => els.push({
    group: 'edges',
    data: { id: 'e' + e.id, source: 'n' + e.src, target: 'n' + e.dst, label: e.label || '', color: e.color || '#6b7686', raw: e }
  }));
  return els;
}

const CY_STYLE = [
  {
    selector: 'node',
    style: {
      'label': 'data(label)', 'color': '#e8eef7', 'font-family': '-apple-system, Segoe UI, Roboto, sans-serif',
      'font-size': 11, 'font-weight': 600, 'text-valign': 'bottom', 'text-margin-y': 7,
      'text-wrap': 'wrap', 'text-max-width': 110,
      'width': 42, 'height': 42, 'border-width': 2, 'border-color': 'rgba(255,255,255,0.25)',
      'background-color': 'data(color)', 'z-index': 10
    }
  },
  {
    selector: 'node[kind = "switch"]',
    style: { 'shape': 'round-rectangle', 'width': 54, 'height': 42, 'font-size': 12 }
  },
  {
    selector: 'node.room',
    style: {
      'shape': 'round-rectangle', 'background-opacity': 0.05,
      'border-width': 2, 'border-style': 'dashed', 'border-color': 'data(color)',
      'width': 'data(w)', 'height': 'data(h)', 'font-size': 13, 'color': '#9fb0c8',
      'text-valign': 'top', 'text-margin-y': 10, 'z-index': 0, 'z-order': 0
    }
  },
  {
    selector: 'edge',
    style: {
      'width': 2.5, 'line-color': 'data(color)', 'curve-style': 'bezier',
      'label': 'data(label)', 'font-size': 10, 'color': '#aab6c9',
      'text-background-color': '#0d131e', 'text-background-opacity': 0.85,
      'text-background-padding': 3, 'text-background-shape': 'roundrectangle',
      'target-arrow-shape': 'none'
    }
  },
  { selector: 'node:selected', style: { 'border-color': '#0a84ff', 'border-width': 4 } },
  { selector: 'edge:selected', style: { 'line-color': '#0a84ff', 'width': 4 } },
];

async function loadMap() {
  const d = await api('/map');
  state.nodes = d.nodes;
  state.edges = d.edges;
  state.rooms = d.rooms;
  return d;
}

function mapNeedsLayout() {
  const real = state.nodes.filter((n) => n.x === 0 && n.y === 0);
  return real.length > 0 && state.nodes.length > 1;
}

function initMapWhenReady() {
  if (mapReady) { if (cy) cy.resize(); return; }
  if (typeof window.cytoscape !== 'function') {
    // Cytoscape ещё не загрузился (например, подтягивается с CDN) — повторим через секунду
    setTimeout(initMapWhenReady, 1000);
    return;
  }
  loadMap().then(() => {
    cy = window.cytoscape({
      container: $('#cy'),
      elements: buildElements(),
      style: CY_STYLE,
      layout: mapNeedsLayout() ? { name: 'grid', rows: Math.max(2, Math.round(Math.sqrt(state.nodes.length + 1))) } : { name: 'preset' },
      wheelSensitivity: 0.25,
      minZoom: 0.15, maxZoom: 2.5
    });
    mapReady = true;
    bindMapEvents();
    if (mapNeedsLayout()) setTimeout(() => saveAllPositions(), 800);
    setTimeout(() => cy.fit(undefined, 60), 400);
  }).catch((e) => toast('Не удалось загрузить карту: ' + e.message, 'err'));
}

function bindMapEvents() {
  cy.on('tap', 'node', (ev) => {
    if (connectMode) {
      const id = ev.target.id();
      if (!connectFrom) {
        connectFrom = id;
        ev.target.select();
        showConnectHint('Теперь кликните второе устройство (кабель протянется автоматически)…');
      } else if (connectFrom !== id) {
        finishConnect(connectFrom, id);
      }
      return;
    }
    // обычный режим: выделение и инспектор обрабатывает событие 'select'
  });

  cy.on('free', 'node', (ev) => {
    const node = ev.target;
    const pos = node.position();
    const raw = node.data('raw');
    if (!raw) return;
    clearTimeout(posTimers[raw.id]);
    posTimers[raw.id] = setTimeout(() => {
      if (raw.kind === 'room') {
        // комнаты живут в отдельной таблице
        api('/map/rooms/' + raw.id, { method: 'PATCH', body: { x: pos.x, y: pos.y } })
          .then(() => { raw.x = pos.x; raw.y = pos.y; })
          .catch(() => toast('Не удалось сохранить позицию комнаты', 'warn'));
      } else {
        const payload = { id: raw.id, x: pos.x, y: pos.y, room_id: roomAt(pos.x, pos.y) };
        api('/map/positions', { method: 'POST', body: { nodes: [payload] } })
          .then(() => { raw.x = pos.x; raw.y = pos.y; })
          .catch(() => toast('Не удалось сохранить позицию', 'warn'));
      }
    }, 350);
  });

  cy.on('select unselect', 'node, edge', () => {
    if (connectMode) return;
    const sel = cy.elements(':selected');
    if (sel.length === 1) showInspector(sel[0]);
    else hideInspector();
  });

  window.addEventListener('resize', () => { if (cy) cy.resize(); });
}

function roomAt(x, y) {
  for (const r of state.rooms) {
    if (Math.abs(x - r.x) <= (r.w || 460) / 2 && Math.abs(y - r.y) <= (r.h || 340) / 2) return r.id;
  }
  return 0;
}

function saveAllPositions() {
  const nodesPayload = [];
  const roomUpdates = [];
  cy.nodes().forEach((n) => {
    const raw = n.data('raw');
    if (!raw) return;
    const p = n.position();
    if (raw.kind === 'room') {
      roomUpdates.push({ id: raw.id, x: p.x, y: p.y });
    } else {
      nodesPayload.push({ id: raw.id, x: p.x, y: p.y, room_id: roomAt(p.x, p.y) });
    }
  });
  if (nodesPayload.length) api('/map/positions', { method: 'POST', body: { nodes: nodesPayload } }).catch(() => {});
  roomUpdates.forEach((r) => api('/map/rooms/' + r.id, { method: 'PATCH', body: { x: r.x, y: r.y } }).catch(() => {}));
}

/* ---------- Панель соединения ---------- */
function showConnectHint(text) {
  const h = $('#connect-hint');
  h.hidden = false;
  h.textContent = text;
}
function resetConnect() {
  connectMode = false;
  connectFrom = null;
  $('#btn-connect').classList.remove('active-mode');
  $('#connect-hint').hidden = true;
}

$('#btn-connect').addEventListener('click', () => {
  connectMode = !connectMode;
  $('#btn-connect').classList.toggle('active-mode', connectMode);
  if (connectMode) {
    connectFrom = null;
    showConnectHint('Режим «Соединить»: кликните первое устройство…');
  } else {
    $('#connect-hint').hidden = true;
  }
});

async function finishConnect(fromId, toId) {
  const src = Number(fromId.replace('n', ''));
  const dst = Number(toId.replace('n', ''));
  try {
    const edge = await api('/map/edges', { method: 'POST', body: { src, dst, label: '', color: '' } });
    if (!cy.getElementById('e' + edge.id).length) {
      cy.add({ group: 'edges', data: { id: 'e' + edge.id, source: fromId, target: toId, label: '', color: '#6b7686', raw: edge } });
      state.edges.push(edge);
    }
    toast('Кабель проложен');
  } catch (e) { toast(e.message, 'err'); }
  resetConnect();
}

/* ---------- Тулбар карты ---------- */
$('#btn-fit').addEventListener('click', () => { if (cy) cy.fit(undefined, 60); });

$('#btn-add-node').addEventListener('click', async () => {
  const r = await openModal('Новое устройство',
    '<label class="field"><span>Подпись</span><input type="text" data-field="label" placeholder="Например: Ресепшн, камера у входа"></label>' +
    '<label class="field"><span>Тип</span><select data-field="kind">' +
    Object.keys(KIND_RU).filter((k) => k !== 'room').map((k) => '<option value="' + k + '">' + KIND_RU[k] + '</option>').join('') +
    '</select></label>',
    'Добавить');
  if (!r.ok || !r.fields.label) return;
  const center = cy ? cy.extent() : null;
  const x = center ? (center.x1 + center.w / 2) : 100;
  const y = center ? (center.y1 + center.h / 2) : 100;
  try {
    const node = await api('/map/nodes', { method: 'POST', body: { label: r.fields.label, kind: r.fields.kind, x, y } });
    state.nodes.push(node);
    if (cy) cy.add({ group: 'nodes', data: nodeElData(node), position: { x, y } });
  } catch (e) { toast(e.message, 'err'); }
});

$('#btn-add-room').addEventListener('click', async () => {
  const r = await openModal('Новая комната',
    '<label class="field"><span>Название</span><input type="text" data-field="name" value="Новая комната" placeholder="Например: Отдел Упаковка"></label>' +
    '<div style="display:flex;gap:12px">' +
    '<label class="field" style="flex:1"><span>Ширина</span><input type="number" data-field="w" value="480"></label>' +
    '<label class="field" style="flex:1"><span>Высота</span><input type="number" data-field="h" value="360"></label></div>',
    'Создать');
  if (!r.ok || !r.fields.name) return;
  const center = cy ? cy.extent() : null;
  const x = center ? (center.x1 + center.w / 2) : 100;
  const y = center ? (center.y1 + center.h / 2) : 100;
  try {
    const room = await api('/map/rooms', { method: 'POST', body: { name: r.fields.name, x, y, w: Number(r.fields.w) || 480, h: Number(r.fields.h) || 360 } });
    state.rooms.push(room);
    if (cy) cy.add({ group: 'nodes', data: nodeElData(room), position: { x, y }, classes: 'room' });
    toast('Комната создана. Перетащите её под устройства.');
  } catch (e) { toast(e.message, 'err'); }
});

$('#btn-delete-sel').addEventListener('click', async () => {
  const sel = cy ? cy.elements(':selected') : null;
  if (!sel || !sel.length) { toast('Сначала кликните элемент на карте', 'info'); return; }
  if (!(await confirmModal('Удалить с карты?', 'Выбранный элемент и его связи будут удалены.')).ok) return;
  for (const el of sel) {
    const raw = el.data('raw');
    if (!raw) continue;
    try {
      if (el.is('edge')) {
        await api('/map/edges/' + raw.id, { method: 'DELETE' });
        state.edges = state.edges.filter((x) => x.id !== raw.id);
      } else if (raw.kind === 'room') {
        await api('/map/rooms/' + raw.id, { method: 'DELETE' });
        state.rooms = state.rooms.filter((x) => x.id !== raw.id);
      } else {
        await api('/map/nodes/' + raw.id, { method: 'DELETE' });
        state.nodes = state.nodes.filter((x) => x.id !== raw.id);
        // связанные рёбра удалит бэкенд
        state.edges = state.edges.filter((x) => x.src !== raw.id && x.dst !== raw.id);
      }
      el.remove();
    } catch (e) { toast(e.message, 'err'); }
  }
  hideInspector();
});

/* ---------- Инспектор ---------- */
function showInspector(el) {
  const panel = $('#inspector');
  const body = $('#inspector-body');
  const raw = el.data('raw');
  if (!raw) { hideInspector(); return; }

  if (el.is('edge')) {
    $('#inspector-title').textContent = 'Кабель / связь';
    body.innerHTML =
      '<label class="field"><span>Подпись</span><input type="text" id="ins-label" value="' + esc(raw.label || '') + '" placeholder="например: патч 12, 3 метра"></label>' +
      '<label class="field"><span>Цвет линии</span><select id="ins-color">' +
      EDGE_COLORS.map((c) => '<option value="' + c[0] + '"' + (c[0] === (raw.color || '') ? ' selected' : '') + '>' + c[1] + '</option>').join('') +
      '</select></label>' +
      '<button class="btn primary wide" id="ins-save" style="margin-top:14px">Сохранить</button>';
    $('#ins-save').addEventListener('click', async () => {
      const color = $('#ins-color').value;
      try {
        const upd = await api('/map/edges/' + raw.id, { method: 'PATCH', body: { label: $('#ins-label').value, color } });
        Object.assign(raw, upd);
        el.data('label', upd.label || '');
        el.data('color', color || '#6b7686');
        toast('Кабель обновлён');
        hideInspector();
      } catch (e) { toast(e.message, 'err'); }
    });
  } else if (raw.kind === 'room') {
    $('#inspector-title').textContent = 'Комната';
    body.innerHTML =
      '<label class="field"><span>Название</span><input type="text" id="ins-label" value="' + esc(raw.name || '') + '"></label>' +
      '<div style="display:flex;gap:12px">' +
      '<label class="field" style="flex:1"><span>Ширина</span><input type="number" id="ins-w" value="' + (raw.w || 460) + '"></label>' +
      '<label class="field" style="flex:1"><span>Высота</span><input type="number" id="ins-h" value="' + (raw.h || 340) + '"></label></div>' +
      '<button class="btn primary wide" id="ins-save" style="margin-top:14px">Сохранить</button>';
    $('#ins-save').addEventListener('click', async () => {
      try {
        const upd = await api('/map/rooms/' + raw.id, {
          method: 'PATCH',
          body: { name: $('#ins-label').value, w: Number($('#ins-w').value) || 460, h: Number($('#ins-h').value) || 340 }
        });
        Object.assign(raw, upd);
        el.data('label', '🏠 ' + upd.name);
        el.data('w', upd.w); el.data('h', upd.h);
        toast('Комната обновлена');
        hideInspector();
      } catch (e) { toast(e.message, 'err'); }
    });
  } else {
    const host = raw.host || {};
    $('#inspector-title').textContent = 'Устройство';
    body.innerHTML =
      '<div class="insp-row"><span class="k">Подпись</span><span class="v">' + esc(raw.label) + '</span></div>' +
      (raw.host_mac ? '<div class="insp-row"><span class="k">MAC</span><span class="v">' + esc(raw.host_mac) + '</span></div>' : '') +
      (host.ip ? '<div class="insp-row"><span class="k">IP</span><span class="v">' + esc(host.ip) + '</span></div>' : '') +
      (host.vendor ? '<div class="insp-row"><span class="k">Производитель</span><span class="v">' + esc(host.vendor) + '</span></div>' : '') +
      (host.last_seen ? '<div class="insp-row"><span class="k">Видели</span><span class="v">' + ago(host.last_seen) + '</span></div>' : '') +
      '<label class="field"><span>Подпись на карте</span><input type="text" id="ins-label" value="' + esc(raw.label) + '"></label>' +
      '<label class="field"><span>Тип</span><select id="ins-kind">' +
      Object.keys(KIND_RU).filter((k) => k !== 'room').map((k) =>
        '<option value="' + k + '"' + (k === raw.kind ? ' selected' : '') + '>' + KIND_RU[k] + '</option>').join('') +
      '</select></label>' +
      '<label class="field"><span>Цвет</span><select id="ins-color">' +
      [''].concat(Object.keys(KIND_COLOR)).map((c) =>
        '<option value="' + c + '"' + (c === (raw.color || '') ? ' selected' : '') + '>' +
        (c ? KIND_RU[c] : 'Авто по типу') + '</option>').join('') +
      '</select></label>' +
      '<button class="btn primary wide" id="ins-save" style="margin-top:14px">Сохранить</button>';
    $('#ins-save').addEventListener('click', async () => {
      try {
        const upd = await api('/map/nodes/' + raw.id, {
          method: 'PATCH',
          body: { label: $('#ins-label').value, kind: $('#ins-kind').value, color: $('#ins-color').value, auto_label: 0 }
        });
        Object.assign(raw, upd);
        el.data('label', (KIND_ICON[upd.kind] || '❓') + ' ' + upd.label);
        el.data('kind', upd.kind);
        el.data('color', upd.color || KIND_COLOR[upd.kind] || KIND_COLOR.unknown);
        toast('Устройство обновлено');
        hideInspector();
      } catch (e) { toast(e.message, 'err'); }
    });
  }
  panel.hidden = false;
}
function hideInspector() { $('#inspector').hidden = true; }
$('#inspector-close').addEventListener('click', hideInspector);
$('#session-close').addEventListener('click', () => { $('#session-panel').hidden = true; });

/* ---------- Сессия «Слушать точку» ---------- */
let sessionTimer = null;

$('#session-duration').addEventListener('change', () => {
  const s = $('#session-duration').value;
  $('#btn-session-start').textContent = '▶ Слушать точку (' + s + ' сек)';
});

$('#btn-session-start').addEventListener('click', async () => {
  if (state.session) { toast('Прослушивание уже идёт', 'warn'); return; }
  const label = $('#session-label').value.trim();
  const iface = $('#session-iface').value;
  const dur = Number($('#session-duration').value) || 30;
  try {
    const r = await api('/sessions/start', { method: 'POST', body: { label, iface, duration_sec: dur } });
    startSessionUI(r.session);
    toast('Слушаю точку «' + r.session.label + '» — ' + dur + ' сек');
  } catch (e) { toast(e.message, 'err'); }
});

$('#btn-session-stop').addEventListener('click', async () => {
  try { await api('/sessions/stop', { method: 'POST' }); } catch (e) { toast(e.message, 'err'); }
});

function startSessionUI(sess) {
  state.session = sess;
  $('#session-active').hidden = false;
  $('#btn-session-start').disabled = true;
  $('#session-log').innerHTML = '';
  $('#session-found').textContent = 'Найдено: 0';
  tickSession();
  sessionTimer = setInterval(tickSession, 300);
  if (!mapReady) initMapWhenReady();
  // узел-свитч уже создан бэкендом — подтянем его на карту
  applyMapNode({ id: sess.node_id, label: sess.label, kind: 'switch', x: 0, y: 0, host_mac: '', color: '', room_id: 0, auto_label: 0 });
}

function stopSessionUI(info) {
  state.session = null;
  clearInterval(sessionTimer);
  $('#session-active').hidden = true;
  $('#btn-session-start').disabled = false;
  if (info) toast('Прослушивание завершено. Устройств найдено: ' + (info.found ?? 0), 'ok', 6000);
}

function tickSession() {
  const s = state.session;
  if (!s) return;
  const left = Math.max(0, s.ends_at - Date.now() / 1000);
  const total = Math.max(1, s.ends_at - s.started);
  $('#session-progress').style.width = Math.max(2, (left / total) * 100) + '%';
  $('#session-countdown').textContent = '⏱ ' + Math.ceil(left) + ' с';
  $('#session-found').textContent = 'Найдено: ' + (s.found_count ?? 0);
  if (left <= 0) stopSessionUI(null); // финальное событие придёт по WS
}

function sessionCatch(host) {
  if (!state.session) return;
  const log = $('#session-log');
  const item = document.createElement('div');
  item.className = 'log-item';
  const name = host.hostname || host.ip || host.mac;
  item.innerHTML = '<span>' + (KIND_ICON[host.device_type] || '❓') + ' ' + esc(name) + '</span>' +
    '<span class="t">' + fmtTime(Date.now() / 1000) + '</span>';
  log.prepend(item);
}

/* ---------- Живое обновление карты по WS ---------- */
function applyMapNode(node) {
  if (!cy) return;
  const existing = cy.getElementById((node.kind === 'room' ? 'r' : 'n') + node.id);
  if (existing.length) {
    existing.data('raw', node);
    if (node.kind === 'room') {
      existing.data('label', '🏠 ' + (node.label || node.name || 'Комната'));
    } else if (node.kind === 'switch') {
      existing.data('label', '🔌 ' + node.label);
    } else {
      existing.data('label', (KIND_ICON[node.kind] || '❓') + ' ' + node.label);
    }
    if (node.x || node.y) existing.position({ x: node.x, y: node.y });
    return;
  }
  if (node.kind === 'room') {
    cy.add({ group: 'nodes', data: nodeElData(node), position: { x: node.x, y: node.y }, classes: 'room' });
    return;
  }
  state.nodes.push(node);
  cy.add({ group: 'nodes', data: nodeElData(node), position: { x: node.x, y: node.y } });
}

function applyMapEdge(edge) {
  if (!cy) return;
  if (cy.getElementById('e' + edge.id).length) return;
  const src = cy.getElementById('n' + edge.src);
  const dst = cy.getElementById('n' + edge.dst);
  if (!src.length || !dst.length) {
    // возможно, узлы ещё не подгружены — обновим карту целиком
    refreshMapSoft();
    return;
  }
  cy.add({ group: 'edges', data: { id: 'e' + edge.id, source: 'n' + edge.src, target: 'n' + edge.dst, label: edge.label || '', color: edge.color || '#6b7686', raw: edge } });
  state.edges.push(edge);
}

let refreshTimer = null;
function refreshMapSoft() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    if (!cy) return;
    try {
      await loadMap();
      cy.elements().remove();
      cy.add(buildElements());
    } catch (e) { /* тихо */ }
  }, 600);
}

/* ============================================================
   ВКЛАДКА 3 — Паспорта
   ============================================================ */
async function loadHosts() {
  try {
    const d = await api('/hosts');
    state.hosts = d.hosts;
    renderHosts();
  } catch (e) {
    $('#hosts-table tbody').innerHTML =
      '<tr><td colspan="12" class="muted empty-cell">Ошибка загрузки: ' + esc(e.message) + '</td></tr>';
  }
}

function hostMatches(h) {
  const q = state.hostFilter.toLowerCase();
  if (q) {
    const hay = [h.hostname, h.ip, h.mac, h.vendor, h.model, h.serial, h.os, h.note, h.username]
      .join(' ').toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (state.hostType === 'passport') {
    return !!(h.serial || h.model || h.cpu);
  }
  if (state.hostType && h.device_type !== state.hostType) return false;
  return true;
}

function renderHosts() {
  const rows = state.hosts.filter(hostMatches);
  $('#hosts-count').textContent = 'Показано: ' + rows.length + ' из ' + state.hosts.length;
  const tb = $('#hosts-table tbody');
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="12" class="muted empty-cell">Ничего не найдено. Устройства появятся, как только радар услышит их в сети, или после запуска агента на ПК.</td></tr>';
    return;
  }
  tb.innerHTML = rows.map((h) => {
    const hasPassport = !!(h.serial || h.model || h.cpu);
    return '<tr>' +
      '<td><b>' + esc(h.hostname || '—') + '</b>' + (hasPassport ? ' <span class="passport-mark" title="Есть паспорт">🪪</span>' : '') + '</td>' +
      '<td class="mono">' + esc(h.ip || '—') + '</td>' +
      '<td class="mono">' + esc(h.mac) + '</td>' +
      '<td><span class="type-pill ' + h.device_type + '">' + (KIND_RU[h.device_type] || h.device_type) + '</span></td>' +
      '<td>' + esc(h.vendor || '—') + '</td>' +
      '<td>' + esc(h.model || '—') + '</td>' +
      '<td>' + esc(h.cpu || '—') + '</td>' +
      '<td>' + esc(h.ram || '—') + '</td>' +
      '<td>' + esc(h.os || '—') + '</td>' +
      '<td class="mono">' + esc(h.serial || '—') + '</td>' +
      '<td class="muted">' + ago(h.last_seen) + '</td>' +
      '<td><div class="row-actions">' +
        '<button class="icon-btn" data-edit="' + h.id + '" title="Изменить">✏️</button>' +
        '<button class="icon-btn" data-del="' + h.id + '" title="Удалить">🗑</button>' +
      '</div></td></tr>';
  }).join('');
}

let searchTimer = null;
$('#hosts-search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.hostFilter = e.target.value.trim(); renderHosts(); }, 250);
});
$('#hosts-filters').addEventListener('click', (e) => {
  const chip = e.target.closest('button.chip');
  if (!chip) return;
  $$('#hosts-filters .chip').forEach((c) => c.classList.toggle('active', c === chip));
  state.hostType = chip.dataset.type;
  renderHosts();
});

$('#hosts-table').addEventListener('click', async (e) => {
  const editBtn = e.target.closest('[data-edit]');
  const delBtn = e.target.closest('[data-del]');
  if (editBtn) {
    const h = state.hosts.find((x) => x.id === Number(editBtn.dataset.edit));
    if (!h) return;
    const r = await openModal('Устройство: ' + (h.hostname || h.mac),
      '<label class="field"><span>Имя</span><input type="text" data-field="hostname" value="' + esc(h.hostname) + '"></label>' +
      '<label class="field"><span>Тип</span><select data-field="device_type">' +
      Object.keys(KIND_RU).filter((k) => k !== 'switch' && k !== 'room').map((k) =>
        '<option value="' + k + '"' + (k === h.device_type ? ' selected' : '') + '>' + KIND_RU[k] + '</option>').join('') +
      '</select></label>' +
      '<label class="field"><span>Заметка</span><textarea data-field="note">' + esc(h.note || '') + '</textarea></label>',
      'Сохранить');
    if (!r.ok) return;
    try {
      await api('/hosts/' + h.id, { method: 'PATCH', body: r.fields });
      Object.assign(h, r.fields);
      renderHosts();
      toast('Сохранено');
    } catch (err) { toast(err.message, 'err'); }
  }
  if (delBtn) {
    const h = state.hosts.find((x) => x.id === Number(delBtn.dataset.del));
    if (!h) return;
    if (!(await confirmModal('Удалить устройство?', '«' + (h.hostname || h.mac) + '» исчезнет из таблицы и с карты сети.')).ok) return;
    try {
      await api('/hosts/' + h.id, { method: 'DELETE' });
      state.hosts = state.hosts.filter((x) => x.id !== h.id);
      renderHosts();
      refreshMapSoft();
    } catch (err) { toast(err.message, 'err'); }
  }
});

$('#btn-agent-win').addEventListener('click', () => { window.open('/api/agent/windows', '_blank'); });
$('#btn-agent-linux').addEventListener('click', () => { window.open('/api/agent/linux', '_blank'); });

/* ============================================================
   Бэкап / импорт
   ============================================================ */
$('#btn-backup').addEventListener('click', () => { window.open('/api/backup/export', '_blank'); });

$('#btn-import').addEventListener('click', () => $('#file-import').click());
$('#file-import').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch (err) {
    toast('Файл не является корректным JSON', 'err');
    return;
  }
  const isAgent = data.macs && !data.hosts;
  const mode = isAgent ? 'merge'
    : (await openModal('Импорт: ' + file.name,
      '<p class="muted small">Как загрузить данные?</p>' +
      '<label class="field"><span>Режим</span><select data-field="mode">' +
      '<option value="merge">Объединить с текущей картой</option>' +
      '<option value="replace">Заменить всё (точное восстановление бэкапа)</option></select></label>',
      'Импортировать')).fields.mode;
  if (!mode) return;
  try {
    const r = await api('/backup/import?mode=' + mode, { method: 'POST', body: data });
    toast('Импорт выполнен ✔');
    await loadHosts();
    if (mapReady) {
      await loadMap();
      if (cy) { cy.elements().remove(); cy.add(buildElements()); }
    }
    loadMeasurements();
  } catch (err) { toast('Импорт не удался: ' + err.message, 'err'); }
});

/* ============================================================
   WebSocket-соединения
   ============================================================ */
function setDot(id, on, warn) {
  const el = $(id);
  el.className = 'status-dot' + (on ? ' on' : warn ? ' warn' : '');
}

function wsUrl(path) {
  return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + path;
}

function connectIfacesWS() {
  const ws = new WebSocket(wsUrl('/ws/interfaces'));
  ws.onopen = () => { state.wsIfOk = true; setDot('#dot-api', true); updateFooter(); };
  ws.onmessage = (m) => {
    try {
      const d = JSON.parse(m.data);
      if (d.type === 'interfaces') {
        state.interfaces = d.interfaces || [];
        state.stats = d.stats || {};
        renderInterfaceCards();
        updateFooter();
      }
    } catch (e) { /* игнорируем битые кадры */ }
  };
  ws.onclose = () => {
    state.wsIfOk = false; setDot('#dot-api', false, true); updateFooter();
    setTimeout(connectIfacesWS, 2500);
  };
}

function connectEventsWS() {
  const ws = new WebSocket(wsUrl('/ws/events'));
  ws.onopen = () => { state.wsEvOk = true; updateFooter(); };
  ws.onmessage = (m) => {
    let d;
    try { d = JSON.parse(m.data); } catch (e) { return; }
    handleEvent(d);
  };
  ws.onclose = () => {
    state.wsEvOk = false; updateFooter();
    setTimeout(connectEventsWS, 2500);
  };
}

let lastToastPerMac = {};
function handleEvent(d) {
  if (d.type === 'discovery') {
    const h = d.host;
    // обновляем таблицу паспортов
    const idx = state.hosts.findIndex((x) => x.mac === h.mac);
    if (idx >= 0) state.hosts[idx] = { ...state.hosts[idx], ...h };
    else state.hosts.unshift(h);
    if ($('#pane-hosts').classList.contains('active') || $('#pane-tester').classList.contains('active')) renderHosts();
    // тост — не чаще одного на устройство в 30 сек (кроме паспортов агента)
    const now = Date.now();
    if (d.iface === 'agent') {
      toast('🪪 Паспорт получен: ' + (h.hostname || h.mac));
    } else if (!lastToastPerMac[h.mac] || now - lastToastPerMac[h.mac] > 30000) {
      lastToastPerMac[h.mac] = now;
      const name = h.hostname || h.ip || h.mac;
      toast('📡 Радар: ' + name + (h.vendor ? ' (' + h.vendor + ')' : ''), 'info', 3500);
    }
    if (state.session) sessionCatch(h);
  } else if (d.type === 'session') {
    if (d.action === 'started') {
      if (!state.session) startSessionUI(d.session);
      applyMapNode(d.node);
    } else if (d.action === 'progress') {
      if (state.session) {
        state.session.found_count = d.found;
        $('#session-found').textContent = 'Найдено: ' + d.found;
      }
    } else {
      stopSessionUI(d);
    }
  } else if (d.type === 'map_changed') {
    if (d.full_reload) { refreshMapSoft(); return; }
    if (d.node) applyMapNode(d.node);
    if (d.edge) applyMapEdge(d.edge);
  } else if (d.type === 'lldp') {
    // LLDP-сосед появится на карточке тестера при следующем срезе (раз в 2 с)
  }
}

/* ============================================================
   Подвал, часы, статистика
   ============================================================ */
function updateFooter() {
  const sniff = state.stats && state.stats.interfaces && state.stats.interfaces.length;
  setDot('#dot-sniff', !!sniff, !sniff);
  const parts = [];
  parts.push(state.wsIfOk ? 'сервер: связь' : 'сервер: нет связи');
  parts.push(sniff ? 'радар: слушаю ' + state.stats.interfaces.join(', ') : 'радар: нет интерфейсов');
  $('#footer-status').textContent = parts.join(' · ');
  $('#footer-stats').textContent = 'пакетов просмотрено: ' + ((state.stats && state.stats.packets) || 0);
}

setInterval(() => {
  $('#clock').textContent = new Date().toLocaleString('ru-RU', {
    weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  });
}, 1000);

/* ============================================================
   Старт
   ============================================================ */
(async function init() {
  connectIfacesWS();
  connectEventsWS();
  loadMeasurements();
  loadHosts();
  setInterval(loadHosts, 20000); // страховка: раз в 20 секунд синхронизируем таблицу
})();
