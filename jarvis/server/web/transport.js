'use strict';

// How the page reaches JARVIS. main.js only calls invoke(command, args) and
// listen(event, callback), and sees the same commands, events and payloads
// either way:
// - in jarvis-app, Tauri carries them to the Rust side, which talks to the
//   daemon's local socket (src-tauri/src/ipc.rs);
// - opened in a browser through `jarvis web`, they ride one WebSocket to the
//   gateway, which relays the same socket protocol to the roles allowed it
//   (Project-JARVIS docs/web-gateway.md). This file is the only place the two
//   differ, and Project-JARVIS serves a pinned copy of this folder.
(function (global) {
    if (global.__TAURI__) {
        global.JarvisTransport = {
            kind: 'tauri',
            invoke: global.__TAURI__.core.invoke,
            listen: global.__TAURI__.event.listen,
            // The Rust side connects, and reconnects, by itself.
            connect() {},
            reconnect() {},
        };
        return;
    }

    // Tauri command -> socket message. Desktop-only commands (settings,
    // providers, widgets, voice, daemon shutdown) are absent: the page hides
    // their controls in a browser, and the gateway refuses them anyway.
    const COMMANDS = {
        send_message:               (a) => ({ type: 'message', content: a.content }),
        stop_stream:                ()  => ({ type: 'stop_stream' }),
        stop:                       ()  => ({ type: 'stop' }),
        stop_all:                   ()  => ({ type: 'stop_all' }),
        stop_task:                  (a) => ({ type: 'stop_task', pid: a.pid }),
        list_sessions:              ()  => ({ type: 'list_sessions' }),
        create_session:             (a) => ({ type: 'create_session', title: a.title }),
        switch_session:             (a) => ({ type: 'switch_session', id: a.id }),
        rename_session:             (a) => ({ type: 'rename_session', id: a.id, title: a.title }),
        delete_session:             (a) => ({ type: 'delete_session', id: a.id }),
        list_confirmations:         ()  => ({ type: 'list_confirmations' }),
        approve_confirmation:       (a) => ({ type: 'approve_confirmation', id: a.id }),
        deny_confirmation:          (a) => ({ type: 'deny_confirmation', id: a.id }),
        partial_approve_confirmation: (a) => ({
            type: 'partial_approve_confirmation', id: a.id, approved_indices: a.approvedIndices,
        }),
        list_clients:               ()  => ({ type: 'list_clients' }),
        // The daemon's health check; `get_status` below is the connection's.
        get_health:                 (a) => ({ type: 'get_status', full: !!a.full }),
        list_work:                  ()  => ({ type: 'list_work' }),
    };

    const listeners = {};
    let socket = null;
    let retryMs = 1000;
    let keepalive = null;
    let closedByGateway = false;
    let state = 'offline';

    function emit(event, payload) {
        (listeners[event] || []).forEach((cb) => cb({ payload }));
    }

    async function listen(event, callback) {
        (listeners[event] = listeners[event] || []).push(callback);
    }

    function connected() {
        return !!socket && socket.readyState === WebSocket.OPEN;
    }

    async function invoke(command, args) {
        // Answered here, as the Tauri side answers it from its own state.
        if (command === 'get_status') return { connected: connected(), state };
        const build = COMMANDS[command];
        if (!build) throw new Error('Not available in the browser: ' + command);
        if (connected()) socket.send(JSON.stringify(build(args || {})));
    }

    // Socket message -> the event and payload the Tauri side emits for it.
    function route(msg) {
        switch (msg.type) {
        case 'state':
            state = msg.state;
            return emit('ipc-state', msg.state);
        case 'response':
            return emit('ipc-chunk', { content: msg.content, done: msg.done, comment: !!msg.comment });
        case 'activity':             return emit('ipc-activity', msg.text || '');
        case 'action':               return emit('ipc-action', msg);
        case 'status':               return emit('ipc-health', msg.sections || []);
        case 'work':                 return emit('ipc-work', msg.work || {});
        case 'ack':                  return emit('ipc-ack', { message: msg.message || '', for: msg.for || null });
        case 'confirmation_request': return emit('ipc-confirm', msg);
        case 'confirmation_list':    return emit('ipc-confirmation-list', msg.confirmations || []);
        case 'session_list':         return emit('ipc-session-list', msg.sessions || []);
        case 'session_switched':     return emit('ipc-session-switched', { session: msg.session, messages: msg.messages || [] });
        case 'session_error':        return emit('ipc-session-error', msg.message);
        case 'client_list':          return emit('ipc-client-list', msg.clients || []);
        case 'error':                return emit('ipc-error', msg.message);
        case 'gateway':              return emit('ipc-gateway', msg);
        case 'DAEMON_SHUTDOWN':      return emit('ipc-daemon-shutdown', msg);
        default:                     return undefined; // the protocol grows
        }
    }

    function connect() {
        const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${scheme}//${location.host}/ws`);
        socket.addEventListener('open', () => {
            retryMs = 1000;
            closedByGateway = false;
            emit('ipc-connected');
            keepalive = setInterval(() => {
                if (connected()) socket.send('{"type":"ping"}');
            }, 25000);
        });
        socket.addEventListener('message', (e) => {
            try { route(JSON.parse(e.data)); } catch (_) { /* ignore a malformed frame */ }
        });
        socket.addEventListener('close', (e) => {
            clearInterval(keepalive);
            state = 'offline';
            emit('ipc-disconnected', { code: e.code, reason: e.reason });
            // 4000-range codes are the gateway ending the session on purpose
            // (idle timeout, refused, unsafe settings): do not hammer it.
            if (e.code >= 4000 && e.code < 5000) { closedByGateway = true; return; }
            setTimeout(connect, retryMs);
            retryMs = Math.min(retryMs * 2, 15000);
        });
    }

    global.JarvisTransport = {
        kind: 'web',
        invoke,
        listen,
        connect,
        reconnect: () => { if (closedByGateway) connect(); },
    };
})(window);
