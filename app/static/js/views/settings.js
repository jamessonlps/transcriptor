// View "Configurações" — gestão de modelos (baixar, ver, remover).

import { $, el, show, hide, fmtBytes, cloneTemplate, toast, confirmModal } from '../ui.js';
import { listModels, deleteModel, openStream, getConfig } from '../api.js';

export async function mount(container, { config } = {}) {
    const frag = cloneTemplate('tpl-settings');
    container.appendChild(frag);
    const root = container;

    const downloadCtrls = new Map();  // key -> { close, row, kind }

    el('refresh-btn', root).addEventListener('click', () => refresh());

    await refresh();

    async function refresh() {
        const list = el('whisper-list', root);
        const diList = el('diarization-list', root);

        try {
            const data = await listModels();

            el('storage-total', root).textContent = fmtBytes(data.total_bytes);
            el('storage-path', root).textContent = data.cache_dir;
            el('cache-dir', root).textContent = data.cache_dir;

            renderModelGroup(list, data.whisper);
            renderModelGroup(diList, data.diarization);

            // Aviso de token pra diarização (só se tem modelo pyannote e não tem token)
            const tokenWarn = el('diarization-token-warn', root);
            const cfg = config || await getConfig();
            const anyDiariNeedsToken = data.diarization.some(m => !m.downloaded);
            if (anyDiariNeedsToken && !cfg.diarization_available) {
                show(tokenWarn);
            } else {
                hide(tokenWarn);
            }
        } catch (err) {
            list.innerHTML = `<div class="alert-error">Falha ao carregar modelos: ${err.message}</div>`;
        }
    }

    function renderModelGroup(container, models) {
        container.innerHTML = '';
        models.forEach(m => {
            const row = buildRow(m);
            container.appendChild(row);
        });
    }

    function buildRow(model) {
        const frag = cloneTemplate('tpl-model-row');
        const row = frag.querySelector('.model-row');
        row.dataset.key = model.key;

        el('label', row).textContent = model.label;
        el('description', row).textContent = model.description;
        const sizeText = model.downloaded
            ? fmtBytes(model.size_bytes_actual)
            : `~${model.size_mb_estimate} MB`;
        el('size', row).textContent = sizeText;
        el('speed', row).textContent = model.speed_label;

        const tagEl = el('tag', row);
        if (model.tag) {
            tagEl.textContent = model.tag.toUpperCase();
            tagEl.classList.add(model.tag === 'novo' ? 'tag-new' : 'tag-best');
            show(tagEl);
        }

        const statusEl = el('status', row);
        if (model.downloaded) {
            statusEl.textContent = '✓ Baixado';
            statusEl.classList.add('status-ok');
            show(el('delete-btn', row));
        } else {
            statusEl.textContent = '○ Disponível';
            statusEl.classList.add('status-off');
            show(el('download-btn', row));
        }

        // Wiring
        el('download-btn', row).addEventListener('click', () => startDownload(model.key, row));
        el('cancel-btn', row).addEventListener('click', () => cancelDownload(model.key));
        el('delete-btn', row).addEventListener('click', () => askDelete(model));

        return row;
    }

    function startDownload(key, row) {
        if (downloadCtrls.has(key)) return;

        hide(el('download-btn', row));
        show(el('cancel-btn', row));
        const progressEl = el('progress', row);
        show(progressEl);
        el('progress-bar', progressEl).style.width = '0%';
        el('progress-status', progressEl).textContent = 'Conectando…';
        el('progress-bytes', progressEl).textContent = '';

        const ctrl = openStream(
            `/api/models/${encodeURIComponent(key)}/download`,
            (event) => handleDownloadEvent(event, key, row),
            (e) => {
                // EventSource só dispara onerror quando o servidor fecha sem 'done',
                // o que normalmente significa conclusão. Verificamos via refresh().
                downloadCtrls.delete(key);
                refresh();
            },
        );
        downloadCtrls.set(key, { ctrl, row });
    }

    function handleDownloadEvent(event, key, row) {
        const progressEl = el('progress', row);

        switch (event.type) {
            case 'start':
                el('progress-status', progressEl).textContent = 'Baixando…';
                break;
            case 'progress': {
                const pct = event.total ? Math.min(100, (event.downloaded / event.total) * 100) : 0;
                el('progress-bar', progressEl).style.width = `${pct}%`;
                el('progress-status', progressEl).textContent = event.stalled
                    ? 'Sem progresso (pode demorar para começar)…'
                    : `${pct.toFixed(0)}%`;
                el('progress-bytes', progressEl).textContent =
                    `${fmtBytes(event.downloaded)} / ${fmtBytes(event.total)}`;
                break;
            }
            case 'done': {
                el('progress-bar', progressEl).style.width = '100%';
                el('progress-status', progressEl).textContent = 'Concluído';
                el('progress-bytes', progressEl).textContent = fmtBytes(event.size_bytes);
                const c = downloadCtrls.get(key);
                if (c) { c.ctrl.close(); downloadCtrls.delete(key); }
                toast(`Modelo "${event.label}" baixado com sucesso.`, { type: 'success' });
                setTimeout(refresh, 600);
                break;
            }
            case 'error':
                el('progress-status', progressEl).textContent = 'Erro';
                toast(event.message, { type: 'error', timeout: 8000 });
                const c = downloadCtrls.get(key);
                if (c) { c.ctrl.close(); downloadCtrls.delete(key); }
                setTimeout(refresh, 600);
                break;
        }
    }

    function cancelDownload(key) {
        const c = downloadCtrls.get(key);
        if (!c) return;
        c.ctrl.close();
        downloadCtrls.delete(key);
        toast(
            `Download cancelado no cliente. O servidor pode continuar até o ` +
            `próximo ponto de checagem — atualize a página em alguns segundos.`,
            { type: 'info', timeout: 6000 }
        );
        refresh();
    }

    async function askDelete(model) {
        const sz = fmtBytes(model.size_bytes_actual);
        const ok = await confirmModal({
            title: `Remover "${model.label}"?`,
            message:
                `Isso vai liberar ${sz} no disco.\n` +
                `Você pode baixar novamente a qualquer momento.`,
            confirmText: 'Remover',
            cancelText: 'Cancelar',
            danger: true,
        });
        if (ok) doDelete(model);
    }

    async function doDelete(model) {
        try {
            const res = await deleteModel(model.key);
            if (res.removed) {
                toast(`"${model.label}" removido (${fmtBytes(model.size_bytes_actual)} liberados).`, { type: 'success' });
            } else {
                toast(`Nada a remover (${res.reason}).`, { type: 'info' });
            }
            refresh();
        } catch (err) {
            toast(`Falha ao remover: ${err.message}`, { type: 'error' });
        }
    }

    return () => {
        // Fecha todos os downloads em curso ao sair
        for (const { ctrl } of downloadCtrls.values()) {
            try { ctrl.close(); } catch {}
        }
        downloadCtrls.clear();
    };
}
