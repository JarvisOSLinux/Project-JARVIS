'use strict';

// The connection switch. main.js talks to JARVIS through invoke(command, args)
// and listen(event, callback), the same shape jarvis-app gets from Tauri; here
// both ride one WebSocket to the `jarvis web` gateway, which relays the
// jarvis.sock protocol (docs/web-gateway.md).
(function (global) {
    const COMMANDS = {
        send_message:               (a) => ({ type: 'message', content: a.content }),
        stop_stream:                ()  => ({ type: 'stop_stream' }),
        stop:                       ()  => ({ type: 'stop' }),
        stop_all:                   ()  => ({ type: 'stop_all' }),
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
        get_status:                 (a) => ({ type: 'get_status', full: !!a.full }),
        list_work:                  ()  => ({ type: 'list_work' }),
    };

    const EVENTS = {
        state:                'ipc-state',
        response:             'ipc-chunk',
        confirmation_request: 'ipc-confirm',
        confirmation_list:    'ipc-confirmation-list',
        session_list:         'ipc-session-list',
        session_switched:     'ipc-session-switched',
        session_error:        'ipc-session-error',
        client_list:          'ipc-client-list',
        error:                'ipc-error',
        ack:                  'ipc-ack',
        gateway:              'ipc-gateway',
        DAEMON_SHUTDOWN:      'ipc-daemon-shutdown',
        activity:             'ipc-activity',
        action:               'ipc-action',
        status:               'ipc-status',
        work:                 'ipc-work',
    };

    const listeners = {};
    let socket = null;
    let retryMs = 1000;
    let keepalive = null;
    let closedByGateway = false;

    function emit(event, payload) {
        (listeners[event] || []).forEach((cb) => cb({ payload }));
    }

    async function listen(event, callback) {
        (listeners[event] = listeners[event] || []).push(callback);
    }

    async function invoke(command, args) {
        const build = COMMANDS[command];
        if (!build) throw new Error('Unknown command: ' + command);
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify(build(args || {})));
    }

    function route(msg) {
        if (msg.type === 'response') {
            emit('ipc-chunk', {
                content: msg.content, done: msg.done, comment: !!msg.comment,
            });
        } else if (msg.type === 'confirmation_list') {
            emit('ipc-confirmation-list', msg.confirmations);
        } else if (msg.type === 'session_list') {
            emit('ipc-session-list', msg.sessions);
        } else if (msg.type === 'session_switched') {
            emit('ipc-session-switched', { session: msg.session, messages: msg.messages });
        } else if (msg.type === 'session_error') {
            emit('ipc-session-error', msg.message);
        } else if (msg.type === 'client_list') {
            emit('ipc-client-list', msg.clients);
        } else if (msg.type === 'error') {
            emit('ipc-error', msg.message);
        } else if (msg.type === 'state') {
            emit('ipc-state', msg.state);
        } else if (msg.type === 'gateway') {
            emit('ipc-gateway', msg);
        } else if (msg.type === 'DAEMON_SHUTDOWN') {
            emit('ipc-daemon-shutdown', msg);
        } else if (EVENTS[msg.type]) {
            emit(EVENTS[msg.type], msg);
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
                if (socket.readyState === WebSocket.OPEN) socket.send('{"type":"ping"}');
            }, 25000);
        });
        socket.addEventListener('message', (e) => {
            try { route(JSON.parse(e.data)); } catch (_) { /* ignore a malformed frame */ }
        });
        socket.addEventListener('close', (e) => {
            clearInterval(keepalive);
            emit('ipc-disconnected', { code: e.code, reason: e.reason });
            // 4000-range codes are the gateway ending the session on purpose
            // (idle timeout, refused, unsafe settings): do not hammer it.
            if (e.code >= 4000 && e.code < 5000) { closedByGateway = true; return; }
            setTimeout(connect, retryMs);
            retryMs = Math.min(retryMs * 2, 15000);
        });
    }

    global.JarvisTransport = { invoke, listen, connect, reconnect: () => { if (closedByGateway) connect(); } };
})(window);
