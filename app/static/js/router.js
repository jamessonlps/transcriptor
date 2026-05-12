// Roteador SPA mínimo baseado em hash.
// Suporta rotas simples (sem params) e cleanup ao trocar de view.

const routes = new Map();
let currentCleanup = null;
let currentRoute = null;

export function register(path, mountFn) {
    routes.set(path, mountFn);
}

export function go(path) {
    location.hash = `#${path}`;
}

function currentPath() {
    const h = location.hash || '#/';
    return h.startsWith('#') ? h.slice(1) : h;
}

async function handleRoute() {
    const path = currentPath();
    const mountFn = routes.get(path) || routes.get('/');
    if (!mountFn) return;

    // Cleanup da view anterior
    if (currentCleanup) {
        try { currentCleanup(); } catch (e) { console.error('cleanup error', e); }
        currentCleanup = null;
    }

    const app = document.getElementById('app');
    // Fade out leve
    app.classList.add('switching');
    await new Promise(r => setTimeout(r, 80));
    app.innerHTML = '';

    // Atualiza nav tabs ativos
    document.querySelectorAll('[data-route]').forEach(a => {
        a.classList.toggle('active', a.dataset.route === path);
    });

    try {
        const cleanup = await mountFn(app);
        if (typeof cleanup === 'function') currentCleanup = cleanup;
        currentRoute = path;
    } catch (err) {
        console.error('Erro ao montar rota', path, err);
        app.innerHTML = `
            <div class="card p-6 border-red-500/40 bg-red-500/5">
                <p class="font-semibold text-red-300 mb-1">Erro ao carregar página</p>
                <p class="text-sm text-slate-400 font-mono">${String(err.message || err)}</p>
            </div>
        `;
    }

    app.classList.remove('switching');
}

export function start() {
    window.addEventListener('hashchange', handleRoute);
    // Intercepta cliques em links [data-route] para preservar histórico nativo
    document.addEventListener('click', (e) => {
        const link = e.target.closest('a[data-route]');
        if (!link) return;
        const route = link.dataset.route;
        if (route && route !== currentPath()) {
            e.preventDefault();
            go(route);
        }
    });

    if (!location.hash) location.hash = '#/';
    handleRoute();
}
