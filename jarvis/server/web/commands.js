'use strict';

// The slash commands, as Project-JARVIS's TUI lists them
// (jarvis/tui/slash_commands_doc.py, slash_command_catalog()): the same names
// in the same order, so a command typed here means what it means there.
// Project-JARVIS's tests/test_web_ui_commands.py fails when the two drift.
// `where: 'app'` runs here (COMMAND_HANDLERS in main.js); `'daemon'` goes to
// JARVIS as a message. `needs` hides a suggestion from a page without the
// body class (can-approve, can-manage, desktop); typed anyway, the gateway's
// role checks still answer. No DOM here: node loads this file in the tests.
const JARVIS_COMMANDS = [
    { name: '/help', usage: '[words]', where: 'app',
      description: 'List the commands, or only those that mention the words (not sent to JARVIS).' },
    { name: '/?', usage: '[words]', where: 'app',
      description: 'List the commands, or only those that mention the words (not sent to JARVIS).' },
    { name: '/export', usage: '[file]', where: 'app',
      description: 'Save the chat as Markdown: under transcripts/ in the app, as a download in a browser; optional file name.' },
    { name: '/clear', usage: '', where: 'app',
      description: 'Clear the chat view (on-screen only; session memory unchanged).' },
    { name: '/commands', usage: '[collapse|expand]', where: 'app',
      description: "Fold or unfold all of the chat's blocks; clicking a block's title folds one." },
    { name: '/quit', usage: '', where: 'app', needs: 'desktop',
      description: 'Quit this app; JARVIS keeps running.' },
    { name: '/exit', usage: '', where: 'app', needs: 'desktop',
      description: 'Quit this app; JARVIS keeps running.' },
    { name: '/status', usage: '[full]', where: 'app', needs: 'approve',
      description: "Check JARVIS's health: model providers, engines, voice, safety, work, session. full also checks for server updates (goes online)." },
    { name: '/providers', usage: '[add|remove|move|edit]', where: 'app', needs: 'manage',
      description: 'Open the providers list; add, remove, move or edit one.' },
    { name: '/model', usage: '[name]', where: 'app', needs: 'approve',
      description: 'Show the current LLM model.' },
    { name: '/settings', usage: '', where: 'app', needs: 'manage',
      description: 'Open the settings (runtime config).' },
    { name: '/setups', usage: '', where: 'app', needs: 'approve',
      description: 'Say how many servers wait for setup; they are filled in on the host.' },
    { name: '/tasks', usage: '', where: 'app', needs: 'approve',
      description: 'What JARVIS is doing now: requests, running tasks with their latest output, timers, and what waits on you.' },
    { name: '/attach', usage: '[clear]', where: 'app', needs: 'approve',
      description: 'Attach images or PDFs to your next message; clear drops them.' },
    { name: '/new', usage: '[title]', where: 'daemon', needs: 'approve',
      description: 'Start a new session; optional title.' },
    { name: '/sessions', usage: '', where: 'daemon',
      description: 'List sessions (current row marked with an asterisk).' },
    { name: '/switch', usage: '<id>', where: 'daemon', needs: 'approve',
      description: 'Switch session by short id prefix.' },
    { name: '/rename', usage: '[<id>] <title>', where: 'daemon', needs: 'manage',
      description: "Rename the session you're in, or the one whose id starts with <id>." },
    { name: '/delete', usage: '<id>', where: 'daemon', needs: 'manage',
      description: 'Delete session by unique id prefix.' },
    { name: '/context', usage: '', where: 'daemon',
      description: 'Show what filled the last prompt, part by part, and how much room is left.' },
    { name: '/stop', usage: '[all|<n>]', where: 'daemon',
      description: "Stop this session's work; its running tasks keep running. /stop all ends them too, /stop <n> ends task n." },
];

const NEEDS_CLASS = { approve: 'can-approve', manage: 'can-manage', desktop: 'desktop' };

/// The commands a page offers; `has(cls)` says whether the body has a class.
function commandsFor(commands, has) {
    return commands.filter((c) => !c.needs || has(NEEDS_CLASS[c.needs]));
}

/// True while `text` is still a bare, unfinished `/command` token: a space
/// means the arguments have started.
function isCommandPrefix(text) {
    return text.startsWith('/') && !/\s/.test(text);
}

/// Names starting with `prefix`, case-insensitively, in catalog order.
function matchCommands(prefix, names) {
    if (!isCommandPrefix(prefix)) return [];
    const low = prefix.toLowerCase();
    return names.filter((name) => name.toLowerCase().startsWith(low));
}

/// The rows the suggestion popup shows for `text`.
function filterCommands(text, commands) {
    if (!isCommandPrefix(text)) return [];
    const low = text.toLowerCase();
    return commands.filter((c) => c.name.toLowerCase().startsWith(low));
}

/// Tab completion with cycling, as the TUI's: the first Tab on a prefix gives
/// the first match plus a space (where the arguments go); Tab again on the
/// line it produced walks the other matches and wraps. Anything typed breaks
/// the chain, since the line no longer equals what was last emitted.
class CommandCompleter {
    constructor(commands) {
        this._commands = [...commands];
        this.reset();
    }

    reset() {
        this._matches = [];
        this._index = 0;
        this._emitted = null;
    }

    cycling(text) {
        return this._matches.length > 0 && text === this._emitted;
    }

    complete(text) {
        if (this.cycling(text)) {
            this._index = (this._index + 1) % this._matches.length;
        } else if (isCommandPrefix(text)) {
            const matches = matchCommands(text, this._commands);
            if (!matches.length) {
                this.reset();
                return null;
            }
            this._matches = matches;
            this._index = 0;
        } else {
            this.reset();
            return null;
        }
        this._emitted = this._matches[this._index] + ' ';
        return this._emitted;
    }
}

/// `/name args…` as `{name, args}`: the name lower-cased, the arguments the
/// rest of the line, trimmed. Null for a line that is not a command.
function parseCommandLine(text) {
    const line = text.trim();
    if (!line.startsWith('/')) return null;
    const match = /^(\S+)\s*([\s\S]*)$/.exec(line);
    return { name: match[1].toLowerCase(), args: match[2].trim() };
}

/// `--flag value` pairs, as Project-JARVIS's parse_flags reads them.
function parseFlags(tokens) {
    const flags = {};
    for (let i = 0; i < tokens.length; i++) {
        if (tokens[i].startsWith('--') && i + 1 < tokens.length) {
            flags[tokens[i].slice(2)] = tokens[i + 1];
            i++;
        }
    }
    return flags;
}

const VISION_WORDS = new Map([
    ['true', true], ['on', true], ['yes', true], ['1', true],
    ['false', false], ['off', false], ['no', false], ['0', false],
]);

/// `--vision` taken out of `tokens`, as Project-JARVIS's take_vision_flag
/// reads it: bare is on, a following true/false says which. `vision` is
/// undefined when the line has none.
function takeVisionFlag(tokens) {
    const rest = [];
    let vision;
    for (let i = 0; i < tokens.length; i++) {
        if (tokens[i] !== '--vision') {
            rest.push(tokens[i]);
            continue;
        }
        const said = VISION_WORDS.get((tokens[i + 1] || '').toLowerCase());
        vision = said ?? true;
        if (said !== undefined) i++;
    }
    return { rest, vision };
}

const KEY_FLAG = /(^|\s)--key(\s|$)/;

/// True when a line carries an API key (`--key <value>`).
function hasKeyFlag(text) {
    return KEY_FLAG.test(text);
}

/// The line as the chat shows it: a `--key` value never appears on screen.
function maskKeys(text) {
    return text.replace(/(^|\s)(--key\s+)\S+/g, '$1$2••••');
}

/// The commands whose name, usage or description mention every word of
/// `query`, ignoring case.
function searchCommands(commands, query) {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return commands.filter((c) => {
        const text = `${c.name} ${c.usage} ${c.description}`.toLowerCase();
        return words.every((w) => text.includes(w));
    });
}

if (typeof module !== 'undefined') {
    module.exports = {
        JARVIS_COMMANDS,
        commandsFor,
        isCommandPrefix,
        matchCommands,
        filterCommands,
        CommandCompleter,
        parseCommandLine,
        parseFlags,
        takeVisionFlag,
        hasKeyFlag,
        maskKeys,
        searchCommands,
    };
}
