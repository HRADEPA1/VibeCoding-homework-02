'use strict';

// ── State ─────────────────────────────────────────────────────────────────
let state = null;
let ws = null;
const MAX_LOG_ENTRIES = 80;

// ── DOM refs ──────────────────────────────────────────────────────────────
const wsDot         = document.getElementById('ws-dot');
const modeBadge     = document.getElementById('mode-badge');
const planStatusEl  = document.getElementById('plan-status');
const modeSelect    = document.getElementById('mode-select');
const btnLoad       = document.getElementById('btn-load');
const btnStart      = document.getElementById('btn-start');
const btnPause      = document.getElementById('btn-pause');
const btnReset      = document.getElementById('btn-reset');
const robotsPanel   = document.getElementById('robots-panel');
const planPanel     = document.getElementById('plan-panel');
const activeOpEl    = document.getElementById('active-op');
const logBody       = document.getElementById('log-body');
const progressFill  = document.getElementById('progress-fill');
const progressText  = document.getElementById('progress-text');
const estopInd      = document.getElementById('estop-ind');
const conveyorInd   = document.getElementById('conveyor-ind');

// ── WebSocket ─────────────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    wsDot.classList.add('connected');
    logEntry('WebSocket connected', 'info');
  };

  ws.onmessage = (ev) => {
    try {
      const newState = JSON.parse(ev.data);
      detectChanges(newState);
      state = newState;
      render();
    } catch (e) {
      console.error('Parse error', e);
    }
  };

  ws.onclose = () => {
    wsDot.classList.remove('connected');
    logEntry('WebSocket disconnected — retrying in 3 s', 'warn');
    setTimeout(connectWS, 3000);
  };

  ws.onerror = () => ws.close();
}

// ── Render ────────────────────────────────────────────────────────────────
function render() {
  if (!state) return;
  renderHeader();
  renderRobots();
  renderPlan();
  renderActiveOp();
  renderWorkcell();
}

function renderHeader() {
  const plan = state.plan;
  const mode = plan ? plan.mode : (modeSelect.value || 'A');
  modeBadge.className = `mode-badge mode-${mode}`;
  modeBadge.textContent = `MODE ${mode}`;

  const status = plan ? plan.status : 'idle';
  planStatusEl.className = `plan-status ps-${status}`;
  planStatusEl.textContent = status.toUpperCase();

  btnStart.disabled = !plan || plan.status === 'running' || plan.status === 'completed';
  btnPause.disabled = !plan || plan.status !== 'running';
  btnReset.disabled = !plan;
  btnStart.textContent = plan && plan.status === 'paused' ? '▶ Resume' : '▶ Start';
}

function renderRobots() {
  const robots = state.workcell.robots || [];
  robotsPanel.innerHTML = '';
  for (const r of robots) {
    const busy  = r.program_busy;
    const err   = r.error_id !== 0;
    const dis   = r.manually_disabled || !r.enabled;

    let cardClass = 'robot-card';
    let dotClass  = 'status-dot';
    let statusStr = 'READY';
    if (dis)        { cardClass += ' disabled'; dotClass += ''; statusStr = 'DISABLED'; }
    else if (err)   { cardClass += ' error';    dotClass += ' error'; statusStr = 'ERROR'; }
    else if (busy)  { cardClass += ' busy';     dotClass += ' busy';  statusStr = 'BUSY'; }
    else            { dotClass += ' ready'; }

    const toolHtml = r.tool_attached
      ? `<span class="val ok">T${r.tool_id}</span>`
      : `<span class="val err">NONE</span>`;

    const errHtml = err
      ? `<span class="val err">ERR ${r.error_id}</span>`
      : `<span class="val ok">OK</span>`;

    const progHtml = r.program_busy
      ? `<span class="val warn">OP ${r.program_number} (ID ${r.call_id})</span>`
      : r.program_done
        ? `<span class="val ok">DONE (rc=${r.result_code})</span>`
        : `<span class="val">—</span>`;

    robotsPanel.insertAdjacentHTML('beforeend', `
      <div class="${cardClass}">
        <div class="robot-header">
          <span class="robot-id">R${r.robot_id}</span>
          <span style="font-size:10px;color:var(--muted)">${statusStr}</span>
          <span class="${dotClass}"></span>
        </div>
        <div class="robot-body">
          <div class="robot-row"><span class="label">Tool</span>${toolHtml}</div>
          <div class="robot-row"><span class="label">Base</span><span class="val">${r.base_frame_id}</span></div>
          <div class="robot-row"><span class="label">Error</span>${errHtml}</div>
          <div class="robot-row"><span class="label">Program</span>${progHtml}</div>
          <div class="tcp-row">
            <div>X <span>${r.tcp_x.toFixed(1)}</span></div>
            <div>Y <span>${r.tcp_y.toFixed(1)}</span></div>
            <div>Z <span>${r.tcp_z.toFixed(1)}</span></div>
            <div>A <span>${r.tcp_a.toFixed(1)}</span></div>
          </div>
        </div>
      </div>
    `);
  }
}

function renderPlan() {
  const plan = state.plan;
  if (!plan) {
    planPanel.innerHTML = '<p class="empty-msg">No plan loaded.<br>Select mode and click Load Plan.</p>';
    progressFill.style.width = '0%';
    progressText.innerHTML = '—';
    return;
  }

  const steps   = plan.steps || [];
  const done    = steps.filter(s => s.status === 'done').length;
  const failed  = steps.filter(s => s.status === 'failed').length;
  const total   = steps.length;
  const pct     = total ? Math.round((done / total) * 100) : 0;

  progressFill.style.width = `${pct}%`;
  if (failed) progressFill.style.background = 'var(--red)';
  else if (plan.status === 'completed') progressFill.style.background = 'var(--green)';
  else progressFill.style.background = '';

  progressText.innerHTML =
    `<span>${done}/${total} steps completed</span>` +
    (failed ? `<span style="color:var(--red)">${failed} failed</span>` : `<span>${pct}%</span>`);

  let rows = '';
  for (const s of steps) {
    const rowClass =
      s.status === 'active'  ? 'active-row' :
      s.status === 'done'    ? 'done-row'   :
      s.status === 'failed'  ? 'failed-row' : '';

    const badge = `<span class="badge badge-${s.status}">${statusIcon(s.status)} ${s.status.toUpperCase()}</span>`;

    const dur = s.duration_ms != null ? `${(s.duration_ms / 1000).toFixed(1)}s` : '';
    const pos = s.position_name ? s.position_name.replace('maze_106_', '').replace(/_/g, ' ') : '—';

    rows += `
      <tr class="${rowClass}">
        <td class="step-num">${s.step_index}</td>
        <td class="step-name">${s.name}</td>
        <td><span class="step-op">op ${s.op_id} ${s.op_name}</span></td>
        <td>R${s.robot_id}</td>
        <td>${badge}</td>
        <td class="duration-cell">${dur}</td>
      </tr>`;
  }

  planPanel.innerHTML = `
    <table class="plan-table">
      <thead>
        <tr>
          <th>#</th><th>Name</th><th>Operation</th><th>Robot</th><th>Status</th><th>Time</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderActiveOp() {
  const plan = state.plan;
  if (!plan) { activeOpEl.innerHTML = ''; return; }

  const step = (plan.steps || []).find(s => s.status === 'active');
  if (!step) {
    const last = [...(plan.steps || [])].reverse().find(s => s.status === 'done' || s.status === 'failed');
    if (last) {
      const isOk = last.status === 'done';
      activeOpEl.innerHTML = `
        <div class="active-op">
          <div class="active-op-title">Last Completed</div>
          <div class="active-op-body">
            <div class="op-row"><span class="label">Step</span><span class="val">${last.step_index} — ${last.name}</span></div>
            <div class="op-row"><span class="label">Result</span>
              <span class="val ${isOk ? 'ok' : 'err'}">${isOk ? '✓ SUCCESS' : '✗ FAILED (rc=' + last.result_code + ')'}</span>
            </div>
            <div class="op-row"><span class="label">Duration</span><span class="val">${last.duration_ms != null ? (last.duration_ms/1000).toFixed(2)+'s' : '—'}</span></div>
          </div>
        </div>`;
    } else {
      activeOpEl.innerHTML = '';
    }
    return;
  }

  // Build param display — show non-zero entries
  const arr = step.parameter_array || [];
  let paramCells = '';
  for (let i = 0; i < 9; i++) {
    paramCells += `
      <div class="param-cell">
        <span class="pi">[${i+1}]</span>
        <span class="pv">${arr[i] ?? 0}</span>
      </div>`;
  }

  const pos = step.position || {};
  const started = step.started_at ? new Date(step.started_at) : null;
  const elapsed = started ? ((Date.now() - started.getTime()) / 1000).toFixed(1) + 's' : '—';

  activeOpEl.innerHTML = `
    <div class="active-op" style="border-color:var(--yellow)">
      <div class="active-op-title" style="color:var(--yellow)">▶ Active Operation</div>
      <div class="active-op-body">
        <div class="op-row"><span class="label">Step</span><span class="val">${step.step_index} — ${step.name}</span></div>
        <div class="op-row"><span class="label">Operation</span><span class="val">op ${step.op_id} ${step.op_name}</span></div>
        <div class="op-row"><span class="label">Robot</span><span class="val">R${step.robot_id} / T${step.tool_id} / Base ${step.base_id}</span></div>
        <div class="op-row"><span class="label">Position</span><span class="val" title="${step.position_name}">${step.position_name ? step.position_name.replace('maze_106_','') : '—'}</span></div>
        <div class="op-row"><span class="label">TCP target</span>
          <span class="val" style="font-size:10px">
            X${pos.X?.toFixed(1)} Y${pos.Y?.toFixed(1)} Z${pos.Z?.toFixed(1)}
          </span>
        </div>
        <div class="op-row"><span class="label">Call ID</span><span class="val">${step.call_id ?? '—'}</span></div>
        <div class="op-row"><span class="label">Elapsed</span><span class="val warn" id="elapsed">${elapsed}</span></div>
        <div class="params-grid">${paramCells}</div>
      </div>
    </div>`;

  // Tick elapsed
  if (started) {
    const el = document.getElementById('elapsed');
    if (el) {
      setInterval(() => {
        const s = ((Date.now() - started.getTime()) / 1000).toFixed(1) + 's';
        el.textContent = s;
      }, 200);
    }
  }
}

function renderWorkcell() {
  const wc = state.workcell;
  const estopOk   = wc.estop_ok;
  const convRunning = wc.conveyor_running;

  estopInd.className   = `indicator ${estopOk ? 'active-ind' : 'inactive-ind'}`;
  estopInd.innerHTML   = `<span class="ind-dot"></span>E-Stop ${estopOk ? 'OK' : 'ACTIVE'}`;

  conveyorInd.className = `indicator ${convRunning ? 'active-ind' : 'inactive-ind'}`;
  conveyorInd.innerHTML = `<span class="ind-dot"></span>Conveyor ${convRunning ? 'Running' : 'Stopped'}`;
}

// ── Event log ─────────────────────────────────────────────────────────────
function logEntry(msg, cls = '') {
  const ts  = new Date().toTimeString().slice(0, 8);
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.innerHTML = `<span class="log-ts">${ts}</span><span class="log-msg ${cls}">${msg}</span>`;
  logBody.prepend(div);
  // Trim old entries
  while (logBody.children.length > MAX_LOG_ENTRIES) {
    logBody.lastChild.remove();
  }
}

// ── Watch state changes for log ───────────────────────────────────────────
let _prevStepStatuses = {};
let _prevPlanStatus = null;
let _prevEstop = null;

function detectChanges(newState) {
  const plan = newState.plan;

  if (plan) {
    if (plan.status !== _prevPlanStatus) {
      const cls =
        plan.status === 'completed' ? 'success' :
        plan.status === 'failed'    ? 'failure' :
        plan.status === 'running'   ? 'info'    : '';
      logEntry(`Plan ${plan.status.toUpperCase()}: ${plan.plan_id}`, cls);
      _prevPlanStatus = plan.status;
    }

    for (const step of (plan.steps || [])) {
      const prev = _prevStepStatuses[step.step_index];
      if (prev !== step.status) {
        if (step.status === 'active') {
          logEntry(`Step ${step.step_index}: ${step.name} → ACTIVE (op ${step.op_id})`, 'warn');
        } else if (step.status === 'done') {
          logEntry(`Step ${step.step_index}: ${step.name} → DONE (${(step.duration_ms/1000).toFixed(1)}s)`, 'success');
        } else if (step.status === 'failed') {
          logEntry(`Step ${step.step_index}: ${step.name} → FAILED rc=${step.result_code}`, 'failure');
        }
        _prevStepStatuses[step.step_index] = step.status;
      }
    }
  }

  const estop = newState.workcell.estop_ok;
  if (_prevEstop !== null && _prevEstop !== estop) {
    logEntry(`E-Stop: ${estop ? 'CLEARED' : 'ACTIVATED'}`, estop ? 'info' : 'failure');
  }
  _prevEstop = estop;
}

// ── API helpers ───────────────────────────────────────────────────────────
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    logEntry(`API error: ${err.detail || res.statusText}`, 'failure');
    throw new Error(err.detail);
  }
  return res.json();
}

// ── Event handlers ────────────────────────────────────────────────────────
btnLoad.addEventListener('click', async () => {
  const mode = modeSelect.value;
  logEntry(`Loading MAZE_106 plan (Mode ${mode})…`, 'info');
  try {
    const r = await api('POST', '/plan/load', { mode });
    logEntry(`Plan loaded: ${r.plan_id} — ${r.steps} steps`, 'success');
    _prevStepStatuses = {};
    _prevPlanStatus = null;
  } catch {}
});

btnStart.addEventListener('click', async () => {
  try {
    const r = await api('POST', '/plan/start');
    logEntry(r.status === 'resumed' ? 'Plan resumed' : 'Plan started', 'info');
  } catch {}
});

btnPause.addEventListener('click', async () => {
  try {
    await api('POST', '/plan/pause');
    logEntry('Plan paused', 'warn');
  } catch {}
});

btnReset.addEventListener('click', async () => {
  try {
    await api('POST', '/plan/reset');
    logEntry('Plan reset', 'info');
    _prevStepStatuses = {};
    _prevPlanStatus = null;
  } catch {}
});

estopInd.addEventListener('click', async () => {
  const current = state?.workcell.estop_ok ?? true;
  try {
    await api('POST', '/workcell/estop', { ok: !current });
  } catch {}
});

conveyorInd.addEventListener('click', async () => {
  const current = state?.workcell.conveyor_running ?? true;
  try {
    await api('POST', '/workcell/conveyor', { running: !current });
  } catch {}
});

// ── Helpers ───────────────────────────────────────────────────────────────
function statusIcon(status) {
  return { pending: '○', active: '◉', done: '✓', failed: '✗', skipped: '—' }[status] || '?';
}

// ── Boot ──────────────────────────────────────────────────────────────────
connectWS();
