// View "Configurações" — gestão do token Hugging Face + gestão de modelos.

import { $, el, show, hide, fmtBytes, cloneTemplate, toast, confirmModal } from '../ui.js';
import {
    deleteHfToken,
    deleteModel,
    getConfig,
    getHfAccess,
    getHfStatus,
    listModels,
    openStream,
    setHfToken,
} from '../api.js';

export async function mount(container) {
    const frag = cloneTemplate('tpl-settings');
    container.appendChild(frag);
    const root = container;

    const downloadCtrls = new Map();  // model key -> { ctrl, row }

    // Estado local do card do HF. Mantemos pra:
    //   - decidir se o wizard está aberto pra editar mesmo com token válido
    //   - evitar double-fetch quando recheck e refresh disparam em sequência
    const hfState = {
        status: null,         // { configured, valid, source, username, masked, error, config_path }
        access: null,         // { models: [...], has_token }
        editing: false,       // true = mostrar o wizard mesmo com token válido
        saving: false,
    };

    el('refresh-btn', root).addEventListener('click', () => refresh());

    // Wiring do card HF
    wireHfCard();

    await Promise.all([refresh(), loadHf()]);

    // ===========================================================================
    // Lista de modelos (whisper + diarização)
    // ===========================================================================

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
            const cfg = await getConfig().catch(() => null);
            const anyDiariNeedsToken = data.diarization.some(m => !m.downloaded);
            if (anyDiariNeedsToken && cfg && !cfg.diarization_available) {
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
            () => {
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
            case 'error': {
                el('progress-status', progressEl).textContent = 'Erro';
                toast(event.message, { type: 'error', timeout: 9000 });
                const c = downloadCtrls.get(key);
                if (c) { c.ctrl.close(); downloadCtrls.delete(key); }
                // Token/acesso: provavelmente o usuário precisa rever o card HF.
                if (event.error_kind === 'no_token' || event.error_kind === 'no_access') {
                    loadHf({ openWizard: event.error_kind === 'no_token' });
                    scrollToHfCard();
                }
                setTimeout(refresh, 600);
                break;
            }
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

    // ===========================================================================
    // Card Hugging Face (token + acesso aos modelos gated)
    // ===========================================================================

    function wireHfCard() {
        const card = el('hf-card', root);
        const input = el('hf-input', card);
        const saveBtn = el('hf-save', card);
        const toggleVisBtn = el('hf-toggle-vis', card);
        const toggleEditBtn = el('hf-toggle-edit', card);
        const cancelEditBtn = el('hf-cancel-edit', card);
        const recheckBtn = el('hf-recheck', card);
        const deleteBtn = el('hf-delete', card);

        // Habilita o "Salvar" só quando o input tem algo (não validamos formato
        // aqui — o backend dá um erro claro se o token não começar com hf_).
        input.addEventListener('input', () => {
            saveBtn.disabled = !input.value.trim() || hfState.saving;
            hide(el('hf-input-msg', card));
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !saveBtn.disabled) saveBtn.click();
        });

        // Toggle senha <-> texto
        toggleVisBtn.addEventListener('click', () => {
            input.type = input.type === 'password' ? 'text' : 'password';
        });

        // Abre o wizard (modo "editar")
        toggleEditBtn.addEventListener('click', () => {
            hfState.editing = true;
            input.value = '';
            renderHfCard();
            setTimeout(() => input.focus(), 50);
        });
        cancelEditBtn.addEventListener('click', () => {
            hfState.editing = false;
            input.value = '';
            renderHfCard();
        });

        saveBtn.addEventListener('click', () => saveHfToken());
        recheckBtn.addEventListener('click', () => loadHf({ keepEditing: false }));
        deleteBtn.addEventListener('click', () => askDeleteHfToken());
    }

    async function loadHf({ openWizard = false, keepEditing = false } = {}) {
        if (openWizard) hfState.editing = true;
        if (!keepEditing) {
            // O default ao recarregar é fechar o wizard se houver token válido
            // (e abrir se não houver).
        }
        renderHfCard({ loading: true });
        try {
            const [status, access] = await Promise.all([
                getHfStatus(),
                getHfAccess().catch(() => ({ models: [], has_token: false })),
            ]);
            hfState.status = status;
            hfState.access = access;
            // Sem token configurado -> sempre abre o wizard.
            // Com token configurado + válido -> fechado por default; usuário abre via "Trocar".
            // Com token inválido -> aberto pra correção.
            if (!status.configured) hfState.editing = true;
            else if (status.valid === false) hfState.editing = true;
            else if (!openWizard && !keepEditing) hfState.editing = false;
            renderHfCard();
        } catch (err) {
            renderHfCard({ error: err.message });
        }
    }

    function renderHfCard({ loading = false, error = null } = {}) {
        const card = el('hf-card', root);
        const badge = el('hf-status-badge', card);
        const title = el('hf-status-title', card);
        const detail = el('hf-status-detail', card);
        const toggleEditBtn = el('hf-toggle-edit', card);
        const wizard = el('hf-wizard', card);
        const accessSection = el('hf-access-section', card);
        const footer = el('hf-footer', card);
        const cancelEditBtn = el('hf-cancel-edit', card);
        const skeleton = el('hf-loading-skeleton', card);

        // Estado: loading. Mostra o skeleton pra o card nao parecer "vazio"
        // enquanto esperamos pela resposta da API do HF (whoami + access).
        if (loading) {
            badge.className = 'hf-status-badge state-loading';
            badge.innerHTML = '<div class="spinner"></div>';
            title.textContent = 'Verificando token…';
            detail.textContent = 'Consultando huggingface.co — geralmente leva 1 a 2 segundos.';
            hide(toggleEditBtn); hide(wizard); hide(accessSection); hide(footer);
            if (skeleton) show(skeleton);
            return;
        }
        // Apos qualquer resposta (sucesso ou erro), o skeleton ja nao faz sentido.
        if (skeleton) hide(skeleton);
        if (error) {
            badge.className = 'hf-status-badge state-err';
            badge.innerHTML = iconAlert();
            title.textContent = 'Falha ao verificar';
            detail.textContent = error;
            hide(toggleEditBtn); hide(wizard); hide(accessSection); hide(footer);
            return;
        }

        const status = hfState.status || {};
        const access = hfState.access || { models: [] };
        const configured = !!status.configured;
        const valid = status.valid === true;
        const invalid = status.configured && status.valid === false;

        // Header (status row)
        if (!configured) {
            badge.className = 'hf-status-badge state-warn';
            badge.innerHTML = iconKey();
            title.textContent = 'Token não configurado';
            detail.textContent = 'Necessário para identificação de falantes (diarização). Siga o passo a passo abaixo.';
        } else if (invalid) {
            badge.className = 'hf-status-badge state-err';
            badge.innerHTML = iconAlert();
            title.textContent = 'Token inválido';
            detail.textContent = status.error || 'O token não foi aceito pelo Hugging Face. Gere um novo e cole abaixo.';
        } else if (valid) {
            const allOk = access.models.length > 0 && access.models.every(m => m.accessible);
            if (allOk) {
                badge.className = 'hf-status-badge state-ok';
                badge.innerHTML = iconCheck();
                title.textContent = `Pronto · ${status.username || 'conta autenticada'}`;
                detail.textContent = 'Token válido e termos aceitos para todos os modelos protegidos.';
            } else {
                badge.className = 'hf-status-badge state-warn';
                badge.innerHTML = iconAlert();
                const missing = access.models.filter(m => !m.accessible).length;
                title.textContent = `Conectado como ${status.username || 'conta'}, mas falta autorização`;
                detail.textContent = `Token válido. Falta aceitar os termos de ${missing} modelo${missing > 1 ? 's' : ''} (veja abaixo).`;
            }
        } else {
            // status.valid === null e configurou — provavelmente ainda em loading da chamada whoami
            badge.className = 'hf-status-badge state-loading';
            badge.innerHTML = '<div class="spinner"></div>';
            title.textContent = 'Verificando token…';
            detail.textContent = '';
        }

        // Botão "Trocar" só aparece quando já há um token configurado e estamos
        // fora do modo edição.
        if (configured && !hfState.editing) show(toggleEditBtn); else hide(toggleEditBtn);

        // Wizard: visível quando não tem token, quando o token é inválido, ou
        // quando o usuário clicou em "Trocar".
        const showWizard = hfState.editing || !configured;
        if (showWizard) {
            show(wizard);
            renderAccessList(el('hf-access-list-wizard', card), access.models, status);
            // Caminho do arquivo de config (informativo)
            const configPath = el('hf-config-path', card);
            if (configPath && status.config_path) configPath.textContent = status.config_path;
            // Cancel só faz sentido se já existe um token salvo (caso contrário,
            // não tem o que cancelar — não há estado prévio)
            if (configured && !invalid) show(cancelEditBtn); else hide(cancelEditBtn);
        } else {
            hide(wizard);
        }

        // Lista de acesso compacta + footer com infos do token (quando há token)
        if (configured && !showWizard) {
            show(accessSection);
            renderAccessList(el('hf-access-list', card), access.models, status);
            show(footer);
            const sourcePill = el('hf-source-label', card);
            if (status.source === 'ui') {
                sourcePill.textContent = 'Salvo aqui na UI';
                sourcePill.className = 'hf-source-pill source-ui';
            } else if (status.source === 'env') {
                sourcePill.textContent = 'Carregado do .env';
                sourcePill.className = 'hf-source-pill source-env';
            } else {
                sourcePill.textContent = '';
                sourcePill.className = 'hf-source-pill';
            }
            el('hf-masked', card).textContent = status.masked || '';
        } else {
            hide(accessSection);
            hide(footer);
        }
    }

    function renderAccessList(container, models, status) {
        if (!container) return;
        container.innerHTML = '';
        if (!models || models.length === 0) {
            container.innerHTML = `<p class="text-xs text-slate-500">Nenhum modelo protegido a checar.</p>`;
            return;
        }
        models.forEach(m => {
            const frag = cloneTemplate('tpl-hf-access-row');
            const row = frag.querySelector('.hf-access-row');
            row.dataset.repoId = m.repo_id;

            el('label', row).textContent = m.label;
            el('repo-id', row).textContent = m.repo_id;

            const iconEl = el('state-icon', row);
            const statusEl = el('status', row);
            const linkEl = el('open-terms', row);
            linkEl.href = m.url;

            if (m.accessible) {
                iconEl.className = 'hf-access-icon state-ok';
                iconEl.innerHTML = iconCheck();
                statusEl.textContent = 'Acesso liberado';
                statusEl.className = 'hf-access-status state-ok';
                hide(linkEl);
            } else if (!status || !status.configured) {
                iconEl.className = 'hf-access-icon state-muted';
                iconEl.innerHTML = iconLock();
                statusEl.textContent = 'Aguardando token';
                statusEl.className = 'hf-access-status state-muted';
                show(linkEl);
            } else if (status && status.valid === false) {
                iconEl.className = 'hf-access-icon state-muted';
                iconEl.innerHTML = iconLock();
                statusEl.textContent = 'Token inválido';
                statusEl.className = 'hf-access-status state-muted';
                show(linkEl);
            } else {
                // Token válido, mas sem acesso ao repo
                iconEl.className = 'hf-access-icon state-warn';
                iconEl.innerHTML = iconLock();
                if (m.reason === 'gated') {
                    statusEl.textContent = 'Termos não aceitos';
                } else if (m.reason === 'unauthorized') {
                    statusEl.textContent = 'Sem permissão';
                } else {
                    statusEl.textContent = 'Sem acesso';
                }
                statusEl.className = 'hf-access-status state-warn';
                show(linkEl);
            }
            container.appendChild(row);
        });
    }

    async function saveHfToken() {
        const card = el('hf-card', root);
        const input = el('hf-input', card);
        const saveBtn = el('hf-save', card);
        const saveLabel = el('hf-save-label', card);
        const msg = el('hf-input-msg', card);

        const token = input.value.trim();
        if (!token) return;

        hide(msg);
        hfState.saving = true;
        saveBtn.disabled = true;
        const originalLabel = saveLabel.textContent;
        saveLabel.textContent = 'Salvando…';

        try {
            await setHfToken(token);
            input.value = '';
            input.type = 'password';
            hfState.editing = false;
            toast('Token salvo e validado com sucesso.', { type: 'success' });
            await loadHf();
            // Refresh dos modelos também — alguns botões dependem de token.
            refresh();
        } catch (err) {
            // Mostra inline (mais útil que toast pra erro de validação)
            msg.textContent = err.message;
            msg.className = 'hf-input-msg state-err';
            show(msg);
        } finally {
            hfState.saving = false;
            saveBtn.disabled = !input.value.trim();
            saveLabel.textContent = originalLabel;
        }
    }

    async function askDeleteHfToken() {
        const ok = await confirmModal({
            title: 'Remover token Hugging Face?',
            message:
                'A identificação de falantes deixará de funcionar até que ' +
                'um novo token seja configurado.\n\n' +
                'Se você tem um token no .env, voltaremos a usá-lo automaticamente.',
            confirmText: 'Remover',
            cancelText: 'Cancelar',
            danger: true,
        });
        if (!ok) return;
        try {
            const res = await deleteHfToken();
            toast(
                res.configured
                    ? 'Token removido. Voltamos pro token do .env.'
                    : 'Token removido.',
                { type: 'info' }
            );
            await loadHf();
            refresh();
        } catch (err) {
            toast(`Falha ao remover token: ${err.message}`, { type: 'error' });
        }
    }

    function scrollToHfCard() {
        const card = el('hf-card', root);
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // -------- Icons (SVG inline) --------

    function iconCheck() {
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
    }
    function iconAlert() {
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
    }
    function iconKey() {
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>`;
    }
    function iconLock() {
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`;
    }

    // ===========================================================================

    return () => {
        for (const { ctrl } of downloadCtrls.values()) {
            try { ctrl.close(); } catch {}
        }
        downloadCtrls.clear();
    };
}
