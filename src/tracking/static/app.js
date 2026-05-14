'use strict';

// ── Config ────────────────────────────────────────────────────────────────
const API = '';  // same origin

// ── State ─────────────────────────────────────────────────────────────────
let wsState = null;
let ws = null;
let _debugPollingInterval = null;
let _elapsedInterval = null;
let _chartUtilization = null;
let _chartScores = null;
let _selectedStateId = null;
let _prevPlanStatus = null;
let _prevStepStatuses = {};
let _prevEstop = null;
let _lastCompletedLogId = null;

// ── DOM refs ──────────────────────────────────────────────────────────────
const wsDot           = document.getElementById('ws-dot');
const planStatusBadge = document.getElementById('plan-status-badge');
const headerMode      = document.getElementById('header-mode');

// ── Mermaid init ──────────────────────────────────────────────────────────
mermaid.initialize({ startOnLoad: false, theme: 'dark' });

// ═════════════════════════════════════════════════════════════════════════
// TAB ROUTING
// ═════════════════════════════════════════════════════════════════════════
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = document.getElementById(`tab-${btn.dataset.tab}`);
      if (panel) panel.classList.add('active');
      onTabActivate(btn.dataset.tab);
    });
  });

  document.querySelectorAll('.sub-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const parent = btn.closest('.tab-panel');
      parent.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
      parent.querySelectorAll('.sub-tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = document.getElementById(`subtab-${btn.dataset.subtab}`);
      if (panel) panel.classList.add('active');
      onSubTabActivate(btn.dataset.subtab);
    });
  });
}

function onTabActivate(tab) {
  if (tab === 'state') loadCapabilityStates();
  if (tab === 'plan')  { loadSavedPlanList(); loadPlanStateList(); }
  if (tab === 'export') loadPlansList();
  if (tab === 'visualize') {
    const activeSubtab = document.querySelector('#tab-visualize .sub-tab-btn.active');
    if (activeSubtab) onSubTabActivate(activeSubtab.dataset.subtab);
  }
}

function onSubTabActivate(subtab) {
  if (subtab === 'diagram') loadDiagram();
  if (subtab === 'stats')   loadStats();
  if (subtab === 'log')     loadHistory();
}

// ═════════════════════════════════════════════════════════════════════════
// WEBSOCKET
// ═════════════════════════════════════════════════════════════════════════
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    wsDot.classList.add('connected');
    addPlanLog('WebSocket connected', 'info');
  };

  ws.onmessage = (ev) => {
    try {
      const newState = JSON.parse(ev.data);
      detectStateChanges(newState);
      wsState = newState;
      updateHeaderFromState();
      // If plan tab is active, update steps
      if (document.querySelector('.tab-btn[data-tab="plan"]')?.classList.contains('active')) {
        renderPlanSteps();
        renderActiveOp();
        renderWorkcellIndicators();
      }
    } catch (e) {
      console.error('WS parse error', e);
    }
  };

  ws.onclose = () => {
    wsDot.classList.remove('connected');
    addPlanLog('WebSocket disconnected — retrying in 3s', 'warn');
    setTimeout(connectWS, 3000);
  };

  ws.onerror = () => ws.close();
}

function updateHeaderFromState() {
  if (!wsState) return;
  const plan = wsState.plan;
  if (plan) {
    planStatusBadge.style.display = '';
    planStatusBadge.className = `plan-status ps-${plan.status}`;
    planStatusBadge.textContent = plan.status.toUpperCase();
    headerMode.style.display = '';
    headerMode.className = `mode-badge mode-${plan.mode}`;
    headerMode.textContent = `MODE ${plan.mode}`;
  } else {
    planStatusBadge.style.display = 'none';
    headerMode.style.display = 'none';
  }
}

function detectStateChanges(newState) {
  const plan = newState.plan;
  if (plan) {
    if (plan.status !== _prevPlanStatus) {
      const cls = plan.status === 'completed' ? 'success'
                : plan.status === 'failed'    ? 'failure'
                : plan.status === 'running'   ? 'info' : '';
      addPlanLog(`Plan ${plan.status.toUpperCase()}: ${plan.plan_id}`, cls);
      _prevPlanStatus = plan.status;
      if (plan.status === 'completed' || plan.status === 'failed') {
        // Fetch newest log id so the Download Log button can use it
        apiGet('/viz/execution-logs').then(logs => {
          if (logs && logs.length) {
            _lastCompletedLogId = logs[0].log_id;
            document.getElementById('btn-download-log').style.display = '';
          }
        }).catch(() => {});
      }
    }
    for (const step of (plan.steps || [])) {
      const prev = _prevStepStatuses[step.step_index];
      if (prev !== step.status) {
        if (step.status === 'active') {
          addPlanLog(`Step ${step.step_index}: ${step.name} → ACTIVE (op ${step.op_id})`, 'warn');
        } else if (step.status === 'done') {
          addPlanLog(`Step ${step.step_index}: ${step.name} → DONE (${((step.duration_ms||0)/1000).toFixed(1)}s)`, 'success');
        } else if (step.status === 'failed') {
          addPlanLog(`Step ${step.step_index}: ${step.name} → FAILED rc=${step.result_code}`, 'failure');
        }
        _prevStepStatuses[step.step_index] = step.status;
      }
    }
  }
  const estop = newState.workcell.estop_ok;
  if (_prevEstop !== null && _prevEstop !== estop) {
    addPlanLog(`E-Stop: ${estop ? 'CLEARED' : 'ACTIVATED'}`, estop ? 'info' : 'failure');
  }
  _prevEstop = estop;
}

// ═════════════════════════════════════════════════════════════════════════
// PANEL 1: SEED
// ═════════════════════════════════════════════════════════════════════════
function initSeedPanel() {
  document.getElementById('btn-seed').addEventListener('click', doSeed);
  document.getElementById('btn-clear').addEventListener('click', doClear);
}

async function doSeed() {
  const progressWrap = document.getElementById('seed-progress-wrap');
  const progressFill = document.getElementById('seed-progress-fill');
  const statusText   = document.getElementById('seed-status-text');
  const countsEl     = document.getElementById('seed-entity-counts');
  const logEl        = document.getElementById('seed-log');

  progressWrap.style.display = '';
  progressFill.style.width = '0%';
  statusText.textContent = 'Connecting to TypeDB...';
  countsEl.innerHTML = '';
  logEl.innerHTML = '';

  const phases = { robots: 0, tools: 0, positions: 0, steps: 0, relations: 0 };
  const phaseOrder = ['robots', 'tools', 'positions', 'steps', 'relations'];

  try {
    const resp = await fetch(`${API}/seed`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      showToast(err.error || 'Seed failed', 'error');
      progressWrap.style.display = 'none';
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let phaseIdx = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        try {
          const evt = JSON.parse(line.slice(5).trim());
          addSeedLog(JSON.stringify(evt), 'info');

          if (evt.error) {
            statusText.textContent = `Error: ${evt.error}`;
            progressFill.style.background = 'var(--red)';
            showToast(`Seed error: ${evt.error}`, 'error');
            return;
          }

          if (evt.phase && evt.phase !== 'done' && evt.phase !== 'error') {
            phases[evt.phase] = evt.count || 0;
            phaseIdx = phaseOrder.indexOf(evt.phase) + 1;
            const pct = Math.round((phaseIdx / (phaseOrder.length + 1)) * 100);
            progressFill.style.width = `${pct}%`;
            statusText.textContent = `Seeding ${evt.phase} (${evt.count})...`;
            renderEntityCounts(phases);
          }

          if (evt.phase === 'error') {
            statusText.textContent = `Error: ${evt.detail || 'unknown'}`;
            progressFill.style.background = 'var(--red)';
            showToast(`Seed error: ${evt.detail}`, 'error');
          }

          if (evt.phase === 'done') {
            progressFill.style.width = '100%';
            progressFill.style.background = 'var(--green)';
            statusText.textContent = `Done — ${evt.total} statements inserted`;
            showToast(`Seed complete: ${evt.total} statements`, 'success');
          }
        } catch (e) {
          // ignore parse errors on partial lines
        }
      }
    }
  } catch (e) {
    showToast(`Seed failed: ${e.message}`, 'error');
    progressFill.style.background = 'var(--red)';
    statusText.textContent = `Failed: ${e.message}`;
  }
}

function renderEntityCounts(phases) {
  const countsEl = document.getElementById('seed-entity-counts');
  countsEl.innerHTML = Object.entries(phases).map(([k, v]) => `
    <div class="entity-count-badge">
      <span class="ec-label">${k}</span>
      <span class="ec-value">${v}</span>
    </div>
  `).join('');
}

function addSeedLog(msg, level = '') {
  const logEl = document.getElementById('seed-log');
  const ts = new Date().toTimeString().slice(0, 8);
  const div = document.createElement('div');
  div.className = 'log-row';
  div.innerHTML = `<span class="log-ts">${ts}</span><span class="log-msg ${level}">${escHtml(msg)}</span>`;
  logEl.prepend(div);
  while (logEl.children.length > 100) logEl.lastChild.remove();
}

async function doClear() {
  if (!confirm('Clear and recreate the TypeDB database? All data will be lost.')) return;
  try {
    const r = await apiPost('/clear');
    showToast('Database cleared', 'success');
    addSeedLog('Database cleared: ' + JSON.stringify(r), 'warn');
  } catch (e) {
    showToast(`Clear failed: ${e.message}`, 'error');
  }
}

// ═════════════════════════════════════════════════════════════════════════
// PANEL 2: STATE
// ═════════════════════════════════════════════════════════════════════════
// Resource data cache for dropdowns
let _resourceData = null;

async function loadResourceData() {
  if (_resourceData) return _resourceData;
  try {
    _resourceData = await apiGet('/resource-data');
  } catch (e) {
    // Fallback: hardcoded from known montrac data
    _resourceData = {
      product: 'MAZE_106',
      tools: [
        {tool_id:1,  label:'T1',  model:'Calibration needle', type:'calibration'},
        {tool_id:2,  label:'T2',  model:'EGI-40',             type:'electric_finger'},
        {tool_id:3,  label:'T3',  model:'EGL-90',             type:'electric_finger'},
        {tool_id:7,  label:'T7',  model:'EGL-90',             type:'electric_finger'},
        {tool_id:8,  label:'T8',  model:'PGN+P100-1',         type:'pneumatic_finger'},
        {tool_id:9,  label:'T9',  model:'PGN+P100-1',         type:'pneumatic_finger'},
        {tool_id:10, label:'T10', model:'Suction cup',         type:'suction'},
        {tool_id:12, label:'T12', model:'PGN+P64-1',           type:'pneumatic_finger'},
        {tool_id:16, label:'T16', model:'EGI-80',              type:'electric_finger'},
      ],
      robots: [
        {robot_id:1, label:'R1', model:'KUKA Agilus 2', slot_tool_ids:[2,3,7,8]},
        {robot_id:2, label:'R2', model:'KUKA Agilus 2', slot_tool_ids:[9,10,12,16]},
        {robot_id:3, label:'R3', model:'KUKA Agilus 2', slot_tool_ids:[2,8,12,16]},
      ],
      base_ids: [11,12,13,21,22,23,31],
      components: [
        {component:'START-INSERT_106',      default_position:'maze_106_pick_insert_start'},
        {component:'FINISH-INSERT_106',     default_position:'maze_106_pick_insert_finish'},
        {component:'LOGO-INSERT_106-RICAIP',default_position:'maze_106_pick_insert_logo'},
        {component:'BALL_106',              default_position:'maze_106_pick_glass'},
        {component:'COVER_106',             default_position:'maze_106_pick_cover'},
        {component:'SNAP-RIVET_106',        default_position:'maze_106_place_rivet_1'},
      ],
      positions: [],
    };
  }
  return _resourceData;
}

function initStatePanel() {
  document.getElementById('btn-refresh-states').addEventListener('click', loadCapabilityStates);
  document.getElementById('btn-save-state').addEventListener('click', saveCapabilityState);
  document.getElementById('btn-clear-editor').addEventListener('click', clearStateEditor);
  // Load resource data then build grids
  loadResourceData().then(rd => {
    initRobotStateGrid(rd);
    initComponentsGrid(rd);
  });
}

function _toolOptions(toolIds, allTools, selectedId) {
  const noneOpt = `<option value="0"${!selectedId ? ' selected' : ''}>— None —</option>`;
  const opts = toolIds.map(tid => {
    const t = allTools.find(x => x.tool_id === tid);
    const typeName = t?.type ? ' · ' + t.type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : '';
    const label = t ? `${t.tool_id} — ${t.label} — ${t.model}${typeName}` : `Tool ${tid}`;
    const sel = tid === selectedId ? ' selected' : '';
    return `<option value="${tid}"${sel}>${escHtml(label)}</option>`;
  });
  return noneOpt + opts.join('');
}

function _baseOptions(baseIds, selectedId) {
  return baseIds.map(bid => {
    const sel = bid === selectedId ? ' selected' : '';
    return `<option value="${bid}"${sel}>${bid}</option>`;
  }).join('');
}

function initRobotStateGrid(rd) {
  const allTools = rd.tools || [];
  const baseIds  = rd.base_ids.length ? rd.base_ids : [11,12,13,21,22,23,31];
  const robots   = rd.robots || [{robot_id:1,label:'R1',slot_tool_ids:[2,3,7,8]},
                                  {robot_id:2,label:'R2',slot_tool_ids:[9,10,12,16]},
                                  {robot_id:3,label:'R3',slot_tool_ids:[2,8,12,16]}];

  const grid = document.getElementById('robot-state-grid');
  grid.innerHTML = `
    <table class="state-grid">
      <thead>
        <tr>
          <th>Robot</th>
          <th>Enabled</th>
          <th>Mounted Tool</th>
          <th>Error ID</th>
          <th>Base Frame</th>
        </tr>
      </thead>
      <tbody id="robot-state-tbody"></tbody>
    </table>
  `;
  const tbody = document.getElementById('robot-state-tbody');
  for (const r of robots) {
    const defaultTool = r.slot_tool_ids[0] || 0;
    const defaultBase = baseIds[0] || 11;
    tbody.insertAdjacentHTML('beforeend', `
      <tr data-robot-id="${r.robot_id}">
        <td style="color:var(--blue);font-weight:700">${escHtml(r.label)}</td>
        <td><input type="checkbox" class="rs-enabled" checked /></td>
        <td>
          <select class="rs-tool-id form-select" style="width:100%;font-size:12px">
            ${_toolOptions(r.slot_tool_ids, allTools, defaultTool)}
          </select>
        </td>
        <td><input type="number" class="rs-error-id" value="0" min="0" max="999" style="width:60px" /></td>
        <td>
          <select class="rs-base-id form-select" style="width:72px;font-size:12px">
            ${_baseOptions(baseIds, defaultBase)}
          </select>
        </td>
      </tr>
    `);
  }
}

function initComponentsGrid(rd) {
  const components = rd.components || [];
  const positions  = rd.positions  || [];
  const posGroups  = rd.position_groups || {};
  const grid = document.getElementById('components-state-grid');
  if (!components.length) {
    grid.innerHTML = '<p class="empty-msg">No component data available.</p>';
    return;
  }

  // Pick the right position subset for a component
  function _posSubset(comp) {
    if (comp === 'SNAP-RIVET_106') return (posGroups.rivet_storage || []).concat(posGroups.rivet_place || []);
    if (comp === 'BALL_106')        return posGroups.ball_positions || positions;
    return positions;
  }

  function _posOpts(comp, selectedPos) {
    const subset = _posSubset(comp);
    const opts = subset.map(p =>
      `<option value="${escHtml(p)}"${p===selectedPos?' selected':''}>${escHtml(p)}</option>`
    ).join('');
    return `<option value="">— unset —</option>${opts}`;
  }

  // Expand each component into qty individual rows
  const rows = [];
  for (const c of components) {
    const qty = c.qty || 1;
    const defaults = c.instance_defaults || [];
    for (let i = 0; i < qty; i++) {
      const defPos = defaults[i] || c.default_position || '';
      const label  = qty > 1 ? `${c.component} <span class="comp-instance">#${i+1}</span>` : c.component;
      rows.push(`
        <tr data-component="${escHtml(c.component)}" data-instance="${i+1}">
          <td style="font-size:11px;color:var(--text-muted)">${label}</td>
          <td style="text-align:center"><input type="checkbox" class="cs-present" checked /></td>
          <td>
            <select class="cs-location form-select" style="width:100%;font-size:11px">
              ${_posOpts(c.component, defPos)}
            </select>
          </td>
        </tr>
      `);
    }
  }

  grid.innerHTML = `
    <table class="state-grid">
      <thead>
        <tr>
          <th>Component</th>
          <th style="text-align:center">Present</th>
          <th>Storage / Pick Location</th>
        </tr>
      </thead>
      <tbody id="component-state-tbody">${rows.join('')}</tbody>
    </table>
  `;
}

async function loadCapabilityStates() {
  try {
    const states = await apiGet('/capability-states');
    renderStateList(states);
  } catch (e) {
    showToast(`Failed to load states: ${e.message}`, 'error');
  }
}

function renderStateList(states) {
  const listEl = document.getElementById('state-list');
  if (!states.length) {
    listEl.innerHTML = '<p class="empty-msg">No states saved yet.</p>';
    return;
  }
  listEl.innerHTML = states.map(s => `
    <div class="state-list-item ${_selectedStateId === s.state_id ? 'selected' : ''}" data-id="${escHtml(s.state_id)}">
      <span class="state-item-name">${escHtml(s.state_id)}</span>
      <span class="state-item-desc">${escHtml(s.description || '')}</span>
      <button class="state-item-del" data-id="${escHtml(s.state_id)}" title="Delete">✕</button>
    </div>
  `).join('');

  listEl.querySelectorAll('.state-list-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('state-item-del')) return;
      loadStateIntoEditor(el.dataset.id);
    });
  });
  listEl.querySelectorAll('.state-item-del').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteCapabilityState(btn.dataset.id);
    });
  });
}

async function loadStateIntoEditor(stateId) {
  try {
    const data = await apiGet(`/capability-states/${stateId}`);
    _selectedStateId = stateId;
    document.getElementById('state-id-input').value = data.state_id || stateId;
    document.getElementById('state-desc-input').value = data.description || '';

    // Populate robot rows
    const robots = data.robots || [];
    document.getElementById('robot-state-tbody').querySelectorAll('tr').forEach(row => {
      const rid = parseInt(row.dataset.robotId);
      const rd = robots.find(r => r.robot_id === rid);
      if (rd) {
        row.querySelector('.rs-enabled').checked = rd.enabled !== false;
        const toolSel = row.querySelector('.rs-tool-id');
        // Try setting select; if option missing, add a temporary option
        const toolId = rd.tool_id || 0;
        if (toolSel.querySelector(`option[value="${toolId}"]`)) {
          toolSel.value = toolId;
        } else if (toolId) {
          const opt = document.createElement('option');
          opt.value = toolId;
          opt.textContent = `Tool ${toolId} (not in rack)`;
          toolSel.insertBefore(opt, toolSel.firstChild);
          toolSel.value = toolId;
        }
        row.querySelector('.rs-error-id').value = rd.error_id || 0;
        const baseSel = row.querySelector('.rs-base-id');
        const baseId = rd.base_frame_id || 0;
        if (baseSel.querySelector(`option[value="${baseId}"]`)) {
          baseSel.value = baseId;
        }
      }
    });

    // Populate component rows (matched by component + instance)
    const components = data.components || [];
    document.getElementById('component-state-tbody')?.querySelectorAll('tr').forEach(row => {
      const comp     = row.dataset.component;
      const instance = parseInt(row.dataset.instance || '1');
      const cd = components.find(c => c.component === comp && (c.instance || 1) === instance);
      if (cd) {
        row.querySelector('.cs-present').checked = cd.present !== false;
        const locSel = row.querySelector('.cs-location');
        if (locSel && cd.location) {
          if (!locSel.querySelector(`option[value="${cd.location}"]`)) {
            const opt = document.createElement('option');
            opt.value = cd.location;
            opt.textContent = cd.location;
            locSel.appendChild(opt);
          }
          locSel.value = cd.location;
        }
      }
    });

    // Mark selected in list
    document.querySelectorAll('.state-list-item').forEach(el => {
      el.classList.toggle('selected', el.dataset.id === stateId);
    });
    showToast(`Loaded state: ${stateId}`, 'info');
  } catch (e) {
    showToast(`Failed to load state: ${e.message}`, 'error');
  }
}

async function saveCapabilityState() {
  const stateId = document.getElementById('state-id-input').value.trim();
  const desc = document.getElementById('state-desc-input').value.trim();
  if (!stateId) {
    showToast('State ID is required', 'warn');
    return;
  }

  const robots = [];
  document.getElementById('robot-state-tbody').querySelectorAll('tr').forEach(row => {
    robots.push({
      robot_id: parseInt(row.dataset.robotId),
      enabled: row.querySelector('.rs-enabled').checked,
      tool_id: parseInt(row.querySelector('.rs-tool-id').value) || 0,
      error_id: parseInt(row.querySelector('.rs-error-id').value) || 0,
      base_frame_id: parseInt(row.querySelector('.rs-base-id').value) || 0,
    });
  });

  const components = [];
  document.getElementById('component-state-tbody')?.querySelectorAll('tr').forEach(row => {
    components.push({
      component: row.dataset.component,
      instance:  parseInt(row.dataset.instance || '1'),
      present:   row.querySelector('.cs-present').checked,
      location:  row.querySelector('.cs-location').value || '',
    });
  });

  const payload = {
    state_id: stateId,
    description: desc,
    created_at: new Date().toISOString(),
    robots,
    components,
    capabilities_override: [],
  };

  try {
    await apiPost('/capability-states', payload);
    showToast(`State saved: ${stateId}`, 'success');
    loadCapabilityStates();
  } catch (e) {
    showToast(`Save failed: ${e.message}`, 'error');
  }
}

async function deleteCapabilityState(stateId) {
  if (!confirm(`Delete state "${stateId}"?`)) return;
  try {
    await apiDelete(`/capability-states/${stateId}`);
    if (_selectedStateId === stateId) {
      _selectedStateId = null;
      clearStateEditor();
    }
    showToast(`Deleted: ${stateId}`, 'success');
    loadCapabilityStates();
  } catch (e) {
    showToast(`Delete failed: ${e.message}`, 'error');
  }
}

function clearStateEditor() {
  _selectedStateId = null;
  document.getElementById('state-id-input').value = '';
  document.getElementById('state-desc-input').value = '';
  const rd = _resourceData;
  document.getElementById('robot-state-tbody').querySelectorAll('tr').forEach(row => {
    row.querySelector('.rs-enabled').checked = true;
    const toolSel = row.querySelector('.rs-tool-id');
    if (toolSel.options.length > 1) toolSel.selectedIndex = 1; // first real tool
    row.querySelector('.rs-error-id').value = 0;
    const baseSel = row.querySelector('.rs-base-id');
    if (baseSel.options.length) baseSel.selectedIndex = 0;
  });
  document.getElementById('component-state-tbody')?.querySelectorAll('tr').forEach(row => {
    row.querySelector('.cs-present').checked = true;
    const locSel = row.querySelector('.cs-location');
    // Reset to default position (stored in data-* or second option)
    const rd_comp = rd?.components?.find(c => c.component === row.dataset.component);
    if (rd_comp && locSel) {
      const opt = locSel.querySelector(`option[value="${rd_comp.default_position}"]`);
      if (opt) locSel.value = rd_comp.default_position;
      else locSel.selectedIndex = 0;
    }
  });
  document.querySelectorAll('.state-list-item').forEach(el => el.classList.remove('selected'));
}

// ═════════════════════════════════════════════════════════════════════════
// GENERATE PLAN
// ═════════════════════════════════════════════════════════════════════════
let _currentGenPlan = null;  // in-memory generated plan

function initGeneratePanel() {
  // Method toggle buttons
  document.querySelectorAll('.method-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.method-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // Populate state dropdown
  apiGet('/capability-states').then(states => {
    const sel = document.getElementById('gen-state-select');
    states.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.state_id;
      opt.textContent = `${s.state_id}${s.description ? ' — ' + s.description : ''}`;
      sel.appendChild(opt);
    });
  }).catch(() => {});

  document.getElementById('btn-generate').addEventListener('click', doGenerate);
  document.getElementById('btn-gen-save').addEventListener('click', doGenSave);
  document.getElementById('btn-gen-load').addEventListener('click', doGenLoadIntoExecutor);
  document.getElementById('btn-gen-clear').addEventListener('click', doGenClear);
}

async function doGenerate() {
  const method  = document.querySelector('.method-btn.active')?.dataset.method || 'typedb_gnn';
  const stateId = document.getElementById('gen-state-select').value || null;

  const spinner = document.getElementById('gen-spinner');
  const btn     = document.getElementById('btn-generate');
  btn.disabled  = true;
  spinner.style.display = '';

  try {
    const plan = await apiPost('/plan/generate', { method, state_id: stateId });
    _currentGenPlan = plan;
    renderGenPlan(plan);
    showToast(`Plan generated: ${plan.steps.length} steps via ${method}`, 'success');
  } catch (e) {
    showToast(`Generation failed: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    spinner.style.display = 'none';
  }
}

function renderGenPlan(plan) {
  const section  = document.getElementById('gen-plan-section');
  const meta     = document.getElementById('gen-plan-meta');
  const stepsDiv = document.getElementById('gen-plan-steps');
  section.style.display = '';

  const methodLabel = {'typedb':'TypeDB only','typedb_gnn':'TypeDB + GNN','gnn':'GNN only'}[plan.generated_by] || plan.generated_by;
  meta.textContent = `${methodLabel} · ${plan.steps.length} steps · ${plan.state_id || 'live state'}`;

  const rd = _resourceData || {};
  const allTools  = rd.tools   || [];
  const allRobots = rd.robots  || [{robot_id:1,label:'R1',slot_tool_ids:[2,3,7,8]},
                                    {robot_id:2,label:'R2',slot_tool_ids:[9,10,12,16]},
                                    {robot_id:3,label:'R3',slot_tool_ids:[2,8,12,16]}];

  function robotOpts(selectedId) {
    return allRobots.map(r =>
      `<option value="${r.robot_id}"${r.robot_id===selectedId?' selected':''}>${escHtml(r.label)}</option>`
    ).join('');
  }

  function toolOptsForRobot(robotId, selectedId) {
    const r = allRobots.find(x => x.robot_id === robotId);
    const tids = r ? r.slot_tool_ids : [];
    const none = `<option value="0"${!selectedId?' selected':''}>—</option>`;
    return none + tids.map(tid => {
      const t = allTools.find(x => x.tool_id === tid);
      const lbl = t ? `${t.tool_id} — ${t.label} — ${t.model}` : `T${tid}`;
      return `<option value="${tid}"${tid===selectedId?' selected':''}>${escHtml(lbl)}</option>`;
    }).join('');
  }

  stepsDiv.innerHTML = plan.steps.map((s, idx) => {
    const a = s.assignment;
    const scoreStr = a?.score != null ? (a.score * 100).toFixed(0) + '%' : '—';
    const scoreClass = !a ? 'score-none' : a.score >= 0.8 ? 'score-high' : a.score >= 0.5 ? 'score-mid' : 'score-low';
    const altStr = s.alternatives?.length
      ? s.alternatives.map(x => `R${x.robot_id}(${(x.score*100).toFixed(0)}%)`).join(', ')
      : '—';
    const violStr = s.filtered_out?.length
      ? s.filtered_out.map(x => `R${x.robot_id}`).join(', ')
      : '';

    return `
      <div class="gen-step-row${s.edited?' edited':''}" data-idx="${idx}">
        <div class="gen-step-header">
          <span class="gen-step-num">${s.step_index}</span>
          <span class="gen-step-name">${escHtml(s.step_name)}</span>
          <span class="gen-step-op muted-text">${escHtml(s.op_name)}</span>
          <span class="gen-score ${scoreClass}">${scoreStr}</span>
          ${s.edited ? '<span class="edited-badge">edited</span>' : ''}
        </div>
        <div class="gen-step-controls">
          <label class="form-label" style="width:36px;flex-shrink:0">Robot</label>
          <select class="form-select gen-robot-sel" style="width:80px;font-size:11px" data-idx="${idx}">
            ${robotOpts(a?.robot_id)}
          </select>
          <label class="form-label" style="width:34px;flex-shrink:0">Tool</label>
          <select class="form-select gen-tool-sel" style="flex:1;font-size:11px;min-width:0" data-idx="${idx}">
            ${toolOptsForRobot(a?.robot_id, a?.tool_id)}
          </select>
        </div>
        ${altStr !== '—' ? `<div class="gen-alts muted-text">Alt: ${escHtml(altStr)}</div>` : ''}
        ${violStr ? `<div class="gen-violations">Filtered: ${escHtml(violStr)}</div>` : ''}
      </div>`;
  }).join('');

  // Wire robot-change → re-populate tool dropdown
  stepsDiv.querySelectorAll('.gen-robot-sel').forEach(sel => {
    sel.addEventListener('change', () => {
      const idx      = parseInt(sel.dataset.idx);
      const robotId  = parseInt(sel.value);
      const toolSel  = stepsDiv.querySelector(`.gen-tool-sel[data-idx="${idx}"]`);
      toolSel.innerHTML = toolOptsForRobot(robotId, null);
      _markStepEdited(idx, robotId, parseInt(toolSel.value));
    });
  });

  stepsDiv.querySelectorAll('.gen-tool-sel').forEach(sel => {
    sel.addEventListener('change', () => {
      const idx     = parseInt(sel.dataset.idx);
      const robotId = parseInt(stepsDiv.querySelector(`.gen-robot-sel[data-idx="${idx}"]`).value);
      _markStepEdited(idx, robotId, parseInt(sel.value));
    });
  });
}

function _markStepEdited(idx, robotId, toolId) {
  if (!_currentGenPlan) return;
  const s = _currentGenPlan.steps[idx];
  s.edited = true;
  if (!s.assignment) s.assignment = {};
  s.assignment.robot_id = robotId;
  s.assignment.tool_id  = toolId;
  s.assignment.score    = null;  // manual edit clears score
  // Update edited badge in DOM
  const row = document.querySelector(`.gen-step-row[data-idx="${idx}"]`);
  if (row) {
    row.classList.add('edited');
    if (!row.querySelector('.edited-badge')) {
      row.querySelector('.gen-step-header').insertAdjacentHTML(
        'beforeend', '<span class="edited-badge">edited</span>'
      );
    }
  }
}

async function doGenSave() {
  if (!_currentGenPlan) { showToast('Nothing to save', 'warn'); return; }
  // Sync current select values into plan before saving
  const stepsDiv = document.getElementById('gen-plan-steps');
  stepsDiv.querySelectorAll('.gen-step-row').forEach(row => {
    const idx     = parseInt(row.dataset.idx);
    const robotId = parseInt(row.querySelector('.gen-robot-sel').value);
    const toolId  = parseInt(row.querySelector('.gen-tool-sel').value);
    const s = _currentGenPlan.steps[idx];
    if (!s.assignment) s.assignment = {};
    s.assignment.robot_id = robotId;
    s.assignment.tool_id  = toolId;
  });

  try {
    _DATA_PLANS_DIR_LOCAL = 'data/plans';  // server-side path
    // POST to /plans/save-generated with plan body
    const r = await fetch('/plans/save-generated', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_currentGenPlan),
    });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    showToast(`Saved: ${d.plan_id}`, 'success');
  } catch (e) {
    showToast(`Save failed: ${e.message}`, 'error');
  }
}

async function doGenLoadIntoExecutor() {
  if (!_currentGenPlan) { showToast('No generated plan', 'warn'); return; }
  // Convert generated plan to execution plan by saving then loading
  try {
    const r = await fetch('/plans/save-generated', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_currentGenPlan),
    });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    showToast(`Loaded plan ${d.plan_id} into executor`, 'info');
    // Switch to Plan tab and load it
    document.querySelector('.tab-btn[data-tab="plan"]').click();
  } catch (e) {
    showToast(`Load failed: ${e.message}`, 'error');
  }
}

function doGenClear() {
  _currentGenPlan = null;
  document.getElementById('gen-plan-section').style.display = 'none';
  document.getElementById('gen-plan-steps').innerHTML = '';
}

// ═════════════════════════════════════════════════════════════════════════
// PANEL 3: PLAN
// ═════════════════════════════════════════════════════════════════════════
async function loadSavedPlanList() {
  try {
    const plans = await apiGet('/plans');
    const sel = document.getElementById('saved-plan-select');
    // Remove old saved options (keep only the first default option)
    while (sel.options.length > 1) sel.remove(1);
    plans.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.plan_id;
      const method = p.generated_by ? ` [${p.generated_by}]` : '';
      const date = p.created_at ? ' · ' + new Date(p.created_at).toLocaleString() : '';
      opt.textContent = `${p.plan_id}${method}${date}`;
      sel.appendChild(opt);
    });
  } catch (e) { /* silent */ }
}

async function loadPlanStateList() {
  try {
    const states = await apiGet('/capability-states');
    const sel = document.getElementById('plan-state-select');
    const prev = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    states.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.state_id;
      opt.textContent = s.description ? `${s.state_id} — ${s.description}` : s.state_id;
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  } catch (e) { /* silent */ }
}

async function doApplyState() {
  const sid = document.getElementById('plan-state-select').value;
  if (!sid) { showToast('No state selected.', 'warn'); return; }
  const btn = document.getElementById('btn-apply-state');
  btn.disabled = true;
  try {
    const r = await apiPost(`/capability-states/${encodeURIComponent(sid)}/apply`);
    showToast(`State "${sid}" applied — ${r.robots_updated} robot(s) updated.`, 'success');
    addPlanLog(`State applied: ${sid} (${r.robots_updated} robots reset)`, 'info');
  } catch (e) {
    showToast(`Apply state failed: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

function initPlanPanel() {
  document.getElementById('btn-load').addEventListener('click', doLoadPlan);
  document.getElementById('btn-refresh-plan-list').addEventListener('click', loadSavedPlanList);
  document.getElementById('btn-refresh-plan-states').addEventListener('click', loadPlanStateList);
  document.getElementById('btn-apply-state').addEventListener('click', doApplyState);
  document.getElementById('btn-start').addEventListener('click', doStartPlan);
  document.getElementById('btn-pause').addEventListener('click', doPausePlan);
  document.getElementById('btn-reset').addEventListener('click', doResetPlan);
  document.getElementById('btn-ack').addEventListener('click', doAckStep);
  document.getElementById('btn-download-log').addEventListener('click', () => {
    if (_lastCompletedLogId) downloadExecLog(_lastCompletedLogId);
    else showToast('No completed execution log available yet.', 'warn');
  });

  document.getElementById('debug-toggle').addEventListener('change', function() {
    const on = this.checked;
    document.getElementById('debug-mode-label').textContent = on
      ? 'On — pauses after each step activation'
      : 'Off — steps run continuously';
  });

  document.getElementById('estop-ind').addEventListener('click', async () => {
    const current = wsState?.workcell.estop_ok ?? true;
    try { await apiPost('/workcell/estop', { ok: !current }); } catch (e) { showToast(e.message, 'error'); }
  });

  document.getElementById('conveyor-ind').addEventListener('click', async () => {
    const current = wsState?.workcell.conveyor_running ?? true;
    try { await apiPost('/workcell/conveyor', { running: !current }); } catch (e) { showToast(e.message, 'error'); }
  });
}

async function doLoadPlan() {
  const mode      = document.getElementById('mode-select').value;
  const debugMode = document.getElementById('debug-toggle').checked;
  const planId    = document.getElementById('saved-plan-select').value;

  try {
    let r;
    if (planId) {
      addPlanLog(`Loading saved plan: ${planId} (Mode ${mode}, debug=${debugMode})...`, 'info');
      r = await apiPost('/plan/load-saved', { plan_id: planId, mode, debug_mode: debugMode });
    } else {
      addPlanLog(`Loading built-in MAZE_106 plan (Mode ${mode}, debug=${debugMode})...`, 'info');
      r = await apiPost('/plan/load', { mode, debug_mode: debugMode });
    }
    addPlanLog(`Plan loaded: ${r.plan_id} — ${r.steps} steps`, 'success');
    _prevStepStatuses = {};
    _prevPlanStatus = null;
    updatePlanButtons();
    if (debugMode) startDebugPolling();
    showToast(`Plan loaded: ${r.steps} steps`, 'success');
  } catch (e) {
    showToast(`Load failed: ${e.message}`, 'error');
  }
}

async function doStartPlan() {
  try {
    const r = await apiPost('/plan/start');
    addPlanLog(r.status === 'resumed' ? 'Plan resumed' : 'Plan started', 'info');
    updatePlanButtons();
    showToast(r.status === 'resumed' ? 'Plan resumed' : 'Plan started', 'success');
  } catch (e) {
    showToast(`Start failed: ${e.message}`, 'error');
  }
}

async function doPausePlan() {
  try {
    await apiPost('/plan/pause');
    addPlanLog('Plan paused', 'warn');
    showToast('Plan paused', 'warn');
  } catch (e) {
    showToast(`Pause failed: ${e.message}`, 'error');
  }
}

async function doResetPlan() {
  try {
    await apiPost('/plan/reset');
    addPlanLog('Plan reset', 'info');
    _prevStepStatuses = {};
    _prevPlanStatus = null;
    _lastCompletedLogId = null;
    document.getElementById('btn-download-log').style.display = 'none';
    stopDebugPolling();
    document.getElementById('debug-pane').style.display = 'none';
    updatePlanButtons();
    showToast('Plan reset', 'info');
  } catch (e) {
    showToast(`Reset failed: ${e.message}`, 'error');
  }
}

async function doAckStep() {
  try {
    const r = await apiPost('/plan/ack');
    addPlanLog('Debug ACK sent — continuing', 'info');
    document.getElementById('debug-pane').style.display = 'none';
    showToast('Step acknowledged', 'success');
  } catch (e) {
    showToast(`ACK failed: ${e.message}`, 'error');
  }
}

function updatePlanButtons() {
  const plan = wsState?.plan;
  const btnStart = document.getElementById('btn-start');
  const btnPause = document.getElementById('btn-pause');
  const btnReset = document.getElementById('btn-reset');

  if (!plan) {
    btnStart.disabled = true;
    btnPause.disabled = true;
    btnReset.disabled = true;
    btnStart.textContent = 'Start';
    return;
  }

  btnStart.disabled = plan.status === 'running' || plan.status === 'completed';
  btnPause.disabled = plan.status !== 'running';
  btnReset.disabled = false;
  btnStart.textContent = plan.status === 'paused' ? 'Resume' : 'Start';
}

function renderPlanSteps() {
  const plan = wsState?.plan;
  const stepsEl = document.getElementById('plan-steps-list');
  const progressFill = document.getElementById('plan-progress-fill');
  const progressText = document.getElementById('plan-progress-text');

  if (!plan) {
    stepsEl.innerHTML = '<p class="empty-msg">Load a plan to begin.</p>';
    progressFill.style.width = '0%';
    progressText.textContent = '';
    updatePlanButtons();
    return;
  }

  const steps = plan.steps || [];
  const done = steps.filter(s => s.status === 'done').length;
  const failed = steps.filter(s => s.status === 'failed').length;
  const total = steps.length;
  const pct = total ? Math.round((done / total) * 100) : 0;

  progressFill.style.width = `${pct}%`;
  progressFill.style.background = failed ? 'var(--red)'
    : plan.status === 'completed' ? 'var(--green)' : '';
  progressText.textContent = `${done}/${total} steps${failed ? ` · ${failed} failed` : ''}`;

  stepsEl.innerHTML = steps.map(s => {
    const dur = s.duration_ms != null ? `${(s.duration_ms / 1000).toFixed(1)}s` : '';
    return `
      <div class="step-row ${s.status}">
        <span class="step-idx">${s.step_index}</span>
        <span class="step-name-cell">${escHtml(s.name)}</span>
        <span class="step-robot">R${s.robot_id}</span>
        <span class="badge badge-${s.status}">${statusIcon(s.status)} ${s.status.toUpperCase()}</span>
        <span class="step-dur">${dur}</span>
      </div>
    `;
  }).join('');

  updatePlanButtons();
}

function renderActiveOp() {
  const plan = wsState?.plan;
  const bodyEl = document.getElementById('active-op-body');

  if (!plan) {
    bodyEl.innerHTML = '<p class="empty-msg">No active operation.</p>';
    if (_elapsedInterval) { clearInterval(_elapsedInterval); _elapsedInterval = null; }
    return;
  }

  const step = (plan.steps || []).find(s => s.status === 'active');
  if (!step) {
    const last = [...(plan.steps || [])].reverse().find(s => s.status === 'done' || s.status === 'failed');
    if (last) {
      const isOk = last.status === 'done';
      bodyEl.innerHTML = `
        <div class="op-detail-grid">
          <span class="op-detail-key">Last</span>
          <span class="op-detail-val">${last.step_index} — ${escHtml(last.name)}</span>
          <span class="op-detail-key">Result</span>
          <span class="op-detail-val ${isOk ? 'ok' : 'err'}">${isOk ? 'SUCCESS' : 'FAILED (rc=' + last.result_code + ')'}</span>
          <span class="op-detail-key">Duration</span>
          <span class="op-detail-val">${last.duration_ms != null ? (last.duration_ms/1000).toFixed(2) + 's' : '—'}</span>
        </div>`;
    } else {
      bodyEl.innerHTML = '<p class="empty-msg">No active operation.</p>';
    }
    if (_elapsedInterval) { clearInterval(_elapsedInterval); _elapsedInterval = null; }
    return;
  }

  if (_elapsedInterval) { clearInterval(_elapsedInterval); _elapsedInterval = null; }

  const arr = step.parameter_array || [];
  const paramCells = arr.slice(0, 10).map((v, i) => `
    <div class="param-cell">
      <span class="pi">[${i+1}]</span>
      <span class="pv">${v}</span>
    </div>`).join('');

  const pos = step.position || {};
  const started = step.started_at ? new Date(step.started_at) : null;

  bodyEl.innerHTML = `
    <div class="op-detail-grid">
      <span class="op-detail-key">Step</span>
      <span class="op-detail-val">${step.step_index} — ${escHtml(step.name)}</span>
      <span class="op-detail-key">Operation</span>
      <span class="op-detail-val">op ${step.op_id} ${escHtml(step.op_name)}</span>
      <span class="op-detail-key">Robot</span>
      <span class="op-detail-val warn">R${step.robot_id} / T${step.tool_id} / Base ${step.base_id}</span>
      <span class="op-detail-key">Position</span>
      <span class="op-detail-val" style="font-size:10px" title="${escHtml(step.position_name||'')}">
        ${escHtml((step.position_name||'').replace('maze_106_',''))||'—'}
      </span>
      <span class="op-detail-key">TCP</span>
      <span class="op-detail-val" style="font-size:10px">
        X${pos.X?.toFixed(1)??'?'} Y${pos.Y?.toFixed(1)??'?'} Z${pos.Z?.toFixed(1)??'?'}
      </span>
      <span class="op-detail-key">Call ID</span>
      <span class="op-detail-val">${step.call_id ?? '—'}</span>
      <span class="op-detail-key">Elapsed</span>
      <span class="op-detail-val warn" id="elapsed-display">…</span>
    </div>
    <div class="params-grid">${paramCells}</div>
  `;

  if (started) {
    const elapsedEl = document.getElementById('elapsed-display');
    const tick = () => {
      if (elapsedEl) elapsedEl.textContent = ((Date.now() - started.getTime()) / 1000).toFixed(1) + 's';
    };
    tick();
    _elapsedInterval = setInterval(tick, 200);
  }
}

function renderWorkcellIndicators() {
  if (!wsState) return;
  const wc = wsState.workcell;
  const estopInd = document.getElementById('estop-ind');
  const conveyorInd = document.getElementById('conveyor-ind');
  estopInd.className = `indicator ${wc.estop_ok ? 'active-ind' : 'inactive-ind'}`;
  estopInd.innerHTML = `<span class="ind-dot"></span>E-Stop ${wc.estop_ok ? 'OK' : 'ACTIVE'}`;
  conveyorInd.className = `indicator ${wc.conveyor_running ? 'active-ind' : 'inactive-ind'}`;
  conveyorInd.innerHTML = `<span class="ind-dot"></span>Conveyor ${wc.conveyor_running ? 'Running' : 'Stopped'}`;
}

function addPlanLog(msg, level = '') {
  const logEl = document.getElementById('plan-log');
  const ts = new Date().toTimeString().slice(0, 8);
  const div = document.createElement('div');
  div.className = 'log-row';
  div.innerHTML = `<span class="log-ts">${ts}</span><span class="log-msg ${level}">${escHtml(msg)}</span>`;
  logEl.prepend(div);
  while (logEl.children.length > 120) logEl.lastChild.remove();
}

// ── Debug polling ─────────────────────────────────────────────────────────
function startDebugPolling() {
  if (_debugPollingInterval) clearInterval(_debugPollingInterval);
  _debugPollingInterval = setInterval(pollDebugStatus, 500);
}

function stopDebugPolling() {
  if (_debugPollingInterval) { clearInterval(_debugPollingInterval); _debugPollingInterval = null; }
}

async function pollDebugStatus() {
  try {
    const status = await apiGet('/debug/status');
    const debugPane = document.getElementById('debug-pane');
    if (status.debug_mode && status.paused_step !== null && status.paused_step !== undefined) {
      debugPane.style.display = '';
      document.getElementById('debug-pane-body').innerHTML = `
        <p class="muted-text" style="margin-bottom:8px">
          Paused on step <strong style="color:var(--yellow)">${status.paused_step}</strong>.
          Review the active operation above, then confirm to continue.
        </p>
        <button id="btn-ack" class="btn btn-success">Confirm Step (ACK)</button>
      `;
      document.getElementById('btn-ack').addEventListener('click', doAckStep);
    } else if (!status.debug_mode) {
      stopDebugPolling();
      debugPane.style.display = 'none';
    } else {
      debugPane.style.display = 'none';
    }
  } catch (e) {
    // silently ignore
  }
}

// ═════════════════════════════════════════════════════════════════════════
// PANEL 4: EXPORT
// ═════════════════════════════════════════════════════════════════════════
function initExportPanel() {
  document.getElementById('btn-export-plan').addEventListener('click', doExportPlan);
  document.getElementById('btn-refresh-plans').addEventListener('click', loadPlansList);
}

async function doExportPlan() {
  try {
    const r = await apiPost('/plans/export', {});
    const msgEl = document.getElementById('export-message');
    msgEl.style.display = '';
    msgEl.textContent = `Exported: ${r.plan_id} → ${r.path}`;
    showToast(`Plan exported: ${r.plan_id}`, 'success');
    loadPlansList();
  } catch (e) {
    showToast(`Export failed: ${e.message}`, 'error');
  }
}

async function loadPlansList() {
  try {
    const plans = await apiGet('/plans');
    renderPlansList(plans);
  } catch (e) {
    showToast(`Failed to load plans: ${e.message}`, 'error');
  }
}

function renderPlansList(plans) {
  const tbody = document.getElementById('plans-tbody');
  if (!plans.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-msg">No plans saved yet.</td></tr>';
    return;
  }
  tbody.innerHTML = plans.map(p => `
    <tr>
      <td style="font-weight:600;color:var(--blue)">${escHtml(p.plan_id)}</td>
      <td><span class="mode-badge mode-${p.mode}" style="font-size:10px;padding:1px 6px">${escHtml(p.mode)}</span></td>
      <td><span class="badge badge-${p.status}">${escHtml(p.status)}</span></td>
      <td>${p.step_count}</td>
      <td style="font-size:10px;color:var(--muted)">${formatDate(p.created_at)}</td>
      <td>
        <div class="action-cell">
          <button class="btn-sm" onclick="downloadPlan('${escHtml(p.plan_id)}')">Download</button>
          <button class="btn-sm train" onclick="queuePlanTraining('${escHtml(p.plan_id)}')">Train</button>
          <button class="btn-sm del" onclick="deletePlan('${escHtml(p.plan_id)}')">Delete</button>
        </div>
      </td>
    </tr>
  `).join('');
}

async function downloadPlan(planId) {
  try {
    const data = await apiGet(`/plans/${planId}`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${planId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    showToast(`Download failed: ${e.message}`, 'error');
  }
}

async function queuePlanTraining(planId) {
  try {
    const r = await apiPost(`/plans/${planId}/train`);
    showToast(`Queued for training: ${planId}`, 'success');
  } catch (e) {
    showToast(`Queue failed: ${e.message}`, 'error');
  }
}

async function deletePlan(planId) {
  if (!confirm(`Delete plan "${planId}"?`)) return;
  try {
    await apiDelete(`/plans/${planId}`);
    showToast(`Deleted: ${planId}`, 'success');
    loadPlansList();
  } catch (e) {
    showToast(`Delete failed: ${e.message}`, 'error');
  }
}

// ═════════════════════════════════════════════════════════════════════════
// PANEL 5: VISUALIZE
// ═════════════════════════════════════════════════════════════════════════
function initVisualizePanel() {
  document.getElementById('btn-refresh-diagram').addEventListener('click', loadDiagram);
  document.getElementById('btn-refresh-stats').addEventListener('click', loadStats);
  document.getElementById('btn-refresh-history').addEventListener('click', loadHistory);
}

async function loadDiagram() {
  const container = document.getElementById('diagram-container');
  container.innerHTML = '<p class="muted-text">Loading diagram...</p>';
  try {
    const r = await apiGet('/viz/diagram');
    if (!r.diagram) {
      container.innerHTML = '<p class="empty-msg">Load a plan to generate the sequence diagram.</p>';
      return;
    }
    try {
      const { svg } = await mermaid.render('mermaid-diagram-' + Date.now(), r.diagram);
      container.innerHTML = svg;
    } catch (e) {
      container.innerHTML = `<pre style="color:var(--muted);font-size:10px;white-space:pre-wrap">${escHtml(r.diagram)}</pre>`;
    }
  } catch (e) {
    container.innerHTML = `<p class="muted-text">Error: ${escHtml(e.message)}</p>`;
    showToast(`Diagram failed: ${e.message}`, 'error');
  }
}

async function loadStats() {
  const summaryEl = document.getElementById('stats-summary');
  summaryEl.innerHTML = '<span class="muted-text">Loading...</span>';
  try {
    const [r, logs] = await Promise.all([
      apiGet('/viz/stats'),
      apiGet('/viz/execution-logs').catch(() => []),
    ]);

    // Aggregate over execution logs for current run stats
    const execTotal = logs.reduce((a, l) => a + (l.summary?.total_steps || 0), 0);
    const execDone  = logs.reduce((a, l) => a + (l.summary?.completed || 0), 0);
    const execFail  = logs.reduce((a, l) => a + (l.summary?.failed || 0), 0);
    const successPct = execTotal ? Math.round((execDone / execTotal) * 100) : 0;

    summaryEl.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Execution Runs</div>
        <div class="stat-value">${logs.length}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Total Steps Run</div>
        <div class="stat-value">${execTotal}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Completed Steps</div>
        <div class="stat-value" style="color:var(--green)">${execDone}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Failed Steps</div>
        <div class="stat-value" style="color:var(--red)">${execFail}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Success Rate</div>
        <div class="stat-value" style="color:${successPct>=80?'var(--green)':successPct>=50?'var(--yellow)':'var(--red)'}">${successPct}%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Saved Plans</div>
        <div class="stat-value">${r.total_plans}</div>
      </div>
    `;

    // Robot utilization chart
    const utilLabels = Object.keys(r.robot_utilization);
    const utilData = Object.values(r.robot_utilization);
    if (_chartUtilization) { _chartUtilization.destroy(); _chartUtilization = null; }
    const ctxUtil = document.getElementById('chart-utilization').getContext('2d');
    _chartUtilization = new Chart(ctxUtil, {
      type: 'bar',
      data: {
        labels: utilLabels,
        datasets: [{
          label: 'Steps Assigned',
          data: utilData,
          backgroundColor: '#58a6ff55',
          borderColor: '#58a6ff',
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' }, beginAtZero: true },
        },
      },
    });

    // Avg score chart
    const scoreLabels = Object.keys(r.avg_score_by_robot);
    const scoreData = Object.values(r.avg_score_by_robot);
    if (_chartScores) { _chartScores.destroy(); _chartScores = null; }
    const ctxScores = document.getElementById('chart-scores').getContext('2d');
    _chartScores = new Chart(ctxScores, {
      type: 'bar',
      data: {
        labels: scoreLabels.length ? scoreLabels : ['No data'],
        datasets: [{
          label: 'Avg Success Rate',
          data: scoreData.length ? scoreData : [0],
          backgroundColor: '#3fb95055',
          borderColor: '#3fb950',
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' }, beginAtZero: true, max: 1 },
        },
      },
    });
  } catch (e) {
    summaryEl.innerHTML = `<span class="muted-text">Error: ${escHtml(e.message)}</span>`;
    showToast(`Stats failed: ${e.message}`, 'error');
  }
}

async function loadHistory() {
  const tbody = document.getElementById('history-tbody');
  tbody.innerHTML = '<tr><td colspan="7" class="muted-text" style="padding:12px 10px">Loading...</td></tr>';
  try {
    const logs = await apiGet('/viz/execution-logs');
    if (!logs.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">No execution logs yet. Run a plan to generate logs.</td></tr>';
      return;
    }
    tbody.innerHTML = logs.map((log, i) => {
      const summ = log.summary || {};
      const pct = summ.total_steps ? Math.round((summ.completed / summ.total_steps) * 100) : 0;
      const statusCls = log.status === 'completed' ? 'badge-done' : log.status === 'failed' ? 'badge-failed' : 'badge-pending';
      return `<tr>
        <td style="color:var(--muted);font-size:10px">${i + 1}</td>
        <td style="font-size:10px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(log.log_id)}">${escHtml(log.plan_id)}</td>
        <td>${escHtml(log.mode || '—')}</td>
        <td><span class="badge ${statusCls}">${escHtml(log.status || '—')}</span></td>
        <td>${summ.total_steps ?? '—'} / <span style="color:var(--green)">${summ.completed ?? 0}</span> / <span style="color:var(--red)">${summ.failed ?? 0}</span></td>
        <td style="font-size:10px;color:var(--muted)">${formatDate(log.started_at)}</td>
        <td>
          <button class="btn" style="font-size:10px;padding:2px 7px" onclick="downloadExecLog('${escHtml(log.log_id)}')">Download</button>
          <button class="btn" style="font-size:10px;padding:2px 7px;margin-left:4px" onclick="expandExecLog('${escHtml(log.log_id)}', this)">Steps</button>
        </td>
      </tr>
      <tr id="expand-${escHtml(log.log_id)}" style="display:none">
        <td colspan="7" style="padding:0 12px 12px"></td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted-text" style="padding:12px 10px">Error: ${escHtml(e.message)}</td></tr>`;
    showToast(`History failed: ${e.message}`, 'error');
  }
}

async function downloadExecLog(logId) {
  const a = document.createElement('a');
  a.href = `/viz/execution-logs/${encodeURIComponent(logId)}/download`;
  a.download = `${logId}.json`;
  a.click();
}

async function expandExecLog(logId, btn) {
  const row = document.getElementById(`expand-${logId}`);
  if (!row) return;
  if (row.style.display !== 'none') {
    row.style.display = 'none';
    btn.textContent = 'Steps';
    return;
  }
  btn.textContent = 'Hide';
  const td = row.querySelector('td');
  td.innerHTML = '<span class="muted-text">Loading...</span>';
  row.style.display = '';
  try {
    const log = await apiGet(`/viz/execution-logs/${encodeURIComponent(logId)}`);
    const steps = log.steps || [];
    if (!steps.length) { td.innerHTML = '<span class="muted-text">No steps.</span>'; return; }
    td.innerHTML = `<table class="data-table" style="margin:0">
      <thead><tr><th>#</th><th>Name</th><th>Robot</th><th>Tool</th><th>Op</th><th>Status</th><th>Duration</th><th>Error</th></tr></thead>
      <tbody>${steps.map(s => {
        const stCls = s.status === 'done' ? 'badge-done' : s.status === 'failed' ? 'badge-failed' : s.status === 'active' ? 'badge-active' : 'badge-pending';
        return `<tr>
          <td>${s.step_index}</td>
          <td style="font-size:10px">${escHtml(s.name || '')}</td>
          <td>R${s.robot_id}</td>
          <td>${s.tool_id ?? '—'}</td>
          <td style="font-size:10px">${escHtml(s.op_id || '')}</td>
          <td><span class="badge ${stCls}">${escHtml(s.status || '')}</span></td>
          <td>${s.duration_ms != null ? s.duration_ms + ' ms' : '—'}</td>
          <td style="font-size:10px;color:var(--red)">${escHtml(s.error_message || '')}</td>
        </tr>`;
      }).join('')}</tbody>
    </table>`;
  } catch (e) {
    td.innerHTML = `<span class="muted-text">Error: ${escHtml(e.message)}</span>`;
  }
}

// ═════════════════════════════════════════════════════════════════════════
// API HELPERS
// ═════════════════════════════════════════════════════════════════════════
async function apiGet(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(`${API}${path}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
}

// ═════════════════════════════════════════════════════════════════════════
// TOAST
// ═════════════════════════════════════════════════════════════════════════
function showToast(msg, level = 'info') {
  const container = document.getElementById('toast-container');
  const div = document.createElement('div');
  div.className = `toast ${level}`;
  div.textContent = msg;
  container.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

// ═════════════════════════════════════════════════════════════════════════
// UTILITIES
// ═════════════════════════════════════════════════════════════════════════
function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('sv-SE', { hour12: false }).replace('T', ' ');
  } catch { return iso; }
}

function statusIcon(status) {
  return { pending: '○', active: '◉', done: '✓', failed: '✗', skipped: '—' }[status] || '?';
}

// ═════════════════════════════════════════════════════════════════════════
// BOOT
// ═════════════════════════════════════════════════════════════════════════
initTabs();
initSeedPanel();
initStatePanel();
initGeneratePanel();
initPlanPanel();
initExportPanel();
initVisualizePanel();
connectWS();
