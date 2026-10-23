'use strict';

// Phone page for `jarvis web`. Ported from jarvis-app's main.js: the same
// chat, sessions drawer and permission requests, over JarvisTransport instead
// of Tauri. Settings, providers, widgets, voice and daemon shutdown stay on
// the desktop; the gateway refuses those messages anyway (docs/web-gateway.md).
const { invoke, listen } = window.JarvisTransport;

// ── State ──────────────────────────────────────────────────────────────────
let isConnected      = false;
let hasMessages      = false;
let currentSessionId = null;
let role             = 'guest';

// ── DOM refs ───────────────────────────────────────────────────────────────
const chatView        = document.getElementById('chat-view');
const emptyState      = document.getElementById('empty-state');
const messages        = document.getElementById('messages');
const messageInput    = document.getElementById('message-input');
const sendBtn         = document.getElementById('send-btn');
const stopBtn         = document.getElementById('stop-btn');
const statusDot       = document.getElementById('status-dot');
const statusLabel     = document.getElementById('status-label');
const who             = document.getElementById('who');
const notice          = document.getElementById('notice');
const sessionsBtn     = document.getElementById('sessions-btn');
const sessionBackdrop = document.getElementById('session-backdrop');
const sessionSidebar  = document.getElementById('session-sidebar');
const sessionList     = document.getElementById('session-list');
const newSessionBtn   = document.getElementById('new-session-btn');

const permissionsBtn       = document.getElementById('permissions-btn');
const permissionIndicator  = document.getElementById('permission-indicator');
const permissionCount      = document.getElementById('permission-count');
const permissionBackdrop   = document.getElementById('permission-backdrop');
const permissionPanel      = document.getElementById('permission-panel');
const permissionPanelTitle = document.getElementById('permission-panel-title');
const permissionList       = document.getElementById('permission-list');

let permissionFlashTimer = null;

const activityEl   = document.getElementById('activity');
const workBtn      = document.getElementById('work-btn');
const workBackdrop = document.getElementById('work-backdrop');
const workPanel    = document.getElementById('work-panel');
const workList     = document.getElementById('work-list');
const statusList   = document.getElementById('status-list');
const statusBtn    = document.getElementById('status-btn');

// Action blocks still running, by action id (only trusted and owner get them).
const actionBlocks = new Map();
let workTimer = null;

// ── Status ─────────────────────────────────────────────────────────────────
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

function setStatus(state) {
    if (state === 'idle') showActivity('');
    const color = STATE_COLOR[state] ?? STATE_COLOR.offline;
    statusDot.style.background = color;
    statusDot.style.color      = color;
    statusLabel.style.color    = color;
    statusLabel.textContent    = STATE_LABEL[state] ?? 'Offline';
    statusDot.classList.toggle('pulsing', state === 'processing' || state === 'speaking');
    document.body.classList.toggle('working', state === 'processing');
}

function setConnected(connected) {
    isConnected = connected;
    messageInput.disabled    = !connected;
    messageInput.placeholder = connected ? 'Message JARVIS…' : 'Connecting…';
    refreshSendBtn();
}

function refreshSendBtn() {
    sendBtn.disabled = !isConnected || messageInput.value.trim() === '';
}

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
    // An answer ends the turn; a comment only says what comes next (#273).
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


// ── What JARVIS is doing (#267) ───────────────────────────────────────────
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
        row.append(el('div', 'work-item-title', `⋯ ${t.title}`));
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
    invoke("list_confirmations");
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
    permissionCount.textContent = items.length ? String(items.length) : '';
    permissionPanelTitle.textContent = items.length
        ? `Permission Requests (${items.length})`
        : 'Permission Requests';

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

async function stopReply() {
    await invoke('stop_stream');
}

function applyGateway(info) {
    role = info.role;
    who.textContent = `${info.login} · ${info.role}`;
    // Only owner and trusted may answer a request; a guest watches and chats.
    document.body.classList.toggle('can-approve', role === 'owner' || role === 'trusted');
    document.body.classList.toggle('can-manage', role === 'owner');
}

function handleDisconnect(info) {
    setConnected(false);
    closeWorkPanel();
    actionBlocks.clear();
    showActivity('');
    setStatus('offline');
    if (info && info.reason) showNotice(info.reason);
}

// ── Boot ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    messageInput.addEventListener('input', refreshSendBtn);
    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener('click', sendMessage);
    stopBtn.addEventListener('click', stopReply);
    sessionsBtn.addEventListener('click', toggleSidebar);
    sessionBackdrop.addEventListener('click', closeSidebar);
    newSessionBtn.addEventListener('click', createNewSession);
    permissionsBtn.addEventListener('click', togglePermissionPanel);
    permissionBackdrop.addEventListener('click', closePermissionPanel);
    workBtn.addEventListener('click', () => (workPanel.classList.contains('visible') ? closeWorkPanel() : openWorkPanel()));
    workBackdrop.addEventListener('click', closeWorkPanel);
    // On a phone a panel covers the screen, so it needs its own way out.
    document.getElementById('work-close').addEventListener('click', closeWorkPanel);
    document.getElementById('permission-close').addEventListener('click', closePermissionPanel);
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        closeWorkPanel();
        closePermissionPanel();
        closeSidebar();
    });
    statusBtn.addEventListener('click', () => {
        statusList.textContent = 'Checking…';
        invoke('get_status');
    });

    await listen('ipc-connected', () => {
        setConnected(true);
        showNotice('');
        invoke('list_sessions');
        invoke('list_confirmations');
    });
    await listen('ipc-disconnected', (e) => handleDisconnect(e.payload));
    await listen('ipc-gateway',      (e) => applyGateway(e.payload));
    await listen('ipc-state',        (e) => setStatus(e.payload));
    await listen('ipc-chunk',        (e) => appendJarvisChunk(e.payload.content, e.payload.done, e.payload.comment));
    await listen('ipc-activity',     (e) => showActivity(e.payload.text));
    await listen('ipc-action',       (e) => onAction(e.payload));
    await listen('ipc-work',         (e) => renderWork(e.payload.work || {}));
    await listen('ipc-status',       (e) => renderStatus(e.payload.sections));
    await listen('ipc-confirm', () => {
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
        invoke('list_sessions');
    });
    await listen('ipc-session-error', (e) => showNotice('Session error: ' + e.payload));
    await listen('ipc-error',         (e) => showNotice(e.payload));
    await listen('ipc-daemon-shutdown', (e) => {
        addSystemMessage(`JARVIS shut down. Last state: ${e.payload.state ?? 'unknown'}.`);
        handleDisconnect(null);
    });

    setStatus('offline');
    window.JarvisTransport.connect();
});
