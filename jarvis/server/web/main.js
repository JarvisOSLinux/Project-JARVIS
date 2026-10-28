'use strict';

// One page for jarvis-app and for `jarvis web` in a browser (Project-JARVIS
// serves a pinned copy of this folder). transport.js says which, and carries
// invoke/listen either way. Settings, providers, widgets, voice and daemon
// shutdown are the desktop's (`desktop-only` in index.html); in a browser the
// gateway's role decides what may be approved or managed.
const { invoke, listen } = window.JarvisTransport;
const IS_DESKTOP = window.JarvisTransport.kind === 'tauri';

// ── State ──────────────────────────────────────────────────────────────────
let isConnected     = false;
let isListening     = false;
let hasMessages     = false;
let currentSessionId = null;
// The desktop user owns this JARVIS; a browser learns its role from the gateway.
let role            = IS_DESKTOP ? 'owner' : 'guest';

// ── DOM refs ───────────────────────────────────────────────────────────────
const chatView        = document.getElementById('chat-view');
const emptyState      = document.getElementById('empty-state');
const messages        = document.getElementById('messages');
const messageInput    = document.getElementById('message-input');
const sendBtn         = document.getElementById('send-btn');
const micBtn          = document.getElementById('mic-btn');
const stopBtn         = document.getElementById('stop-btn');
const who             = document.getElementById('who');
const notice          = document.getElementById('notice');
const statusDot       = document.getElementById('status-dot');
const statusLabel     = document.getElementById('status-label');
const sessionsBtn     = document.getElementById('sessions-btn');
const sessionBackdrop = document.getElementById('session-backdrop');
const sessionSidebar  = document.getElementById('session-sidebar');
const sessionList     = document.getElementById('session-list');
const newSessionBtn   = document.getElementById('new-session-btn');

const permissionsBtn     = document.getElementById('permissions-btn');
const permissionIndicator = document.getElementById('permission-indicator');
const permissionCount    = document.getElementById('permission-count');
const permissionBackdrop = document.getElementById('permission-backdrop');
const permissionPanel    = document.getElementById('permission-panel');
const permissionPanelTitle = document.getElementById('permission-panel-title');
const permissionList     = document.getElementById('permission-list');
const approveAllBtn      = document.getElementById('approve-all-btn');

let permissionFlashTimer = null;

const settingsBtn        = document.getElementById('settings-btn');
const settingsBackdrop   = document.getElementById('settings-backdrop');
const settingsModal      = document.getElementById('settings-modal');
const settingsCloseBtn   = document.getElementById('settings-close-btn');
const settingsTabGeneralBtn   = document.getElementById('settings-tab-general-btn');
const settingsTabProvidersBtn = document.getElementById('settings-tab-providers-btn');
const settingsTabWidgetsBtn   = document.getElementById('settings-tab-widgets-btn');
const settingsTabGeneral      = document.getElementById('settings-tab-general');
const settingsTabProviders    = document.getElementById('settings-tab-providers');
const settingsTabWidgets      = document.getElementById('settings-tab-widgets');
const settingsVoiceToggle     = document.getElementById('settings-voice-toggle');
const settingsConfirmationMode = document.getElementById('settings-confirmation-mode');
const settingsWakeChimePath   = document.getElementById('settings-wake-chime-path');
const settingsChooseChimeBtn  = document.getElementById('settings-choose-chime-btn');
const settingsResetChimeBtn   = document.getElementById('settings-reset-chime-btn');
const settingsSocketPath      = document.getElementById('settings-socket-path');
const settingsQuitOnShutdown  = document.getElementById('settings-quit-on-shutdown');
const settingsShutdownBtn     = document.getElementById('settings-shutdown-btn');
const settingsError           = document.getElementById('settings-error');

const shutdownBackdrop  = document.getElementById('shutdown-backdrop');
const shutdownModal     = document.getElementById('shutdown-modal');
const shutdownClientList = document.getElementById('shutdown-client-list');
const shutdownCancelBtn  = document.getElementById('shutdown-cancel-btn');
const shutdownConfirmBtn = document.getElementById('shutdown-confirm-btn');

const providerList          = document.getElementById('provider-list');
const settingsAddProviderBtn = document.getElementById('settings-add-provider-btn');
const providerForm           = document.getElementById('provider-form');
const providerFormType        = document.getElementById('provider-form-type');
const providerFormModel       = document.getElementById('provider-form-model');
const providerFormName        = document.getElementById('provider-form-name');
const providerFormUrl         = document.getElementById('provider-form-url');
const providerFormTemperature = document.getElementById('provider-form-temperature');
const providerFormKey         = document.getElementById('provider-form-key');
const providerFormError       = document.getElementById('provider-form-error');
const providerFormCancelBtn   = document.getElementById('provider-form-cancel-btn');
const providerFormSaveBtn     = document.getElementById('provider-form-save-btn');

let cachedProviders = [];
let editingProviderName = null;

const widgetList = document.getElementById('widget-list');

const activityEl   = document.getElementById('activity');
const workBtn      = document.getElementById('work-btn');
const workBackdrop = document.getElementById('work-backdrop');
const workPanel    = document.getElementById('work-panel');
const workList     = document.getElementById('work-list');
const statusList   = document.getElementById('status-list');
const statusBtn    = document.getElementById('status-btn');

// Action blocks still running, by action id.
const actionBlocks = new Map();
let workTimer = null;

// ── Status ─────────────────────────────────────────────────────────────────
// woken/capturing come from the daemon's formal voice state machine
// (Project-JARVIS#141) -- both render like the existing "listening" state
// since, from the user's point of view, the mic is active either way.
const STATE_COLOR = {
    idle:       '#00c8ff',
    woken:      '#00ff88',
    capturing:  '#00ff88',
    listening:  '#00ff88',
    processing: '#ffaa00',
    speaking:   '#aa44ff',
    offline:    '#ff4455',
};

const STATE_LABEL = {
    idle:       'Idle',
    woken:      'Listening',
    capturing:  'Listening',
    listening:  'Listening',
    processing: 'Working',
    speaking:   'Speaking',
    offline:    'Offline',
};

const LISTENING_LIKE_STATES = new Set(['listening', 'woken', 'capturing']);

function setStatus(state) {
    if (state === 'idle' || state === 'offline') showActivity('');
    const color = STATE_COLOR[state] ?? STATE_COLOR.offline;
    statusDot.style.background   = color;
    statusDot.style.color        = color;
    statusLabel.style.color      = color;
    statusLabel.textContent      = STATE_LABEL[state] ?? 'Offline';

    const pulsing = LISTENING_LIKE_STATES.has(state) || state === 'processing' || state === 'speaking';
    statusDot.classList.toggle('pulsing', pulsing);
    document.body.classList.toggle('working', state === 'processing');

    isListening = LISTENING_LIKE_STATES.has(state);
    micBtn.classList.toggle('listening', isListening);
    renderSettingsVoiceToggle();
}

function setConnected(connected) {
    isConnected = connected;
    messageInput.disabled    = !connected;
    messageInput.placeholder = connected ? 'Message JARVIS…' : 'Connecting…';
    if (!connected) {
        micBtn.classList.remove('listening');
        isListening = false;
        renderSettingsVoiceToggle();
    }
    refreshSendBtn();
}

function refreshSendBtn() {
    sendBtn.disabled = !isConnected || messageInput.value.trim() === '';
}

/// A line above the input for what went wrong; a click dismisses it.
function showNotice(text) {
    notice.textContent = text;
    notice.classList.toggle('hidden', !text);
}

// ── Messages ───────────────────────────────────────────────────────────────
function timestamp() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function showMessages() {
    if (!hasMessages) {
        hasMessages = true;
        emptyState.style.display = 'none';
    }
}

function addUserMessage(content) {
    showMessages();
    foldOlderBlocks(null);
    const el = document.createElement('div');
    el.className = 'message user-message';

    const col = document.createElement('div');
    col.className = 'bubble-col align-right';

    const bubble = document.createElement('div');
    bubble.className = 'bubble user-bubble';
    bubble.textContent = content;

    const ts = document.createElement('div');
    ts.className = 'timestamp right';
    ts.textContent = timestamp();

    col.append(bubble, ts);
    el.append(col);
    messages.append(el);
    requestAnimationFrame(() => el.classList.add('visible'));
    scrollBottom();
}

const STREAM_CURSOR = ' <span class="stream-cursor">▌</span>';

function appendJarvisChunk(content, done, comment) {
    showMessages();
    const streaming = messages.querySelector('.jarvis-message.streaming');
    // An answer ends the turn; a comment only says what comes next.
    if (done && !comment) showActivity('');

    if (streaming) {
        const textEl = streaming.querySelector('.bubble-text');
        const accumulated = (textEl.dataset.raw ?? '') + content;
        textEl.dataset.raw = accumulated;
        textEl.innerHTML   = renderMarkdown(accumulated) + (done ? '' : STREAM_CURSOR);
        if (done) streaming.classList.remove('streaming');
    } else {
        foldOlderBlocks(null);
        const el = document.createElement('div');
        el.className = 'message jarvis-message' + (done ? '' : ' streaming')
            + (comment ? ' comment' : '');

        const avatar = document.createElement('div');
        avatar.className = 'avatar';
        avatar.textContent = 'J';

        const col = document.createElement('div');
        col.className = 'bubble-col';

        const bubble = document.createElement('div');
        bubble.className = 'bubble jarvis-bubble';

        const textEl = document.createElement('div');
        textEl.className   = 'bubble-text markdown-content';
        textEl.dataset.raw = content;
        textEl.innerHTML   = renderMarkdown(content) + (done ? '' : STREAM_CURSOR);

        const ts = document.createElement('div');
        ts.className   = 'timestamp';
        ts.textContent = timestamp();

        bubble.append(textEl);
        col.append(bubble, ts);
        el.append(avatar, col);
        messages.append(el);
        requestAnimationFrame(() => el.classList.add('visible'));
    }
    scrollBottom();
}

function scrollBottom() {
    requestAnimationFrame(() => { chatView.scrollTop = chatView.scrollHeight; });
}

// A lightweight, chat-local annotation for events with no daemon reply of
// their own (e.g. a DAEMON_SHUTDOWN notice) -- "recorded into session
// history" in spirit, without needing a round-trip write to a daemon that's
// already tearing itself down (Project-JARVIS#146 / jarvisos-app#17).
function addSystemMessage(text) {
    showMessages();
    const el = document.createElement('div');
    el.className = 'system-message';
    el.textContent = text;
    messages.append(el);
    scrollBottom();
}

// ── Sessions ───────────────────────────────────────────────────────────────
function sessionLabel(session) {
    return session.title && session.title.trim() ? session.title : `Chat ${session.id.slice(0, 8)}`;
}

function formatSessionDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
        ? iso
        : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function openSidebar() {
    sessionSidebar.classList.add('visible');
    sessionBackdrop.classList.add('visible');
}

function closeSidebar() {
    sessionSidebar.classList.remove('visible');
    sessionBackdrop.classList.remove('visible');
}

function toggleSidebar() {
    sessionSidebar.classList.contains('visible') ? closeSidebar() : openSidebar();
}

function renderSessionList(sessions) {
    sessionList.innerHTML = '';
    if (!sessions.length) {
        const empty = document.createElement('div');
        empty.className = 'session-empty';
        empty.textContent = 'No sessions yet.';
        sessionList.append(empty);
        return;
    }
    for (const session of sessions) {
        sessionList.append(buildSessionItem(session));
    }
}

function buildSessionItem(session) {
    const item = document.createElement('div');
    item.className = 'session-item' + (session.id === currentSessionId ? ' active' : '');
    item.dataset.id = session.id;

    const main = document.createElement('div');
    main.className = 'session-item-main';

    const title = document.createElement('div');
    title.className = 'session-item-title';
    title.textContent = sessionLabel(session);

    const meta = document.createElement('div');
    meta.className = 'session-item-meta';
    const count = session.entry_count != null ? `${session.entry_count} msg` : '';
    meta.textContent = [formatSessionDate(session.updated_at || session.created_at), count]
        .filter(Boolean)
        .join(' · ');

    main.append(title, meta);

    const renameBtn = document.createElement('button');
    renameBtn.className = 'session-item-action';
    renameBtn.title = 'Rename';
    renameBtn.textContent = '✎';
    renameBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        startRename(title, session);
    });

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'session-item-action';
    deleteBtn.title = 'Delete';
    deleteBtn.textContent = '×';
    deleteBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        confirmDeleteSession(session);
    });

    const actions = document.createElement('div');
    actions.className = 'session-item-actions';
    actions.append(renameBtn, deleteBtn);

    item.append(main, actions);
    item.addEventListener('click', () => switchToSession(session.id));
    return item;
}

function startRename(titleEl, session) {
    const input = document.createElement('input');
    input.className = 'session-item-title-input';
    input.value = session.title || '';
    input.addEventListener('click', (e) => e.stopPropagation());

    let settled = false;
    const restore = () => { if (input.parentElement) input.replaceWith(titleEl); };

    const commit = async () => {
        if (settled) return;
        settled = true;
        const value = input.value.trim();
        if (value && value !== session.title) {
            // A successful rename broadcasts session_list, which rebuilds the
            // sidebar -- no need to manually restore titleEl in that case.
            await invoke('rename_session', { id: session.id, title: value });
        } else {
            restore();
        }
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { settled = true; restore(); }
    });
    input.addEventListener('blur', commit);

    titleEl.replaceWith(input);
    input.focus();
    input.select();
}

async function confirmDeleteSession(session) {
    if (!window.confirm(`Delete "${sessionLabel(session)}"? This can't be undone.`)) return;
    await invoke('delete_session', { id: session.id });
}

async function switchToSession(id) {
    if (id === currentSessionId) { closeSidebar(); return; }
    await invoke('switch_session', { id });
    closeSidebar();
}

async function createNewSession() {
    await invoke('create_session', { title: null });
    closeSidebar();
}

function restoreSessionMessages(session, msgs) {
    currentSessionId = session.id;
    messages.innerHTML = '';
    actionBlocks.clear();
    hasMessages = false;
    emptyState.style.display = '';
    for (const m of msgs) {
        if (m.role === 'user') addUserMessage(m.content);
        else appendJarvisChunk(m.content, true);
    }
    sessionList.querySelectorAll('.session-item').forEach((el) => {
        el.classList.toggle('active', el.dataset.id === currentSessionId);
    });
}

// ── What JARVIS is doing (Project-JARVIS#267, #271) ─────────────────────────
function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

function duration(seconds) {
    if (seconds == null) return '';
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return m < 60 ? `${m}m ${String(s % 60).padStart(2, '0')}s`
                  : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/// The line above the input: what JARVIS is doing at this moment. Empty text
/// says how many tasks still run, or hides the line.
function showActivity(text) {
    if (!text) {
        let running = 0;
        for (const block of actionBlocks.values()) if (block.kind === 'tool') running++;
        text = running ? `${running} task${running === 1 ? '' : 's'} still running` : '';
    }
    activityEl.textContent = text ? `⋯ ${text}` : '';
    activityEl.classList.toggle('hidden', !text);
}

const ACTION_MARK = {
    running:  '⋯',
    ok:       '✓',
    failed:   '✗ failed',
    stopped:  '■ stopped',
    waiting:  '… waiting for you',
    approved: '✓ approved',
    denied:   '✗ denied',
};

function setMark(markEl, status) {
    markEl.textContent = ACTION_MARK[status] ?? status;
    markEl.className = 'action-mark ' + status;
}

/// A block folds once something newer arrives, unless the user opened or
/// closed it (#271), the same rule as the TUI.
function foldOlderBlocks(newest) {
    for (const block of messages.querySelectorAll('details.action-block')) {
        if (block !== newest && !block.dataset.touched) block.open = false;
    }
}

function onAction(ev) {
    if (ev.phase === 'start') {
        showMessages();
        const block = el('details', 'action-block action-' + (ev.kind || 'action'));
        block.open = true;
        const summary = el('summary');
        const mark = el('span');
        const title = el('span', 'action-title', ev.title || '');
        const time = el('span', 'action-time');
        summary.append(mark, title, time);
        summary.addEventListener('click', () => { block.dataset.touched = '1'; });
        const detail = el('div', 'action-detail', ev.detail || '');
        detail.hidden = !ev.detail;
        const output = el('pre', 'action-output');
        output.hidden = true;
        block.append(summary, detail, output);
        setMark(mark, 'running');
        foldOlderBlocks(block);
        messages.append(block);
        actionBlocks.set(ev.id, { kind: ev.kind, mark, time, output });
        scrollBottom();
        return;
    }
    const block = actionBlocks.get(ev.id);
    if (!block) return; // started before this page connected
    if (ev.output) {
        block.output.textContent = ev.output;
        block.output.hidden = false;
        block.output.scrollTop = block.output.scrollHeight;
    }
    if (ev.phase === 'end') {
        actionBlocks.delete(ev.id);
        setMark(block.mark, ev.status || 'ok');
        if (ev.started_at && ev.at) block.time.textContent = duration(ev.at - ev.started_at);
    }
}

function openWorkPanel() {
    workPanel.classList.add('visible');
    workBackdrop.classList.add('visible');
    statusList.innerHTML = '';
    invoke('list_work');
    clearInterval(workTimer);
    workTimer = setInterval(() => invoke('list_work'), 2000);
}

function closeWorkPanel() {
    workPanel.classList.remove('visible');
    workBackdrop.classList.remove('visible');
    clearInterval(workTimer);
    workTimer = null;
}

function workSection(title, rows, empty) {
    const section = el('div', 'work-section');
    section.append(el('div', 'work-section-title', title));
    if (!rows.length) section.append(el('div', 'work-empty', empty));
    for (const row of rows) section.append(row);
    return section;
}

function renderWork(work) {
    const now = Date.now() / 1000;
    const requests = (work.requests || []).map((r) => {
        const row = el('div', 'work-item');
        row.append(el('div', 'work-item-title', r.text));
        if (r.since) row.append(el('div', 'work-item-meta', duration(now - r.since) + ' ago'));
        return row;
    });
    if (work.thinking) {
        requests.push(el('div', 'work-item-meta', `${work.thinking} waiting on the model`));
    }
    const tasks = (work.tasks || []).map((t) => {
        const row = el('div', 'work-item');
        if (t.kind === 'timer') {
            row.append(el('div', 'work-item-title', `⏲ ${t.title}`));
            row.append(el('div', 'work-item-meta', `fires in ${duration(t.fires_in)}`));
            return row;
        }
        const head = el('div', 'work-item-head');
        head.append(el('div', 'work-item-title', `⋯ ${t.title}`));
        // Kills this task only; a package manager asks for a second press (#270).
        const stopTask = el('button', 'task-stop-btn', 'Stop');
        stopTask.title = 'End this task';
        stopTask.addEventListener('click', async () => {
            await invoke('stop_task', { pid: t.pid });
            invoke('list_work');
        });
        head.append(stopTask);
        row.append(head);
        const meta = [t.running_for != null ? `running ${duration(t.running_for)}` : '', t.request ? `for: ${t.request}` : '']
            .filter(Boolean).join(' · ');
        if (meta) row.append(el('div', 'work-item-meta', meta));
        if (t.detail) row.append(el('div', 'action-detail', t.detail));
        const lines = String(t.output || '').split('\n').filter((l) => l.trim()).slice(-6);
        if (lines.length) row.append(el('pre', 'action-output', lines.join('\n')));
        return row;
    });
    const waiting = [];
    if (work.confirmations) waiting.push(el('div', 'work-item-title', `${work.confirmations} permission request${work.confirmations === 1 ? '' : 's'}`));
    if (work.setups) waiting.push(el('div', 'work-item-title', `${work.setups} server setup${work.setups === 1 ? '' : 's'} (on the host)`));
    workList.innerHTML = '';
    workList.append(
        workSection('Requests', requests, 'None'),
        workSection('Running', tasks, 'Nothing'),
        workSection('Waiting on you', waiting, 'Nothing'),
    );
}

const STATUS_MARK = { ok: '✓', warn: '!', fail: '✗', off: '–' };

function renderStatus(sections) {
    statusList.innerHTML = '';
    for (const section of sections || []) {
        const rows = (section.checks || []).map((c) => {
            const row = el('div', 'status-check');
            row.append(el('span', 'status-mark ' + c.state, STATUS_MARK[c.state] ?? '?'));
            row.append(el('span', 'status-name', c.name));
            if (c.detail) row.append(el('span', 'status-detail', c.detail));
            return row;
        });
        statusList.append(workSection(section.title, rows, ''));
    }
}

// ── Permission requests ────────────────────────────────────────────────────
function openPermissionPanel() {
    permissionPanel.classList.add('visible');
    permissionBackdrop.classList.add('visible');
    invoke('list_confirmations');
}

function closePermissionPanel() {
    permissionPanel.classList.remove('visible');
    permissionBackdrop.classList.remove('visible');
}

function togglePermissionPanel() {
    permissionPanel.classList.contains('visible') ? closePermissionPanel() : openPermissionPanel();
}

function flashPermissionIndicator() {
    clearTimeout(permissionFlashTimer);
    permissionIndicator.classList.remove('flash');
    void permissionIndicator.offsetWidth; // restart the fade if it's already flashing
    permissionIndicator.classList.add('flash');
    permissionFlashTimer = setTimeout(() => permissionIndicator.classList.remove('flash'), 2500);
}

function formatPermissionDate(epochSeconds) {
    if (!epochSeconds) return '';
    const d = new Date(epochSeconds * 1000);
    return Number.isNaN(d.getTime())
        ? ''
        : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function buildPermissionItem(item) {
    const el = document.createElement('div');
    el.className = 'permission-item';
    el.dataset.id = item.id;

    // tool_lines carries the rendered command per task; tool_names is the
    // fallback the daemon sends when it has no rendered form.
    const lines = (item.tool_lines && item.tool_lines.length)
        ? item.tool_lines
        : (item.tool_names || []);

    const meta = document.createElement('div');
    meta.className = 'permission-item-meta';
    meta.textContent = formatPermissionDate(item.created_at);

    const actions = document.createElement('div');
    actions.className = 'permission-item-actions';

    const approveBtn = document.createElement('button');
    approveBtn.className = 'permission-action approve';

    const denyBtn = document.createElement('button');
    denyBtn.className = 'permission-action deny';
    denyBtn.textContent = 'Deny';
    denyBtn.addEventListener('click', () => resolveConfirmation(item.id, false));

    if (lines.length <= 1) {
        const tools = document.createElement('div');
        tools.className = 'permission-item-tools';
        tools.textContent = lines.length ? lines[0] : 'Unknown tool';
        approveBtn.textContent = 'Approve';
        approveBtn.addEventListener('click', () => resolveConfirmation(item.id, true));
        actions.append(approveBtn, denyBtn);
        el.append(tools, meta, actions);
        return el;
    }

    // A batch: let the user pick which tasks run. All start selected, so the
    // default gesture stays whole-batch approve and nothing is a surprise.
    const list = document.createElement('div');
    list.className = 'permission-item-tasks';
    const boxes = [];
    lines.forEach((line, index) => {
        const row = document.createElement('label');
        row.className = 'permission-task';

        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = true;
        box.addEventListener('change', syncApprove);
        boxes.push(box);

        const text = document.createElement('span');
        text.className = 'permission-task-text';
        text.textContent = line;

        row.append(box, text);
        list.append(row);
    });

    function selectedIndices() {
        return boxes.map((b, i) => (b.checked ? i : -1)).filter((i) => i >= 0);
    }

    function syncApprove() {
        const picked = selectedIndices();
        approveBtn.disabled = picked.length === 0;
        approveBtn.textContent = picked.length === boxes.length
            ? `Approve all ${boxes.length}`
            : `Approve ${picked.length} of ${boxes.length}`;
    }

    approveBtn.addEventListener('click', () => {
        const picked = selectedIndices();
        if (!picked.length) return;
        if (picked.length === boxes.length) {
            resolveConfirmation(item.id, true);
        } else {
            partialApproveConfirmation(item.id, picked);
        }
    });
    syncApprove();

    actions.append(approveBtn, denyBtn);
    el.append(list, meta, actions);
    return el;
}

function renderPermissionList(items) {
    permissionList.innerHTML = '';
    permissionPanelTitle.textContent = items.length
        ? `Permission Requests (${items.length})`
        : 'Permission Requests';
    approveAllBtn.disabled = items.length === 0;
    permissionCount.textContent = items.length ? String(items.length) : '';

    if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'permission-empty';
        empty.textContent = 'No pending requests.';
        permissionList.append(empty);
        return;
    }
    for (const item of items) permissionList.append(buildPermissionItem(item));
}

async function resolveConfirmation(id, approved) {
    await invoke(approved ? 'approve_confirmation' : 'deny_confirmation', { id });
    await invoke('list_confirmations');
}

/// Approve only the checked tasks; the daemon denies the rest of that batch.
async function partialApproveConfirmation(id, approvedIndices) {
    await invoke('partial_approve_confirmation', { id, approvedIndices });
    await invoke('list_confirmations');
}

async function approveAllConfirmations() {
    await invoke('approve_all_confirmations');
    await invoke('list_confirmations');
}

// ── Settings ───────────────────────────────────────────────────────────────
function clearSettingsError() {
    settingsError.textContent = '';
    settingsError.classList.remove('visible');
}

function showSettingsError(message) {
    settingsError.textContent = message;
    settingsError.classList.add('visible');
}

function switchSettingsTab(tab) {
    settingsTabGeneralBtn.classList.toggle('active', tab === 'general');
    settingsTabProvidersBtn.classList.toggle('active', tab === 'providers');
    settingsTabWidgetsBtn.classList.toggle('active', tab === 'widgets');
    settingsTabGeneral.classList.toggle('active', tab === 'general');
    settingsTabProviders.classList.toggle('active', tab === 'providers');
    settingsTabWidgets.classList.toggle('active', tab === 'widgets');
}

function renderSettingsVoiceToggle() {
    settingsVoiceToggle.textContent = isListening ? 'Enabled' : 'Disabled';
    settingsVoiceToggle.classList.toggle('disabled', !isListening);
}

async function openSettingsModal() {
    settingsModal.classList.add('visible');
    settingsBackdrop.classList.add('visible');
    clearSettingsError();
    switchSettingsTab('general');
    renderSettingsVoiceToggle();
    await invoke('get_settings');
    await invoke('list_providers');
    settingsSocketPath.textContent = await invoke('get_connection_info');
    settingsQuitOnShutdown.checked = await invoke('get_quit_on_daemon_shutdown');
    await refreshWidgetList();
}

function closeSettingsModal() {
    settingsModal.classList.remove('visible');
    settingsBackdrop.classList.remove('visible');
    closeProviderForm();
}

function toggleSettingsModal() {
    settingsModal.classList.contains('visible') ? closeSettingsModal() : openSettingsModal();
}

function applySettings(settings) {
    settingsConfirmationMode.value = settings.confirmation_mode;
    settingsWakeChimePath.textContent = settings.wake_chime_path;
}

async function chooseWakeChimeFile() {
    const path = await invoke('pick_wake_chime_file');
    if (!path) return; // user cancelled the native picker
    clearSettingsError();
    await invoke('set_wake_chime_path', { path });
}

// ── Providers ──────────────────────────────────────────────────────────────
function providerLabel(p) {
    const temp = p.temperature != null ? `, temp ${p.temperature}` : '';
    return `${p.type}/${p.model}${temp}`;
}

function buildProviderItem(p, index) {
    const el = document.createElement('div');
    el.className = 'provider-item';

    const main = document.createElement('div');
    main.className = 'provider-item-main';
    const name = document.createElement('div');
    name.className = 'provider-item-name';
    name.textContent = p.name || '(unnamed)';
    const meta = document.createElement('div');
    meta.className = 'provider-item-meta';
    meta.textContent = providerLabel(p);
    main.append(name, meta);

    const editBtn = document.createElement('button');
    editBtn.className = 'provider-item-action';
    editBtn.title = 'Edit';
    editBtn.textContent = '✎';
    editBtn.addEventListener('click', () => openProviderForm(p));

    const removeBtn = document.createElement('button');
    removeBtn.className = 'provider-item-action';
    removeBtn.title = 'Remove';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => removeProvider(p.name));

    const actions = document.createElement('div');
    actions.className = 'provider-item-actions';
    if (index > 0) {
        // JARVIS tries providers in this order; up moves one earlier.
        const upBtn = document.createElement('button');
        upBtn.className = 'provider-item-action';
        upBtn.title = 'Try earlier';
        upBtn.textContent = '↑';
        upBtn.addEventListener('click', () => invoke('move_provider', { name: p.name, position: index }));
        actions.append(upBtn);
    }
    actions.append(editBtn, removeBtn);

    el.append(main, actions);
    return el;
}

function renderProviderList(providers) {
    cachedProviders = providers;
    providerList.innerHTML = '';
    if (!providers.length) {
        const empty = document.createElement('div');
        empty.className = 'provider-empty';
        empty.textContent = 'No providers configured.';
        providerList.append(empty);
        return;
    }
    providers.forEach((p, index) => providerList.append(buildProviderItem(p, index)));
}

function openProviderForm(existing) {
    editingProviderName = existing ? existing.name : null;
    providerFormError.textContent = '';
    providerFormError.classList.remove('visible');
    providerFormType.value = existing?.type || 'ollama';
    providerFormModel.value = existing?.model || '';
    providerFormName.value = existing?.name || '';
    providerFormUrl.value = existing?.url || '';
    providerFormTemperature.value = existing?.temperature ?? '';
    providerFormKey.value = '';
    providerFormSaveBtn.textContent = existing ? 'Save' : 'Add';
    providerForm.classList.remove('hidden');
    settingsAddProviderBtn.classList.add('hidden');
}

function closeProviderForm() {
    editingProviderName = null;
    providerForm.classList.add('hidden');
    settingsAddProviderBtn.classList.remove('hidden');
}

function showProviderFormError(message) {
    providerFormError.textContent = message;
    providerFormError.classList.add('visible');
}

async function saveProviderForm() {
    const model = providerFormModel.value.trim();
    if (!model) return showProviderFormError('Model is required.');

    const ptype = providerFormType.value;
    const apiKey = providerFormKey.value.trim();
    if (ptype === 'api' && !apiKey && !editingProviderName) {
        return showProviderFormError('API key is required for cloud providers.');
    }

    let temperature = null;
    const tempStr = providerFormTemperature.value.trim();
    if (tempStr) {
        temperature = Number(tempStr);
        if (Number.isNaN(temperature) || temperature < 0 || temperature > 2) {
            return showProviderFormError('Temperature must be a number between 0.0 and 2.0.');
        }
    }

    const url = providerFormUrl.value.trim();
    const name = providerFormName.value.trim();

    if (editingProviderName) {
        const fields = { model, type: ptype };
        if (url) fields.url = url;
        if (apiKey) fields.key = apiKey;
        if (temperature !== null) fields.temperature = temperature;
        await invoke('edit_provider', { name: editingProviderName, fields });
    } else {
        await invoke('add_provider', {
            ptype,
            model,
            name: name || null,
            url: url || null,
            apiKey: apiKey || null,
            temperature,
        });
    }
}

async function removeProvider(name) {
    await invoke('remove_provider', { name });
}

// ── Widgets (jarvisos-app#18, #16) ──────────────────────────────────────────
function trustBadgeClass(trustStatus) {
    if (trustStatus === 'verified') return 'trust-badge trust-verified';
    if (trustStatus === 'community') return 'trust-badge trust-community';
    return 'trust-badge trust-unreviewed';
}

function buildWidgetItem(w) {
    const el = document.createElement('div');
    el.className = 'widget-item';

    const header = document.createElement('div');
    header.className = 'widget-item-header';

    const name = document.createElement('div');
    name.className = 'widget-item-name';
    name.textContent = w.name;

    const badge = document.createElement('span');
    badge.className = trustBadgeClass(w.trustStatus);
    badge.textContent = w.trustStatus;

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'widget-toggle-btn' + (w.enabled ? '' : ' disabled');
    toggleBtn.textContent = w.enabled ? 'Enabled' : 'Disabled';
    toggleBtn.addEventListener('click', () => setWidgetEnabled(w.id, !w.enabled));

    header.append(name, badge, toggleBtn);

    const desc = document.createElement('div');
    desc.className = 'widget-item-desc';
    desc.textContent = w.description || '';

    const meta = document.createElement('div');
    meta.className = 'widget-item-meta';
    meta.textContent = w.source === 'installed' ? 'Installed' : 'Bundled';

    const actions = document.createElement('div');
    actions.className = 'settings-row';
    const customizeBtn = document.createElement('button');
    customizeBtn.textContent = 'Customize appearance…';
    customizeBtn.addEventListener('click', () => customizeWidgetAppearance(w.id));
    const resetBtn = document.createElement('button');
    resetBtn.textContent = 'Reset appearance';
    resetBtn.addEventListener('click', () => resetWidgetAppearance(w.id));
    actions.append(customizeBtn, resetBtn);

    el.append(header, desc, meta, actions);
    return el;
}

function renderWidgetList(widgets) {
    widgetList.innerHTML = '';
    if (!widgets.length) {
        const empty = document.createElement('div');
        empty.className = 'provider-empty';
        empty.textContent = 'No widgets found.';
        widgetList.append(empty);
        return;
    }
    for (const w of widgets) widgetList.append(buildWidgetItem(w));
}

async function refreshWidgetList() {
    renderWidgetList(await invoke('list_widgets'));
}

async function setWidgetEnabled(id, enabled) {
    await invoke('set_widget_enabled', { id, enabled });
    await refreshWidgetList();
}

async function customizeWidgetAppearance(id) {
    const picked = await invoke('pick_widget_appearance_folder', { id });
    if (!picked) return; // user cancelled, or the folder had no manifest.json
    await refreshWidgetList();
}

async function resetWidgetAppearance(id) {
    await invoke('reset_widget_appearance', { id });
    await refreshWidgetList();
}

// ── Daemon shutdown (Project-JARVIS#146 / jarvisos-app#17) ──────────────────
async function openShutdownModal() {
    shutdownClientList.innerHTML = 'Loading&hellip;';
    shutdownModal.classList.add('visible');
    shutdownBackdrop.classList.add('visible');
    await invoke('list_clients');
}

function closeShutdownModal() {
    shutdownModal.classList.remove('visible');
    shutdownBackdrop.classList.remove('visible');
}

function renderShutdownClientList(clients) {
    shutdownClientList.innerHTML = '';
    if (!clients.length) {
        const empty = document.createElement('div');
        empty.className = 'shutdown-client-item';
        empty.textContent = 'No other clients connected.';
        shutdownClientList.append(empty);
        return;
    }
    for (const label of clients) {
        const item = document.createElement('div');
        item.className = 'shutdown-client-item';
        item.textContent = label;
        shutdownClientList.append(item);
    }
}

async function confirmShutdown() {
    closeShutdownModal();
    await invoke('request_daemon_shutdown');
}

function formatShutdownTimestamp(epochSeconds) {
    if (!epochSeconds) return '';
    const d = new Date(epochSeconds * 1000);
    return Number.isNaN(d.getTime())
        ? ''
        : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function handleDaemonShutdown(payload) {
    closeShutdownModal();
    const when = formatShutdownTimestamp(payload.timestamp);
    addSystemMessage(
        `JARVIS shut down${when ? ' at ' + when : ''}. Last state: ${payload.state}.`
    );
    // Reuses the same disconnected treatment ipc-disconnected drives --
    // the daemon is already tearing its sockets down, so the real
    // disconnect will follow within moments regardless; this just makes
    // the UI reflect it immediately instead of lagging behind.
    setConnected(false);
    setStatus('offline');
}

// ── Stop (Project-JARVIS#270) ────────────────────────────────────────────────
// The daemon answers each stop with an `ack` saying what stopped and what
// still runs; acks for other requests (a confirmation decided) stay quiet.
const STOP_COMMANDS = new Set(['stop', 'stop_all', 'stop_task']);

/// Stops JARVIS's work in the session; its running tasks keep running.
async function stopReply() {
    await invoke('stop');
}

function onAck(ack) {
    if (STOP_COMMANDS.has(ack.for) && ack.message) addSystemMessage(ack.message);
}

// ── Connection ─────────────────────────────────────────────────────────────
/// The gateway's word on who this browser is (`jarvis web` only).
function applyGateway(info) {
    role = info.role;
    who.textContent = `${info.login} · ${info.role}`;
    // Only owner and trusted may answer a request; a guest watches and chats.
    document.body.classList.toggle('can-approve', role === 'owner' || role === 'trusted');
    document.body.classList.toggle('can-manage', role === 'owner');
}

function handleDisconnect(info) {
    setConnected(false);
    setStatus('offline');
    closeWorkPanel();
    actionBlocks.clear();
    showActivity('');
    if (info && info.reason) showNotice(info.reason);
}

function closePanels() {
    closeWorkPanel();
    closePermissionPanel();
    closeSidebar();
    closeSettingsModal();
    closeShutdownModal();
}

// ── Actions ────────────────────────────────────────────────────────────────
async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || !isConnected) return;
    messageInput.value = '';
    refreshSendBtn();
    addUserMessage(text);
    setStatus('processing');
    await invoke('send_message', { content: text });
}

async function toggleListening() {
    await invoke('toggle_listening');
}

// ── Boot ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    document.body.classList.add(IS_DESKTOP ? 'desktop' : 'web');
    if (IS_DESKTOP) document.body.classList.add('can-approve', 'can-manage');

    messageInput.addEventListener('input', refreshSendBtn);

    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener('click', sendMessage);
    micBtn.addEventListener('click', toggleListening);
    stopBtn.addEventListener('click', stopReply);
    notice.addEventListener('click', () => showNotice(''));
    // On a phone a panel covers the screen, so each has its own way out.
    document.getElementById('permission-close').addEventListener('click', closePermissionPanel);
    document.getElementById('stop-all-btn').addEventListener('click', () => invoke('stop_all'));
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePanels();
    });
    sessionsBtn.addEventListener('click', toggleSidebar);
    sessionBackdrop.addEventListener('click', closeSidebar);
    newSessionBtn.addEventListener('click', createNewSession);
    permissionsBtn.addEventListener('click', togglePermissionPanel);
    permissionBackdrop.addEventListener('click', closePermissionPanel);
    approveAllBtn.addEventListener('click', approveAllConfirmations);

    settingsBtn.addEventListener('click', toggleSettingsModal);
    settingsBackdrop.addEventListener('click', closeSettingsModal);
    settingsCloseBtn.addEventListener('click', closeSettingsModal);
    settingsTabGeneralBtn.addEventListener('click', () => switchSettingsTab('general'));
    settingsTabProvidersBtn.addEventListener('click', () => switchSettingsTab('providers'));
    settingsTabWidgetsBtn.addEventListener('click', () => switchSettingsTab('widgets'));
    settingsVoiceToggle.addEventListener('click', toggleListening);
    settingsConfirmationMode.addEventListener('change', () => {
        clearSettingsError();
        invoke('set_confirmation_mode', { mode: settingsConfirmationMode.value });
    });
    settingsChooseChimeBtn.addEventListener('click', chooseWakeChimeFile);
    settingsResetChimeBtn.addEventListener('click', () => {
        clearSettingsError();
        invoke('reset_wake_chime_path');
    });
    settingsAddProviderBtn.addEventListener('click', () => openProviderForm(null));
    providerFormCancelBtn.addEventListener('click', closeProviderForm);
    providerFormSaveBtn.addEventListener('click', saveProviderForm);

    settingsShutdownBtn.addEventListener('click', openShutdownModal);
    shutdownBackdrop.addEventListener('click', closeShutdownModal);
    shutdownCancelBtn.addEventListener('click', closeShutdownModal);
    shutdownConfirmBtn.addEventListener('click', confirmShutdown);
    settingsQuitOnShutdown.addEventListener('change', () => {
        invoke('set_quit_on_daemon_shutdown', { enabled: settingsQuitOnShutdown.checked });
    });

    workBtn.addEventListener('click', () => (workPanel.classList.contains('visible') ? closeWorkPanel() : openWorkPanel()));
    workBackdrop.addEventListener('click', closeWorkPanel);
    document.getElementById('work-close').addEventListener('click', closeWorkPanel);
    statusBtn.addEventListener('click', () => {
        statusList.textContent = 'Checking…';
        invoke('get_health', { full: false });
    });

    // IPC events from Rust backend
    await listen('ipc-connected',    ()      => {
        setConnected(true);
        showNotice('');
        invoke('list_sessions');
        invoke('list_confirmations');
    });
    await listen('ipc-disconnected', (e)     => handleDisconnect(e.payload));
    await listen('ipc-gateway',      (e)     => applyGateway(e.payload));
    await listen('ipc-ack',          (e)     => onAck(e.payload));
    await listen('ipc-state',        (e)     => setStatus(e.payload));
    await listen('ipc-chunk',        (e)     => appendJarvisChunk(e.payload.content, e.payload.done, e.payload.comment));
    await listen('ipc-activity',     (e)     => showActivity(e.payload));
    await listen('ipc-action',       (e)     => onAction(e.payload));
    await listen('ipc-work',         (e)     => renderWork(e.payload || {}));
    await listen('ipc-health',       (e)     => renderStatus(e.payload));
    await listen('ipc-wake',         ()      => setStatus('listening'));
    await listen('ipc-confirm', () => {
        // Non-blocking by design: no window.confirm(), no toast. Just a
        // subtle cue that the Permission Requests panel has something new.
        flashPermissionIndicator();
        invoke('list_confirmations');
    });
    await listen('ipc-confirmation-list', (e) => renderPermissionList(e.payload));
    await listen('ipc-session-list', (e) => {
        renderSessionList(e.payload);
        // A guest may not switch sessions; it follows the host's current one.
        if (currentSessionId === null && e.payload.length > 0 && role !== 'guest') {
            switchToSession(e.payload[0].id);
        }
    });
    await listen('ipc-session-switched', (e) => {
        restoreSessionMessages(e.payload.session, e.payload.messages);
        // create_session/switch_session only reply with session_switched, not
        // a refreshed list -- pull one explicitly so a newly created session
        // (or updated_at reordering) shows up in the sidebar immediately.
        invoke('list_sessions');
    });
    await listen('ipc-session-error', (e) => showNotice('Session error: ' + e.payload));
    await listen('ipc-error', (e) => showNotice(e.payload));
    await listen('ipc-settings', (e) => applySettings(e.payload));
    await listen('ipc-provider-list', (e) => {
        renderProviderList(e.payload);
        closeProviderForm(); // a refreshed list means the pending add/edit/remove succeeded
    });
    await listen('ipc-provider-error', (e) => showProviderFormError(e.payload));
    await listen('ipc-client-list', (e) => renderShutdownClientList(e.payload));
    await listen('ipc-daemon-shutdown', (e) => handleDaemonShutdown(e.payload));
    await listen('ipc-open-shutdown-modal', openShutdownModal); // tray menu entry point
    await listen('ipc-config-updated', (e) => {
        if (e.payload.key === 'CONFIRMATION_MODE' || e.payload.key === 'WAKE_CHIME_PATH') {
            clearSettingsError();
            invoke('get_settings');
        }
    });
    await listen('ipc-config-error', (e) => {
        if (e.payload.key === 'CONFIRMATION_MODE' || e.payload.key === 'WAKE_CHIME_PATH') {
            showSettingsError(e.payload.message);
        }
    });

    if (!IS_DESKTOP) {
        setStatus('offline');
        window.JarvisTransport.connect();
        return;
    }

    // Listeners are now registered, but the backend's IPC poll thread may have
    // already emitted ipc-connected/ipc-state before this point (Tauri does not
    // queue events for late listeners). Pull current truth to reconcile.
    const status = await invoke('get_status');
    setConnected(status.connected);
    setStatus(status.state);
    if (status.connected) {
        await invoke('list_sessions');
        await invoke('list_confirmations');
    }
});
