'use strict';

// One page for jarvis-app and for `jarvis web` in a browser (Project-JARVIS
// serves a pinned copy of this folder). transport.js says which, and carries
// invoke/listen either way. In a browser the gateway's role decides what may
// be approved or managed; the owner's phone is the app (`manage-only` in
// index.html), less what belongs to the laptop itself: widgets, choosing a
// file, the socket and shutting JARVIS down (`desktop-only`).
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
const attachBtn       = document.getElementById('attach-btn');
const attachInput     = document.getElementById('attach-input');
const attachmentsRow  = document.getElementById('attachments');
const commandPopup    = document.getElementById('command-popup');
const dropOverlay     = document.getElementById('drop-overlay');
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
const settingsCatalog         = document.getElementById('settings-catalog');
const settingsSocketPath      = document.getElementById('settings-socket-path');
const settingsQuitOnShutdown  = document.getElementById('settings-quit-on-shutdown');
const settingsShutdownBtn     = document.getElementById('settings-shutdown-btn');
const settingsError           = document.getElementById('settings-error');

const approvalBackdrop  = document.getElementById('approval-backdrop');
const approvalModal     = document.getElementById('approval-modal');
const approvalBody      = document.getElementById('approval-body');
const approvalGoal      = document.getElementById('approval-goal');
const approvalCount     = document.getElementById('approval-count');
const approvalLaterBtn  = document.getElementById('approval-later-btn');
const passwordBackdrop  = document.getElementById('password-backdrop');
const passwordModal     = document.getElementById('password-modal');
const passwordForm      = document.getElementById('password-form');
const passwordCommand   = document.getElementById('password-command');
const passwordAttempt   = document.getElementById('password-attempt');
const passwordInput     = document.getElementById('password-input');
const passwordPrompt    = document.getElementById('password-prompt');
const passwordError     = document.getElementById('password-error');
const passwordSubmitBtn = document.getElementById('password-submit-btn');
const passwordCancelBtn = document.getElementById('password-cancel-btn');
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
const providerFormVision      = document.getElementById('provider-form-vision');
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

/// Send takes text or a finished attachment, and waits for every upload.
function refreshSendBtn() {
    const text = messageInput.value.trim();
    sendBtn.disabled = !isConnected || (text === '' && !readyAttachments().length)
        || (uploading() && !lineCommand(parseCommandLine(text)));
}

/// A line above the input for what went wrong; a click dismisses it.
function showNotice(text) {
    notice.textContent = text;
    notice.classList.toggle('hidden', !text);
}

// ── Messages ───────────────────────────────────────────────────────────────
/// When a message was said: `at` is the daemon's stored time in seconds, or
/// now for a live one. An older day gets its date, so a restored chat does
/// not read as if it all happened a minute ago.
function timestamp(at) {
    const d = at ? new Date(at * 1000) : new Date();
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    if (d.toDateString() === new Date().toDateString()) return time;
    return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

/// One of the line icons in index.html's sprite (#i-<name>).
function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
}

function showMessages() {
    if (!hasMessages) {
        hasMessages = true;
        emptyState.style.display = 'none';
    }
}

/// `files`: what went with it, as `{name, kind, size}`.
function addUserMessage(content, at, files = []) {
    showMessages();
    foldOlderBlocks(null);
    const el = document.createElement('div');
    el.className = 'message user-message';
    el.dataset.text = content + (files.length ? `\nAttached: ${files.map((f) => f.name).join(', ')}` : '');

    const col = document.createElement('div');
    col.className = 'bubble-col align-right';

    const bubble = document.createElement('div');
    bubble.className = 'bubble user-bubble';
    if (content) bubble.append(content);
    if (files.length) {
        const row = document.createElement('div');
        row.className = 'bubble-files';
        for (const f of files) row.append(fileChip(f));
        bubble.append(row);
    }

    const ts = document.createElement('div');
    ts.className = 'timestamp right';
    ts.textContent = timestamp(at);

    col.append(bubble, ts);
    el.append(col);
    messages.append(el);
    requestAnimationFrame(() => el.classList.add('visible'));
    scrollBottom();
}

const STREAM_CURSOR = ' <span class="stream-cursor">▌</span>';

/// A reply bubble around `textEl`, at the end of the chat.
function addJarvisBubble(textEl, className, at) {
    foldOlderBlocks(null);
    const el = document.createElement('div');
    el.className = 'message jarvis-message' + className;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = 'J';

    const col = document.createElement('div');
    col.className = 'bubble-col';

    const bubble = document.createElement('div');
    bubble.className = 'bubble jarvis-bubble';

    const ts = document.createElement('div');
    ts.className   = 'timestamp';
    ts.textContent = timestamp(at);

    bubble.append(textEl);
    col.append(bubble, ts);
    el.append(avatar, col);
    messages.append(el);
    requestAnimationFrame(() => el.classList.add('visible'));
}

/// What a command answered, here or from JARVIS: its rows and tables as
/// written, since markdown ran the /sessions rows into one line.
function addCommandReply(text, at) {
    showMessages();
    const pre = document.createElement('pre');
    pre.className = 'bubble-text command-reply';
    pre.dataset.raw = text;
    pre.textContent = text;
    addJarvisBubble(pre, '', at);
    scrollBottom();
}

function appendJarvisChunk(content, done, comment, at, command) {
    showMessages();
    const streaming = messages.querySelector('.jarvis-message.streaming');
    // An answer ends the turn; a comment only says what comes next.
    if (done && !comment) showActivity('');
    if (command) {
        if (content) addCommandReply(content, at);
        return;
    }

    if (streaming) {
        const textEl = streaming.querySelector('.bubble-text');
        const accumulated = (textEl.dataset.raw ?? '') + content;
        textEl.dataset.raw = accumulated;
        textEl.innerHTML   = renderMarkdown(accumulated) + (done ? '' : STREAM_CURSOR);
        if (done) streaming.classList.remove('streaming');
    } else if (content) {
        // An empty done chunk closes a stream; with none open it has nothing to close.
        const textEl = document.createElement('div');
        textEl.className   = 'bubble-text markdown-content';
        textEl.dataset.raw = content;
        textEl.innerHTML   = renderMarkdown(content) + (done ? '' : STREAM_CURSOR);
        addJarvisBubble(textEl, (done ? '' : ' streaming') + (comment ? ' comment' : ''), at);
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

// A drawer follows a finger pushing it back off the edge it came from
// (side -1: left, 1: right) and closes past 60px; a mostly vertical drag is
// left to scroll the list.
function swipeToClose(panel, close, side) {
    let startX = null, startY = 0, dx = 0, dragging = false;
    const end = () => {
        startX = null;
        panel.style.transition = '';
        panel.style.transform = '';
        if (dragging && Math.abs(dx) > 60) close();
        dragging = false;
    };
    panel.addEventListener('touchstart', (e) => {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        dx = 0;
    }, { passive: true });
    panel.addEventListener('touchmove', (e) => {
        if (startX === null) return;
        const x = e.touches[0].clientX - startX;
        const y = e.touches[0].clientY - startY;
        if (!dragging) {
            if (Math.abs(y) > 10 && Math.abs(y) > Math.abs(x)) { startX = null; return; }
            if (Math.abs(x) < 10) return;
            dragging = true;
        }
        dx = side < 0 ? Math.min(0, x) : Math.max(0, x);
        panel.style.transition = 'none';
        panel.style.transform = `translateX(${dx}px)`;
    }, { passive: true });
    panel.addEventListener('touchend', end);
    panel.addEventListener('touchcancel', end);
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
    renameBtn.append(icon('pencil'));
    renameBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        startRename(title, session);
    });

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'session-item-action';
    deleteBtn.title = 'Delete';
    deleteBtn.append(icon('trash'));
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

/// The chat view only: JARVIS keeps the session, and running blocks still update.
function clearChat() {
    messages.innerHTML = '';
    hasMessages = false;
    emptyState.style.display = '';
}

/// `session` null: the chat shown was deleted, and JARVIS is in no session.
function restoreSessionMessages(session, msgs) {
    currentSessionId = session ? session.id : null;
    clearChat();
    actionBlocks.clear();
    for (const m of msgs) {
        if (m.role === 'user') addUserMessage(m.content, m.timestamp);
        else if (m.role === 'action') restoreActionBlock(m);
        else appendJarvisChunk(m.content, true, !!m.comment, m.timestamp);
    }
    showActivity('');
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
    exited:   '■ exited',  // ran, printed nothing, exit code ≠ 0: its answer
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
        // What ran, on the folded line too (the command, not just the tool).
        const brief = el('span', 'action-brief', (ev.detail || '').split('\n')[0]);
        const time = el('span', 'action-time');
        summary.append(mark, title, brief, time);
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

/// A block from a session's history, drawn as its start and end. One still
/// running stays open, and its next update or end, sent live, finds it.
function restoreActionBlock(m) {
    onAction({ phase: 'start', id: m.id, kind: m.kind, title: m.title, detail: m.detail });
    if (m.status === 'running') {
        onAction({ phase: 'update', id: m.id, output: m.output });
    } else {
        onAction({ phase: 'end', id: m.id, status: m.status, output: m.output,
                   started_at: m.started_at, at: m.at });
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
        head.append(el('div', 'work-item-title', `${t.awaiting_password ? '⏳' : '⋯'} ${t.title}`));
        // Kills this task only; a package manager asks for a second press (#270).
        const stopTask = el('button', 'task-stop-btn', 'Stop');
        stopTask.title = 'End this task';
        stopTask.addEventListener('click', async () => {
            await invoke('stop_task', { pid: t.pid });
            invoke('list_work');
        });
        head.append(stopTask);
        row.append(head);
        if (t.awaiting_password) {
            row.append(el('div', 'work-item-alert', 'Waiting for the administrator password on the host computer'));
        }
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

// What the panel shows: Approve All approves these and no request that came in
// after the list was drawn.
let shownConfirmationIds = [];

// ── A permission request as it arrives ──────────────────────────────────────
// Each request the page has not shown yet opens as a pop-up for those who may
// approve: the same card as the panel, plus Later, which leaves it waiting in
// the panel. It takes no focus, so Enter typed in the chat never approves.
const approvalSeen = new Set(); // ids popped up on this page
let approvalShownId = null;
let pendingConfirmations = [];

function syncApprovalPopup(items) {
    if (approvalShownId !== null && !items.some((i) => i.id === approvalShownId)) {
        closeApprovalPopup(); // answered here or on another screen
    }
    if (approvalShownId !== null || !document.body.classList.contains('can-approve')) return;
    const next = items.find((i) => !approvalSeen.has(i.id));
    if (next) openApprovalPopup(next, items);
}

function openApprovalPopup(item, items) {
    approvalSeen.add(item.id);
    approvalShownId = item.id;
    approvalGoal.textContent = item.goal_description ? `For: ${item.goal_description}` : '';
    const waiting = items.length - 1;
    approvalCount.textContent = waiting > 0 ? `${waiting} more waiting` : '';
    approvalBody.replaceChildren(buildPermissionItem(item));
    approvalModal.classList.add('visible');
    approvalBackdrop.classList.add('visible');
    if (navigator.vibrate) {
        try { navigator.vibrate(150); } catch (_) { /* not allowed before a tap */ }
    }
}

function closeApprovalPopup() {
    approvalShownId = null;
    approvalModal.classList.remove('visible');
    approvalBackdrop.classList.remove('visible');
    approvalBody.replaceChildren();
}

/// Leave it in the panel and show the next one not seen yet.
function laterApproval() {
    closeApprovalPopup();
    syncApprovalPopup(pendingConfirmations);
}

function renderPermissionList(items) {
    shownConfirmationIds = items.map((item) => item.id);
    pendingConfirmations = items;
    syncApprovalPopup(items);
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
    await invoke('approve_all_confirmations', { ids: shownConfirmationIds });
    await invoke('list_confirmations');
}

// ── Settings ───────────────────────────────────────────────────────────────
const OFFLINE_CHANGE = 'Not connected to JARVIS, so nothing changed. Try again once it reconnects.';
const LOST_CHANGE = 'The connection dropped before JARVIS answered. Check this change once it reconnects.';
const ENDED_CHANGE = 'This session has ended. Reload the page to connect again, then check this change.';

// What is waiting on JARVIS's answer, so a refusal or a dropped connection can
// be shown where the change was made.
let pendingSetting = null;  // the setting's key
let pendingProvider = null; // 'form', 'list' for the list's own buttons, or 'command' (/providers)
let providerDone = null;    // what a /providers change says once JARVIS takes it
let editAfterList = null;   // /providers edit <name>: open its form once the list is in
let sessionEnded = false;   // the gateway closed it on purpose; no reconnect follows

function settingsOpen() {
    return settingsModal.classList.contains('visible');
}

function clearSettingsError() {
    settingsError.textContent = '';
    settingsError.classList.remove('visible');
}

function showSettingsError(message) {
    settingsError.textContent = message;
    settingsError.classList.add('visible');
}

/// The Settings window covers the notice on a phone, so while it is open an
/// error goes in the window.
function showChangeError(message) {
    if (settingsOpen()) showSettingsError(message);
    else showNotice(message);
}

function offlineChange() {
    return sessionEnded ? ENDED_CHANGE : OFFLINE_CHANGE;
}

function restorePendingSetting() {
    if (pendingSetting !== null) putBackSetting(pendingSetting);
    pendingSetting = null;
}

function onError(message) {
    if (passwordSent) return showPasswordError(message);
    if (pendingProvider === 'form') showProviderFormError(message);
    else if (pendingProvider === 'command') addCommandReply(`Error: ${message}`);
    else showChangeError(message);
    pendingProvider = null;
    editAfterList = null;
    restorePendingSetting();
}

function loseChangesInFlight() {
    const message = sessionEnded ? ENDED_CHANGE : LOST_CHANGE;
    if (pendingProvider === 'form') showProviderFormError(message);
    else if (pendingProvider !== null || pendingSetting !== null) showChangeError(message);
    pendingProvider = null;
    restorePendingSetting();
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

// JARVIS's settings, from the list it sends (the same one the TUI's settings
// window draws). Each control sets its value in place, so a reply to one
// change does not rebuild the panel under the user.
const settingControls = new Map(); // key -> function that shows a value
const settingValues = new Map();   // key -> what JARVIS last said it runs with
let settingsDrawn = '';

function showSetting(key, value) {
    settingValues.set(key, value);
    settingControls.get(key)?.(value);
}

/// Back to what JARVIS runs with, even in a field still being typed in.
function putBackSetting(key) {
    settingControls.get(key)?.(settingValues.get(key), true);
}

function renderSettings(list) {
    const keys = list.map((s) => s.key).join(' ');
    if (keys !== settingsDrawn) drawSettings(list);
    settingsDrawn = keys;
    for (const s of list) showSetting(s.key, s.value);
}

function drawSettings(list) {
    const advancedOpen = settingsCatalog.querySelector('details')?.open ?? false;
    settingControls.clear();
    settingsCatalog.replaceChildren();
    let group = null;
    let section = null;
    for (const s of list) {
        if (s.section !== section) {
            section = s.section;
            if (section === 'Advanced') {
                group = el('details', 'settings-group');
                group.open = advancedOpen;
                group.append(el('summary', 'settings-group-title', section));
            } else {
                group = el('div', 'settings-group');
                group.append(el('div', 'settings-group-title', section));
            }
            settingsCatalog.append(group);
        }
        group.append(settingField(s));
    }
}

function settingField(s) {
    const field = el('div', 'settings-section');
    const label = el('label', 'settings-label', s.label);
    label.htmlFor = 'setting-' + s.key;
    let control;
    let show;
    if (s.kind === 'select') {
        control = document.createElement('select');
        for (const o of s.options || []) {
            const option = el('option', null, o.label);
            option.value = o.value;
            control.append(option);
        }
        control.addEventListener('change', () => changeSetting(s.key, control.value));
        show = (v) => { control.value = v; };
        field.append(label, control);
    } else if (s.kind === 'bool') {
        control = document.createElement('input');
        control.type = 'checkbox';
        control.addEventListener('change', () => changeSetting(s.key, control.checked));
        show = (v) => { control.checked = !!v; };
        label.className = 'settings-checkbox-label';
        const row = el('div', 'settings-row settings-row-spread');
        row.append(label, control);
        field.append(row);
    } else if (s.kind === 'sound') {
        control = el('div', 'settings-value');
        show = (v) => { control.textContent = v; };
        const choose = el('button', 'desktop-only', 'Choose file\u2026');
        choose.addEventListener('click', async () => {
            const path = await invoke('pick_wake_chime_file');
            if (path) changeSetting(s.key, path);
        });
        const reset = el('button', null, 'Restore default');
        reset.addEventListener('click', () => changeSetting(s.key, ''));
        const row = el('div', 'settings-row');
        row.append(choose, reset);
        field.append(label, control, row);
    } else {
        control = document.createElement('input');
        control.type = 'number';
        control.step = s.kind === 'int' ? '1' : 'any';
        if (s.min != null) control.min = s.min;
        if (s.max != null) control.max = s.max;
        control.addEventListener('change', () => changeSetting(s.key, control.value));
        // Not while the user is typing in it, unless the value was refused.
        show = (v, force) => { if (force || document.activeElement !== control) control.value = v; };
        field.append(label, control);
    }
    control.id = 'setting-' + s.key;
    if (s.hint) field.append(el('div', 'settings-hint', s.hint));
    settingControls.set(s.key, show);
    return field;
}

function changeSetting(key, value) {
    clearSettingsError();
    if (!isConnected) {
        showSettingsError(offlineChange());
        putBackSetting(key);
        return;
    }
    pendingSetting = key;
    invoke('set_setting', { key, value });
}

// ── Providers ──────────────────────────────────────────────────────────────
// A phone is told whether a provider has a key (`api_key_set`), never the key.
function hasKey(p) {
    return !!(p.api_key || p.api_key_set);
}

function providerLabel(p) {
    const temp = p.temperature != null ? `, temp ${p.temperature}` : '';
    const vision = p.vision ? ' \u00b7 vision' : '';
    return `${p.type}/${p.model}${temp}${vision}${hasKey(p) ? ' \u00b7 key saved' : ''}`;
}

// What each type wants, as the TUI's provider form says it (provider_modal.py).
const PROVIDER_HINTS = {
    '': {
        model: 'Choose a type first',
        url: 'Choose a type first',
        key: 'Choose a type first',
    },
    api: {
        model: 'e.g. nvidia/nemotron-3.5-lightning-30b-a3b',
        url: 'e.g. https://integrate.api.nvidia.com or http://localhost:11434 (Ollama)',
        key: 'The provider\u2019s API key (blank for a server on this computer)',
    },
    llamacpp: {
        model: 'A GGUF file, or Hugging Face user/repo:quant (Qwen/Qwen3-4B-GGUF:Q4_K_M)',
        url: 'Blank: JARVIS runs it here',
        key: 'Not needed for a local model',
    },
    'ollama-cloud': {
        model: 'e.g. gpt-oss:120b',
        url: 'Blank: https://ollama.com',
        key: 'Your ollama.com key (blank: OLLAMA_API_KEY)',
    },
    lmstudio: {
        model: 'The model loaded in LM Studio',
        url: 'Blank: http://localhost:1234/v1',
        key: 'Not needed',
    },
};

function isLoopback(url) {
    try {
        return ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname);
    } catch (_) {
        return false;
    }
}

function isHttpUrl(url) {
    if (!/^https?:\/\//i.test(url)) return false;
    try {
        return !!new URL(url).hostname;
    } catch (_) {
        return false;
    }
}

function showProviderHints() {
    const hints = PROVIDER_HINTS[providerFormType.value] || PROVIDER_HINTS[''];
    providerFormModel.placeholder = hints.model;
    providerFormUrl.placeholder = hints.url;
    const editing = cachedProviders.find((p) => p.name === editingProviderName);
    providerFormKey.placeholder = editing && hasKey(editing)
        ? 'A key is saved; type a new one to replace it (another host needs its own)'
        : hints.key;
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
    editBtn.append(icon('pencil'));
    editBtn.addEventListener('click', () => openProviderForm(p));

    const removeBtn = document.createElement('button');
    removeBtn.className = 'provider-item-action';
    removeBtn.title = 'Remove';
    removeBtn.append(icon('trash'));
    removeBtn.addEventListener('click', () => removeProvider(p.name));

    const actions = document.createElement('div');
    actions.className = 'provider-item-actions';
    if (index > 0) {
        // JARVIS tries providers in this order; up moves one earlier.
        const upBtn = document.createElement('button');
        upBtn.className = 'provider-item-action';
        upBtn.title = 'Try earlier';
        upBtn.append(icon('arrow-up'));
        upBtn.addEventListener('click', () => sendProviderChange('list', 'move_provider', { name: p.name, position: index }));
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
    clearProviderFormError();
    providerFormType.value = existing?.type || '';
    providerFormModel.value = existing?.model || '';
    providerFormName.value = existing?.name || '';
    providerFormUrl.value = existing?.url || '';
    providerFormTemperature.value = existing?.temperature ?? '';
    providerFormKey.value = '';
    providerFormVision.checked = !!existing?.vision;
    providerFormSaveBtn.textContent = existing ? 'Save' : 'Add';
    showProviderHints();
    providerForm.classList.remove('hidden');
    settingsAddProviderBtn.classList.add('hidden');
}

function closeProviderForm() {
    editingProviderName = null;
    providerFormKey.value = '';
    if (pendingProvider === 'form') pendingProvider = 'list'; // its error has nowhere to show now
    providerForm.classList.add('hidden');
    settingsAddProviderBtn.classList.remove('hidden');
}

function clearProviderFormError() {
    providerFormError.textContent = '';
    providerFormError.classList.remove('visible');
}

function showProviderFormError(message) {
    providerFormError.textContent = message;
    providerFormError.classList.add('visible');
}

function sendProviderChange(from, command, args, done = null) {
    // One at a time: a reply does not say which request it answers.
    if (pendingProvider !== null) {
        if (from === 'command') addCommandReply('Another provider change is still waiting for JARVIS; try again in a moment.');
        return false;
    }
    clearSettingsError();
    clearProviderFormError();
    if (!isConnected) {
        (from === 'form' ? showProviderFormError : showSettingsError)(offlineChange());
        return false;
    }
    pendingProvider = from;
    providerDone = done;
    invoke(command, args);
    return true;
}

async function saveProviderForm() {
    const model = providerFormModel.value.trim();
    if (!model) return showProviderFormError('Model is required.');

    const ptype = providerFormType.value;
    if (!ptype) return showProviderFormError('Choose a type.');
    const apiKey = providerFormKey.value.trim();
    const typedUrl = providerFormUrl.value.trim();
    if (ptype === 'api' && !typedUrl) {
        return showProviderFormError(`An API provider needs its URL (${PROVIDER_HINTS.api.url}).`);
    }
    if (typedUrl && !isHttpUrl(typedUrl)) {
        return showProviderFormError('The URL must start with http:// or https:// and name a host.');
    }
    if (ptype === 'api' && !apiKey && !editingProviderName && !isLoopback(typedUrl)) {
        return showProviderFormError('A cloud provider needs its API key.');
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
    const vision = providerFormVision.checked;

    if (editingProviderName) {
        const fields = { model, type: ptype, vision };
        if (url) fields.url = url;
        if (apiKey) fields.key = apiKey;
        if (temperature !== null) fields.temperature = temperature;
        sendProviderChange('form', 'edit_provider', { name: editingProviderName, fields });
    } else {
        sendProviderChange('form', 'add_provider', {
            ptype,
            model,
            name: name || null,
            url: url || null,
            apiKey: apiKey || null,
            temperature,
            vision,
        });
    }
}

function removeProvider(name) {
    if (sendProviderChange('list', 'remove_provider', { name }) && editingProviderName === name) {
        closeProviderForm();
    }
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

// ── sudo password (Project-JARVIS#310) ─────────────────────────────────────
// JARVIS asks when a root command needs the host's sudo password and its
// popup setting is on. Requests wait in arrival order; one shows at a time.
// The typed password is sent and cleared at once: it is kept nowhere here.
const passwordRequests = new Map(); // id -> request
let passwordShownId = null;
let passwordSent = false; // an answer for the shown request is on its way

function showNextPasswordRequest() {
    const next = passwordRequests.values().next().value;
    passwordShownId = next ? next.id : null;
    passwordSent = false;
    passwordInput.value = '';
    passwordError.textContent = '';
    passwordError.classList.remove('visible');
    if (!next) {
        passwordModal.classList.remove('visible');
        passwordBackdrop.classList.remove('visible');
        return;
    }
    passwordCommand.textContent = next.command || '';
    // sudo's own question: its password prompt, or a code or new password
    // when PAM asks for one.
    passwordPrompt.textContent = next.prompt || 'Password';
    passwordAttempt.textContent = next.retry
        ? `Wrong password; sudo asks again (try ${next.attempt}). Several wrong passwords in a row can lock the account for a few minutes.`
        : '';
    passwordAttempt.classList.toggle('visible', !!next.retry);
    passwordSubmitBtn.disabled = false;
    passwordCancelBtn.disabled = false;
    passwordModal.classList.add('visible');
    passwordBackdrop.classList.add('visible');
    // Not while the user is typing elsewhere: the rest of a chat line would
    // go to sudo as a password, and a wrong one counts as a failed try.
    const typing = document.activeElement && document.activeElement !== document.body
        && document.activeElement.matches('input, textarea, select, [contenteditable]');
    if (!typing) passwordInput.focus();
}

function onPasswordRequest(request) {
    if (!request || !request.id || !document.body.classList.contains('can-approve')) return;
    passwordRequests.set(request.id, request);
    if (passwordShownId === null) showNextPasswordRequest();
}

function onPasswordDone(done) {
    if (!done || !passwordRequests.delete(done.id)) return;
    if (done.id === passwordShownId) showNextPasswordRequest();
}

function showPasswordError(message) {
    passwordError.textContent = message;
    passwordError.classList.add('visible');
    passwordSubmitBtn.disabled = false;
    passwordCancelBtn.disabled = false;
    passwordSent = false;
}

function submitPassword(event) {
    event.preventDefault();
    if (passwordShownId === null || passwordSent) return;
    const password = passwordInput.value;
    if (!password) return showPasswordError('Type the password first.');
    if (!isConnected) return showPasswordError(offlineChange());
    passwordInput.value = '';
    passwordSent = true;
    passwordSubmitBtn.disabled = true;
    passwordCancelBtn.disabled = true;
    invoke('answer_password', { id: passwordShownId, password });
}

function cancelPassword() {
    if (passwordShownId === null || passwordSent) return;
    if (!isConnected) return showPasswordError(offlineChange());
    passwordSent = true;
    passwordSubmitBtn.disabled = true;
    passwordCancelBtn.disabled = true;
    invoke('cancel_password', { id: passwordShownId });
}

/// The connection went: JARVIS sends what still waits once it is back.
function forgetPasswordRequests() {
    passwordRequests.clear();
    showNextPasswordRequest();
}

function handleDisconnect(info) {
    sessionEnded = !!info && info.code >= 4000 && info.code < 5000;
    loseChangesInFlight();
    // Not answerable while offline; it opens again once the list returns.
    if (approvalShownId !== null) {
        approvalSeen.delete(approvalShownId);
        closeApprovalPopup();
    }
    forgetPasswordRequests();
    if (sessionEnded && settingsOpen()) showSettingsError(ENDED_CHANGE);
    // Their ids were this connection's.
    const dropped = attachments.length > 0;
    dropAttachments();
    setConnected(false);
    setStatus('offline');
    closeWorkPanel();
    actionBlocks.clear();
    showActivity('');
    if (info && info.reason) showNotice(info.reason);
    else if (dropped) showNotice('The connection dropped, and the attached files with it; attach them again.');
}

function closePanels() {
    if (approvalShownId !== null) laterApproval();
    closeWorkPanel();
    closePermissionPanel();
    closeSidebar();
    closeSettingsModal();
    closeShutdownModal();
}

// ── Slash commands (the TUI's, from commands.js) ───────────────────────────
// A `where: 'app'` command runs here, in COMMAND_HANDLERS; a handler that
// returns false passes the line on to JARVIS, as the TUI passes "/clear foo".
// The others go to JARVIS as a message.
function hasBodyClass(cls) {
    return document.body.classList.contains(cls);
}

function canApprove() {
    return hasBodyClass('can-approve');
}

/// The catalog command a parsed line names, or undefined: a message, or an
/// unknown /x that goes to JARVIS as one.
function lineCommand(line) {
    return line ? JARVIS_COMMANDS.find((c) => c.name === line.name) : undefined;
}

function offeredCommands() {
    return commandsFor(JARVIS_COMMANDS, hasBodyClass);
}

let completer = null;
let completerNames = '';

/// One completer while the offered commands stay the same, so Tab can cycle.
function commandCompleter() {
    const names = offeredCommands().map((c) => c.name);
    if (names.join(' ') !== completerNames) {
        completerNames = names.join(' ');
        completer = new CommandCompleter(names);
    }
    return completer;
}

let popupRows = [];
let popupIndex = 0;

function popupOpen() {
    return !commandPopup.classList.contains('hidden');
}

function closeCommandPopup() {
    commandPopup.classList.add('hidden');
    commandPopup.replaceChildren();
    popupRows = [];
    messageInput.setAttribute('aria-expanded', 'false');
    messageInput.removeAttribute('aria-activedescendant');
}

/// The suggestions for the line, while it is a bare `/token`.
function syncCommandPopup() {
    popupRows = filterCommands(messageInput.value, offeredCommands());
    if (!popupRows.length) return closeCommandPopup();
    commandPopup.replaceChildren(...popupRows.map((c, i) => {
        const row = el('div', 'command-option');
        row.id = `command-option-${i}`;
        row.title = `${c.name} ${c.usage} — ${c.description}`;
        row.setAttribute('role', 'option');
        row.append(el('span', 'command-name', c.name));
        if (c.usage) row.append(el('span', 'command-usage', c.usage));
        row.append(el('span', 'command-desc', c.description));
        row.addEventListener('click', () => acceptCommand(c.name));
        return row;
    }));
    commandPopup.classList.remove('hidden');
    messageInput.setAttribute('aria-expanded', 'true');
    highlightCommand(0);
}

function highlightCommand(index) {
    popupIndex = (index + popupRows.length) % popupRows.length;
    [...commandPopup.children].forEach((row, i) => {
        row.classList.toggle('active', i === popupIndex);
        row.setAttribute('aria-selected', String(i === popupIndex));
    });
    const active = commandPopup.children[popupIndex];
    messageInput.setAttribute('aria-activedescendant', active.id);
    active.scrollIntoView({ block: 'nearest' });
}

/// The page writing the line, not the user typing: a Tab cycle goes on.
function writeInput(text) {
    messageInput.value = text;
    messageInput.setSelectionRange(text.length, text.length);
    syncCommandPopup();
    refreshSendBtn();
}

function acceptCommand(name) {
    writeInput(name + ' ');
}

function onInputKeydown(e) {
    if (e.key === 'Tab' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
        // Otherwise Tab moves the focus on, as it always did.
        const completion = commandCompleter().complete(messageInput.value);
        if (completion !== null) {
            e.preventDefault();
            writeInput(completion);
        }
    } else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && popupOpen()) {
        e.preventDefault();
        highlightCommand(popupIndex + (e.key === 'ArrowDown' ? 1 : -1));
    } else if (e.key === 'Escape' && popupOpen()) {
        e.stopPropagation(); // closes the list, not the panels too
        closeCommandPopup();
    } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const pick = popupOpen() ? popupRows[popupIndex].name : null;
        // Only when it changes the line: a typed or completed command sends on the first Enter.
        if (pick && pick !== messageInput.value.trim()) acceptCommand(pick);
        else sendMessage();
    }
}

function helpCommand(args) {
    const found = searchCommands(offeredCommands(), args);
    addCommandReply(found.length
        ? found.map((c) => `${c.name}${c.usage ? ' ' + c.usage : ''} — ${c.description}`).join('\n')
        : `Nothing mentions ${args}.`);
}

/// The chat as it is drawn: what was said, JARVIS's own text, and what it ran.
function transcriptMarkdown() {
    const lines = [];
    for (const node of messages.children) {
        if (node.classList.contains('user-message')) {
            lines.push(`**You:** ${node.dataset.text}`);
        } else if (node.classList.contains('jarvis-message')) {
            lines.push(`**JARVIS:** ${node.querySelector('.bubble-text').dataset.raw || ''}`);
        } else if (node.matches('details.action-block')) {
            lines.push(`- ${node.querySelector('.action-title').textContent}`);
        } else if (node.classList.contains('system-message')) {
            lines.push(`_${node.textContent}_`);
        }
    }
    return [
        '# JARVIS transcript export',
        '',
        `- Exported (UTC): ${new Date().toISOString()}`,
        `- Session id: \`${currentSessionId || 'none'}\``,
        '',
        '---',
        '',
        lines.length ? lines.join('\n\n') : '_(no transcript lines yet)_',
        '',
    ].join('\n');
}

function downloadText(file, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }));
    const link = el('a');
    link.href = url;
    link.download = file;
    document.body.append(link);
    link.click();
    link.remove();
    // A phone may still be reading it when click() returns.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function exportCommand(args) {
    const name = args
        || `${currentSessionId ? currentSessionId.slice(0, 8) : 'no-session'}_${Math.floor(Date.now() / 1000)}`;
    const text = transcriptMarkdown();
    if (!IS_DESKTOP) {
        const base = name.split(/[\\/]/).pop() || 'transcript';
        const file = /\.md$/i.test(base) ? base : `${base}.md`;
        downloadText(file, text);
        addCommandReply(`Saved the transcript as ${file} (your browser's downloads).`);
        return;
    }
    try {
        addCommandReply(`Exported transcript to ${await invoke('export_transcript', { name, text })}`);
    } catch (e) {
        addCommandReply(`Export failed: ${e}`);
    }
}

function commandsCommand(args) {
    const arg = args.toLowerCase();
    if (!arg) {
        addCommandReply("Click a block's title to fold it; /commands collapse or /commands expand folds them all.");
    } else if (arg !== 'collapse' && arg !== 'expand') {
        addCommandReply('Usage: /commands [collapse|expand]');
    } else {
        for (const block of messages.querySelectorAll('details.action-block')) {
            block.open = arg === 'expand';
            block.dataset.touched = '1'; // foldOlderBlocks leaves it as set
        }
    }
}

function quitCommand(args) {
    if (args) return false;
    if (IS_DESKTOP) invoke('quit_app');
    else addCommandReply('Close this tab to leave; JARVIS keeps running on the host.');
}

function openProvidersTab() {
    openSettingsModal(); // opens on General
    switchSettingsTab('providers');
}

/// /providers as the TUI reads it (local_input.py); a key is sent, never shown.
function providersCommand(args) {
    const tokens = args.split(/\s+/).filter(Boolean);
    if (!tokens.length) return openProvidersTab();
    const sub = tokens[0].toLowerCase();
    const name = tokens[1];
    if (sub === 'add') {
        const { rest, vision } = takeVisionFlag(tokens.slice(1));
        const flags = parseFlags(rest);
        if (!flags.type || !flags.model) {
            openProvidersTab();
            openProviderForm(null);
            return;
        }
        sendProviderChange('command', 'add_provider', {
            ptype: flags.type,
            model: flags.model,
            name: flags.name || null,
            url: flags.url || null,
            apiKey: flags.key || null,
            temperature: null,
            vision: !!vision,
        }, `Added provider ${flags.name ? `'${flags.name}' ` : ''}(${flags.type}/${flags.model})`);
    } else if (sub === 'remove') {
        if (!name) return addCommandReply('Usage: /providers remove <name>');
        sendProviderChange('command', 'remove_provider', { name }, `Removed provider '${name}'`);
    } else if (sub === 'move') {
        if (tokens.length < 3) return addCommandReply('Usage: /providers move <name> <position>');
        const position = /^\d+$/.test(tokens[2]) ? Number(tokens[2]) : 0;
        if (position < 1) return addCommandReply('Position must be a number');
        sendProviderChange('command', 'move_provider', { name, position },
            `Moved provider '${name}' to position ${position}`);
    } else if (sub === 'edit') {
        if (!name) {
            return addCommandReply('Usage: /providers edit <name> [--model <m>] [--url <u>] [--key <k>] [--vision <true|false>]');
        }
        const { rest, vision } = takeVisionFlag(tokens.slice(2));
        const fields = parseFlags(rest);
        if (vision !== undefined) fields.vision = vision;
        if (!Object.keys(fields).length) {
            editAfterList = name; // the form opens pre-filled from the list JARVIS sends
            invoke('list_providers');
            return;
        }
        if (Number.isFinite(Number(fields.temperature))) fields.temperature = Number(fields.temperature);
        sendProviderChange('command', 'edit_provider', { name, fields },
            `Updated provider '${name}': ${Object.keys(fields).join(', ')}`);
    } else {
        addCommandReply(`Unknown subcommand '${sub}'. Use: add, remove, move, edit`);
    }
}

function openProviderEditor(name, providers) {
    const existing = providers.find((p) => p.name === name);
    if (!existing) return addCommandReply(`Provider '${name}' not found`);
    openProvidersTab();
    openProviderForm(existing);
}

// A /model or /setups waiting for the reply it reads its answer from.
let modelAsked = false;
let setupsAsked = false;

function modelLine(sections) {
    const llm = (sections || []).find((s) => s.title === 'Language model');
    const inUse = llm && (llm.checks || []).find((c) => c.detail === 'in use');
    return `Model: ${inUse ? inUse.name : '(unknown)'}`;
}

function setupsLine(work) {
    const n = work.setups || 0;
    if (!n) return 'No server setups are waiting.';
    return `${n} server setup${n === 1 ? ' waits' : 's wait'} on the host. `
        + 'Fill them in with /setups in jarvis tui, or run `jarvis setups fill`.';
}

function attachCommand(args) {
    const arg = args.toLowerCase();
    if (!arg) {
        attachInput.click();
    } else if (arg === 'clear') {
        const n = attachments.length;
        dropAttachments(true);
        addCommandReply(n ? `Dropped ${n} attachment${n === 1 ? '' : 's'}.` : 'No attachments to drop.');
    } else {
        addCommandReply('Usage: /attach [clear]');
    }
}

const COMMAND_HANDLERS = {
    '/help': helpCommand,
    '/?': helpCommand,
    '/export': exportCommand,
    '/clear': (args) => {
        if (args) return false;
        clearChat();
    },
    '/commands': commandsCommand,
    '/quit': quitCommand,
    '/exit': quitCommand,
    '/status': (args) => {
        const arg = args.toLowerCase();
        if (arg && arg !== 'full') return false;
        openWorkPanel(); // clears the status list, so first
        statusList.textContent = 'Checking…';
        invoke('get_health', { full: arg === 'full' });
    },
    '/providers': providersCommand,
    '/model': (args) => {
        if (args) return addCommandReply("Use /providers edit <name> --model <model> to change a provider's model.");
        modelAsked = true;
        invoke('get_health', { full: false });
    },
    '/settings': (args) => {
        if (args) return false;
        openSettingsModal();
    },
    // Setups never cross the gateway, and their values never the socket: this
    // only says how many wait.
    '/setups': (args) => {
        if (args) return false;
        setupsAsked = true;
        openWorkPanel();
    },
    '/tasks': (args) => {
        if (args) return false;
        openWorkPanel();
    },
    '/attach': attachCommand,
};

// ── Attachments: images and PDFs ───────────────────────────────────────────
// Each file goes up in 32 KiB pieces (upload_chunk); JARVIS answers once, with
// upload_done and the file's id or with upload_error. The ids go with the next
// message, and only on this connection: a dropped connection drops them.
const ATTACH_MAX_BYTES = 20 * 1024 * 1024;
const ATTACH_CHUNK = 32 * 1024;
const ATTACH_MAX_FILES = 10;
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif'];

const attachments = []; // {upload, file, name, size, kind, url, chip, bar, id, meta}
let uploadQueue = Promise.resolve(); // one upload at a time: JARVIS caps those in flight

function attachmentKind(file) {
    const name = file.name.toLowerCase();
    if (file.type === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
    if (IMAGE_TYPES.includes(file.type) || IMAGE_EXTENSIONS.some((ext) => name.endsWith(ext))) return 'image';
    return null;
}

function readyAttachments() {
    return attachments.filter((a) => a.id);
}

function uploading() {
    return attachments.some((a) => !a.id);
}

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/// A sent file, as its message shows it: `{name, kind, size}`.
function fileChip(f) {
    const chip = el('span', 'attachment-chip');
    chip.title = f.name;
    chip.append(icon(f.kind === 'pdf' ? 'file' : 'image'), el('span', 'attachment-name', f.name));
    if (f.size != null) chip.append(el('span', 'attachment-size', formatSize(f.size)));
    return chip;
}

function buildAttachmentChip(item) {
    const chip = el('div', 'attachment-chip uploading');
    chip.title = item.name;
    if (item.url) {
        const img = el('img', 'attachment-thumb');
        img.alt = '';
        // The page's Content-Security-Policy may refuse a blob: image.
        img.addEventListener('error', () => img.replaceWith(icon('image')));
        img.src = item.url;
        chip.append(img);
    } else {
        chip.append(icon('file'));
    }
    const remove = el('button', 'attachment-remove');
    remove.type = 'button';
    remove.title = `Remove ${item.name}`;
    remove.setAttribute('aria-label', remove.title);
    remove.append(icon('x'));
    remove.addEventListener('click', () => cancelAttachment(item));
    item.bar = el('span', 'attachment-progress');
    item.bar.style.width = '0%';
    chip.append(el('span', 'attachment-name', item.name), el('span', 'attachment-size', formatSize(item.size)),
        remove, item.bar);
    return chip;
}

function uploadId() {
    return Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('');
}

function toBase64(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x2000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x2000));
    }
    return btoa(binary);
}

function addFiles(files) {
    if (!canApprove()) return;
    if (!isConnected) return showNotice('Not connected to JARVIS; attach the files once it reconnects.');
    for (const file of files) {
        const kind = attachmentKind(file);
        if (!kind) { showNotice(`${file.name} is not an image (PNG, JPEG, WebP, GIF) or a PDF.`); continue; }
        if (!file.size) { showNotice(`${file.name} is empty.`); continue; }
        if (file.size > ATTACH_MAX_BYTES) { showNotice(`${file.name} is larger than 20 MB.`); continue; }
        if (attachments.length >= ATTACH_MAX_FILES) {
            showNotice(`At most ${ATTACH_MAX_FILES} files go with one message.`);
            break;
        }
        const item = {
            upload: uploadId(), file, name: file.name, size: file.size, kind, id: null, meta: null,
            url: kind === 'image' ? URL.createObjectURL(file) : null,
        };
        item.chip = buildAttachmentChip(item);
        attachments.push(item);
        attachmentsRow.append(item.chip);
        uploadQueue = uploadQueue.then(() => uploadFile(item));
    }
    attachmentsRow.classList.toggle('hidden', !attachments.length);
    refreshSendBtn();
}

// A file removed mid-upload stops sending; its upload_cancel frees it on JARVIS.
async function uploadFile(item) {
    try {
        for (let seq = 0, offset = 0; offset < item.size; seq++, offset += ATTACH_CHUNK) {
            const bytes = new Uint8Array(await item.file.slice(offset, offset + ATTACH_CHUNK).arrayBuffer());
            if (!attachments.includes(item) || !isConnected) return;
            const chunk = { upload: item.upload, seq, data: toBase64(bytes), last: offset + ATTACH_CHUNK >= item.size };
            if (seq === 0) Object.assign(chunk, { name: item.name, size: item.size });
            await invoke('upload_chunk', chunk);
            item.bar.style.width = `${Math.round(100 * Math.min(1, (offset + ATTACH_CHUNK) / item.size))}%`;
        }
    } catch (e) {
        if (!attachments.includes(item)) return;
        cancelAttachment(item);
        showNotice(`${item.name} could not be read: ${e}`);
    }
}

function onUploadDone(done) {
    const item = attachments.find((a) => a.upload === done.upload);
    if (!item || !done.attachment) return;
    item.id = done.attachment.id;
    item.meta = done.attachment;
    item.chip.classList.remove('uploading');
    item.bar.remove();
    refreshSendBtn();
}

function onUploadError(failed) {
    const item = attachments.find((a) => a.upload === failed.upload);
    if (!item) return;
    removeAttachment(item);
    showNotice(`${item.name}: ${failed.message || 'the upload failed'}`);
}

function removeAttachment(item) {
    const index = attachments.indexOf(item);
    if (index < 0) return;
    attachments.splice(index, 1);
    item.chip.remove();
    if (item.url) URL.revokeObjectURL(item.url);
    attachmentsRow.classList.toggle('hidden', !attachments.length);
    refreshSendBtn();
}

/// The user took the file back: JARVIS drops its upload, finished or not.
function cancelAttachment(item) {
    if (isConnected) invoke('upload_cancel', { upload: item.upload });
    removeAttachment(item);
}

/// `cancel`: the user dropped them; without, their connection is gone.
function dropAttachments(cancel = false) {
    for (const item of [...attachments]) (cancel ? cancelAttachment : removeAttachment)(item);
}

// Files dragged onto the window. The page takes the drop even when it does
// not attach (a guest), so the browser does not open the file instead.
let dragDepth = 0;

function fileDrag(e) {
    return !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
}

function showDropOverlay(shown) {
    dropOverlay.classList.toggle('hidden', !(shown && canApprove()));
}

// ── Actions ────────────────────────────────────────────────────────────────
async function sendMessage() {
    const text = messageInput.value.trim();
    const ready = readyAttachments();
    if (!isConnected || (!text && !ready.length)) return;
    const line = parseCommandLine(text);
    const command = lineCommand(line);
    // A command takes no files, so it need not wait for an upload; they wait
    // for the next message.
    if (!command && uploading()) return;
    messageInput.value = '';
    closeCommandPopup();
    refreshSendBtn();
    const files = command ? [] : ready;
    // Every line shows, as in the TUI; a typed API key does not.
    addUserMessage(line ? maskKeys(text) : text, null, files.map((a) => a.meta));
    if (command && command.where === 'app') {
        // Typed though not offered; the gateway still checks. /quit answers a
        // browser itself.
        if (command.needs !== 'desktop' && !commandsFor([command], hasBodyClass).length) {
            addCommandReply(`${command.name} is not available to a ${role}.`);
            return;
        }
        if ((await COMMAND_HANDLERS[command.name](line.args)) !== false) return;
    }
    if (line && hasKeyFlag(text)) {
        addCommandReply('Not sent: a line with --key stays on this screen, so the key never reaches the model.');
        return;
    }
    const ids = files.map((a) => a.id);
    for (const a of files) removeAttachment(a); // their ids are used up
    setStatus('processing');
    await invoke('send_message', { content: text, attachments: ids });
}

async function toggleListening() {
    if (!isConnected) return showChangeError(offlineChange());
    await invoke('toggle_listening');
}

// ── Boot ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    document.body.classList.add(IS_DESKTOP ? 'desktop' : 'web');
    if (IS_DESKTOP) document.body.classList.add('can-approve', 'can-manage');

    messageInput.addEventListener('input', () => {
        commandCompleter().reset(); // the user typed: a Tab cycle ends
        syncCommandPopup();
        refreshSendBtn();
    });
    messageInput.addEventListener('keydown', onInputKeydown);
    messageInput.addEventListener('blur', closeCommandPopup);
    // The input keeps the focus, so its blur does not close the list under a
    // tap; a click (not pointerdown) picks, so a finger scrolling it does not.
    commandPopup.addEventListener('mousedown', (e) => e.preventDefault());

    attachBtn.addEventListener('click', () => attachInput.click());
    attachInput.addEventListener('change', () => {
        addFiles(attachInput.files);
        attachInput.value = '';
    });
    document.addEventListener('dragenter', (e) => {
        if (!fileDrag(e)) return;
        e.preventDefault();
        dragDepth++;
        showDropOverlay(true);
    });
    document.addEventListener('dragover', (e) => {
        if (!fileDrag(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = canApprove() ? 'copy' : 'none';
    });
    document.addEventListener('dragleave', (e) => {
        if (!fileDrag(e)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) showDropOverlay(false);
    });
    document.addEventListener('drop', (e) => {
        if (!fileDrag(e)) return;
        e.preventDefault();
        dragDepth = 0;
        showDropOverlay(false);
        addFiles(e.dataTransfer.files);
    });

    sendBtn.addEventListener('click', sendMessage);
    micBtn.addEventListener('click', toggleListening);
    // In a browser the button turns the laptop's wake word on or off.
    if (!IS_DESKTOP) {
        micBtn.title = 'Wake word on the laptop: on or off';
        micBtn.setAttribute('aria-label', micBtn.title);
    }
    providerFormType.addEventListener('change', showProviderHints);
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
    document.getElementById('session-close').addEventListener('click', closeSidebar);
    swipeToClose(sessionSidebar, closeSidebar, -1);
    swipeToClose(permissionPanel, closePermissionPanel, 1);
    swipeToClose(workPanel, closeWorkPanel, 1);
    newSessionBtn.addEventListener('click', createNewSession);
    permissionsBtn.addEventListener('click', togglePermissionPanel);
    permissionBackdrop.addEventListener('click', closePermissionPanel);
    approveAllBtn.addEventListener('click', approveAllConfirmations);
    passwordForm.addEventListener('submit', submitPassword);
    approvalLaterBtn.addEventListener('click', laterApproval);
    approvalBackdrop.addEventListener('click', laterApproval);
    // A decision made in the pop-up closes it at once; the refreshed list
    // then shows the next request.
    approvalBody.addEventListener('click', (e) => {
        const button = e.target.closest('.permission-action');
        if (button && !button.disabled) closeApprovalPopup();
    });
    passwordCancelBtn.addEventListener('click', cancelPassword);

    settingsBtn.addEventListener('click', toggleSettingsModal);
    settingsBackdrop.addEventListener('click', closeSettingsModal);
    settingsCloseBtn.addEventListener('click', closeSettingsModal);
    settingsTabGeneralBtn.addEventListener('click', () => switchSettingsTab('general'));
    settingsTabProvidersBtn.addEventListener('click', () => switchSettingsTab('providers'));
    settingsTabWidgetsBtn.addEventListener('click', () => switchSettingsTab('widgets'));
    settingsVoiceToggle.addEventListener('click', toggleListening);
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
        // A browser gets what waits with the gateway's hello.
        if (IS_DESKTOP) invoke('list_password_requests');
        sessionEnded = false;
        if (settingsOpen()) {
            if (settingsError.textContent === OFFLINE_CHANGE) clearSettingsError();
            if (providerFormError.textContent === OFFLINE_CHANGE) clearProviderFormError();
            invoke('get_settings');
            invoke('list_providers');
        }
    });
    await listen('ipc-disconnected', (e)     => handleDisconnect(e.payload));
    await listen('ipc-gateway',      (e)     => applyGateway(e.payload));
    await listen('ipc-ack',          (e)     => onAck(e.payload));
    await listen('ipc-state',        (e)     => setStatus(e.payload));
    await listen('ipc-chunk',        (e)     => appendJarvisChunk(e.payload.content, e.payload.done, e.payload.comment,
                                                                   undefined, e.payload.command));
    await listen('ipc-activity',     (e)     => showActivity(e.payload));
    // Said on another screen, in the TUI or aloud; this one drew its own.
    await listen('ipc-user-message', (e)     => addUserMessage(e.payload.content || '', e.payload.at,
                                                                e.payload.attachments || []));
    await listen('ipc-upload-done',  (e)     => onUploadDone(e.payload));
    await listen('ipc-upload-error', (e)     => onUploadError(e.payload));
    await listen('ipc-action',       (e)     => onAction(e.payload));
    await listen('ipc-work', (e) => {
        renderWork(e.payload || {});
        if (setupsAsked) addCommandReply(setupsLine(e.payload || {}));
        setupsAsked = false;
    });
    await listen('ipc-health', (e) => {
        renderStatus(e.payload);
        if (modelAsked) addCommandReply(modelLine(e.payload));
        modelAsked = false;
    });
    await listen('ipc-wake',         ()      => setStatus('listening'));
    await listen('ipc-confirm', () => {
        // The refreshed list opens the pop-up (syncApprovalPopup); never a
        // blocking window.confirm(), which would freeze the page.
        flashPermissionIndicator();
        invoke('list_confirmations');
    });
    await listen('ipc-confirmation-list', (e) => renderPermissionList(e.payload));
    await listen('ipc-session-list', (e) => {
        renderSessionList(e.payload);
        // In no session (just connected, or the current one was deleted): go to
        // the newest. A guest may not switch sessions; it follows the host's.
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
    await listen('ipc-error', (e) => onError(e.payload));
    await listen('ipc-settings', (e) => renderSettings(e.payload || []));
    await listen('ipc-provider-list', (e) => {
        renderProviderList(e.payload);
        // Only the form's own save closes it: a list also comes from a
        // reconnect or another screen, and the user may be typing.
        if (pendingProvider === 'form') closeProviderForm();
        if (pendingProvider === 'command') addCommandReply(providerDone);
        pendingProvider = null;
        if (editAfterList !== null) {
            const name = editAfterList;
            editAfterList = null;
            openProviderEditor(name, e.payload);
        }
    });
    await listen('ipc-provider-error', (e) => {
        if (pendingProvider === 'form') showProviderFormError(e.payload);
        else if (pendingProvider === 'command') addCommandReply(`Error: ${e.payload}`);
        else showChangeError(e.payload);
        pendingProvider = null;
    });
    await listen('ipc-client-list', (e) => renderShutdownClientList(e.payload));
    await listen('ipc-password-request', (e) => onPasswordRequest(e.payload));
    await listen('ipc-password-done', (e) => onPasswordDone(e.payload));
    await listen('ipc-daemon-shutdown', (e) => handleDaemonShutdown(e.payload));
    await listen('ipc-open-shutdown-modal', openShutdownModal); // tray menu entry point
    await listen('ipc-config-updated', (e) => {
        if (e.payload.key === pendingSetting) pendingSetting = null;
        showSetting(e.payload.key, e.payload.value);
    });
    await listen('ipc-config-error', (e) => {
        if (e.payload.key === pendingSetting) pendingSetting = null;
        showSettingsError(e.payload.message);
        putBackSetting(e.payload.key);
        invoke('get_settings'); // put the refused control back to what JARVIS runs with
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
        // A request sent before this page listened was dropped by Tauri.
        if (IS_DESKTOP) await invoke('list_password_requests');
    }
});
