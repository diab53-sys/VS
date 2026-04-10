// injector.js — auto-enters the queue when READY/ADMITTED; polls queue state.
// Runs in page context after bridge.js.
(function () {
    'use strict';

    var _lastState  = '';
    var _lastReport = 0;
    var REPORT_EVERY = 10000; // ms

    function _enterQueue() {
        // Try common "Enter" / "Proceed" button labels
        var candidates = document.querySelectorAll(
            'button, a[role="button"], input[type="button"], input[type="submit"]'
        );
        for (var i = 0; i < candidates.length; i++) {
            var el   = candidates[i];
            var text = (el.innerText || el.value || el.textContent || '').trim().toLowerCase();
            if (text === 'enter'    ||
                text === 'proceed'  ||
                text === 'continue' ||
                text === 'go'       ||
                text.indexOf('enter') === 0) {
                try { el.click(); } catch (e) {}
                return;
            }
        }
    }

    function _pollQueue() {
        try {
            var qi    = window.queueinfo || {};
            var state = qi.state || '';
            var now   = Date.now();

            // State change → report immediately
            if (state !== _lastState) {
                _lastState = state;
                if (window.__qbot_state) {
                    var ai = window.admissionInfo || null;
                    if (!ai && qi.response) {
                        try { ai = JSON.parse(qi.response).admissionInfo; } catch (e) {}
                    }
                    window.__qbot_state(state, {
                        waitingTime:   ai ? (ai.waitingTime   || '') : '',
                        queuePosition: ai ? (ai.queuePosition || '') : '',
                        canEnter:      ai ? (ai.canEnter      || '') : '',
                    });
                }
            }

            // Periodic heartbeat
            if (now - _lastReport > REPORT_EVERY) {
                _lastReport = now;
                if (window.__qbot_send) {
                    window.__qbot_send('/ping', {
                        slot_id: window.__FIFA_BOT_INSTANCE_ID || 0,
                        url:     location.href.substring(0, 120),
                        state:   state,
                    });
                }
            }

            // Auto-enter on READY or ADMITTED
            if (state === 'READY' || state === 'ADMITTED') {
                _enterQueue();
            }
        } catch (e) {}
    }

    // Poll every 2 seconds
    setInterval(_pollQueue, 2000);
})();
