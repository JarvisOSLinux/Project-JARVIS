'use strict';

// Minimal, dependency-free markdown -> HTML renderer for JARVIS chat bubbles.
// Handles headings, lists, bold/italic, inline code, fenced code blocks, and
// links. Re-parses the full accumulated text on every streamed chunk, so an
// unclosed fence/emphasis marker just renders as literal text (or an
// already-open code block) until the closing marker arrives.
(function (global) {
    function escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    const SAFE_URL_SCHEME = /^(https?:|mailto:)/i;

    function sanitizeUrl(url) {
        const trimmed = url.trim();
        if (SAFE_URL_SCHEME.test(trimmed) || trimmed.startsWith('/') || trimmed.startsWith('#')) {
            return trimmed;
        }
        return null;
    }

    // A NUL-delimited placeholder can't collide with ordinary chat text (NUL
    // never appears in real text and escapeHtml never produces it), so it
    // safely protects inline code spans from the bold/italic/link passes
    // that run afterward. A plain " N " placeholder would instead collide
    // with ordinary text like "I have 3 items".
    const PLACEHOLDER = /\x00(\d+)\x00/g;

    function renderInline(escapedText) {
        const codeSpans = [];
        let text = escapedText.replace(/`([^`]+)`/g, (_, code) => {
            codeSpans.push(code);
            return '\x00' + (codeSpans.length - 1) + '\x00';
        });

        text = text
            .replace(/\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g, (whole, label, url) => {
                const safe = sanitizeUrl(url);
                return safe
                    ? '<a href="' + safe + '" rel="noopener noreferrer">' + label + '</a>'
                    : label;
            })
            .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_, a, b) => '<strong>' + (a ?? b) + '</strong>')
            .replace(/\*([^*]+)\*|_([^_]+)_/g, (_, a, b) => '<em>' + (a ?? b) + '</em>');

        return text.replace(PLACEHOLDER, (_, i) => '<code>' + codeSpans[Number(i)] + '</code>');
    }

    function renderMarkdown(raw) {
        const lines = escapeHtml(raw).split('\n');
        const html = [];
        let listType = null;
        let i = 0;

        function closeList() {
            if (listType) {
                html.push('</' + listType + '>');
                listType = null;
            }
        }

        while (i < lines.length) {
            const line = lines[i];

            const fenceMatch = line.match(/^```(\w*)\s*$/);
            if (fenceMatch) {
                closeList();
                const lang = fenceMatch[1];
                const codeLines = [];
                i++;
                while (i < lines.length && !/^```\s*$/.test(lines[i])) {
                    codeLines.push(lines[i]);
                    i++;
                }
                i++; // consume closing fence, or run off the end if unclosed mid-stream
                const langAttr = lang ? ' data-lang="' + escapeHtml(lang) + '"' : '';
                html.push('<pre' + langAttr + '><code>' + codeLines.join('\n') + '</code></pre>');
                continue;
            }

            const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
            if (headingMatch) {
                closeList();
                const level = headingMatch[1].length;
                html.push('<h' + level + '>' + renderInline(headingMatch[2]) + '</h' + level + '>');
                i++;
                continue;
            }

            const ulMatch = line.match(/^[-*]\s+(.*)$/);
            const olMatch = line.match(/^\d+\.\s+(.*)$/);
            if (ulMatch || olMatch) {
                const wantType = ulMatch ? 'ul' : 'ol';
                if (listType !== wantType) {
                    closeList();
                    html.push('<' + wantType + '>');
                    listType = wantType;
                }
                html.push('<li>' + renderInline(ulMatch ? ulMatch[1] : olMatch[1]) + '</li>');
                i++;
                continue;
            }

            closeList();

            if (line.trim() === '') {
                i++;
                continue;
            }

            const paraLines = [line];
            i++;
            while (
                i < lines.length &&
                lines[i].trim() !== '' &&
                !/^```/.test(lines[i]) &&
                !/^#{1,6}\s+/.test(lines[i]) &&
                !/^[-*]\s+/.test(lines[i]) &&
                !/^\d+\.\s+/.test(lines[i])
            ) {
                paraLines.push(lines[i]);
                i++;
            }
            html.push('<p>' + renderInline(paraLines.join(' ')) + '</p>');
        }
        closeList();
        return html.join('');
    }

    global.renderMarkdown = renderMarkdown;
})(window);
