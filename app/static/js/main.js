// Entry point — bootstrap the app:
//
//   1. hydrate the top-nav icons (they live outside the SPA's mounted view)
//   2. fetch /api/config on every navigation so the SPA always reflects the
//      latest token state without forcing a page reload
//   3. register routes and start the router

import { register, start } from './router.js';
import { getConfig } from './api.js';
import { hydrateIcons } from './components/icons.js';
import { initRuntimeBadge } from './components/runtime-badge.js';
import { mount as mountTranscribe } from './views/transcribe.js';
import { mount as mountSettings } from './views/settings.js';
import { toast } from './ui.js';

async function fetchConfig() {
    try {
        return await getConfig();
    } catch (err) {
        toast(`Não foi possível conectar ao servidor: ${err.message}`,
            { type: 'error', timeout: 8000 });
        return null;
    }
}

async function boot() {
    // Static chrome (top nav, brand mark) — these are real DOM, not
    // template clones, so the auto-hydration in ``cloneTemplate`` doesn't
    // reach them. Hydrate explicitly once at boot.
    hydrateIcons(document);

    const footerYear = document.querySelector('[data-el="footer-year"]');
    if (footerYear) footerYear.textContent = String(new Date().getFullYear());

    // Não bloqueia o resto do boot — o badge popula assincronamente.
    initRuntimeBadge();

    register('/', async (container) => mountTranscribe(container, { config: await fetchConfig() }));
    register('/settings', async (container) => mountSettings(container, { config: await fetchConfig() }));

    start();
}

boot();
