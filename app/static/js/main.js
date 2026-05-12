// Entry point — bootstrap da app: carrega config, registra rotas, inicia.

import { register, start } from './router.js';
import { getConfig } from './api.js';
import { mount as mountTranscribe } from './views/transcribe.js';
import { mount as mountSettings } from './views/settings.js';
import { toast } from './ui.js';

async function boot() {
    let config = null;
    try {
        config = await getConfig();
    } catch (err) {
        // Não é fatal — algumas views podem funcionar sem config; outras vão pedir.
        toast(`Não foi possível conectar ao servidor: ${err.message}`,
            { type: 'error', timeout: 8000 });
    }

    const footerYear = document.querySelector('[data-el="footer-year"]');
    if (footerYear) footerYear.textContent = String(new Date().getFullYear());

    register('/', (container) => mountTranscribe(container, { config }));
    register('/settings', (container) => mountSettings(container, { config }));

    start();
}

boot();
