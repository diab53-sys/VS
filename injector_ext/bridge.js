// bridge.js — relays captcha tokens and queue events to the local solve server.
// Injected into every page as an init script by queue_farmer.py.
(function () {
    'use strict';

    var PORT     = window.__FIFA_BOT_PORT     || 9099;
    var INSTANCE = window.__FIFA_BOT_INSTANCE_ID || 0;

    // POST JSON to the local solve server (fire-and-forget).
    window.__qbot_send = function (endpoint, data) {
        try {
            fetch('http://127.0.0.1:' + PORT + endpoint, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(data),
            }).catch(function () {});
        } catch (e) {}
    };

    // Called by injector.js / page code when a captcha token is available.
    window.__qbot_captcha = function (token) {
        console.log('CAPTCHA_SOLVED:' + token);
        window.__qbot_send('/captcha', { slot_id: INSTANCE, token: token });
    };

    // Called to report queue state changes.
    window.__qbot_state = function (state, info) {
        window.__qbot_send('/state', { slot_id: INSTANCE, state: state, info: info || {} });
    };

    // Hook into DataDome: intercept the script tag that loads the DD tag and
    // capture any CAPTCHA= query param from its src before it fires abort().
    var _ddObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            m.addedNodes.forEach(function (node) {
                if (node.tagName === 'SCRIPT') {
                    var src = node.src || '';
                    var match = src.match(/[?&]CAPTCHA=([^&]+)/i);
                    if (match) {
                        var token = decodeURIComponent(match[1]);
                        window.__qbot_captcha(token);
                    }
                }
            });
        });
    });

    try {
        _ddObserver.observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {}
})();
