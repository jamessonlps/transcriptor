// Entry point — bootstrap da app: carrega config, registra rotas, inicia.

import { register, start } from './router.js';
import { getConfig } from './api.js';
import { mount as mountTranscribe } from './views/transcribe.js';
import { mount as mountSettings } from './views/settings.js';
import { toast } from './ui.js';

// Re-fetch da config a cada navegação: estado do token pode ter mudado em
// Configurações e queremos refletir isso em Transcrever sem reload.
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
    const footerYear = document.querySelector('[data-el="footer-year"]');
    if (footerYear) footerYear.textContent = String(new Date().getFullYear());

    register('/', async (container) => mountTranscribe(container, { config: await fetchConfig() }));
    register('/settings', async (container) => mountSettings(container, { config: await fetchConfig() }));

    start();
}

boot();
