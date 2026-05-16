// View "Configurações" — Hugging Face token management + model catalog.
//
// The shape mirrors ``transcribe.js`` but the state isn't deep enough to
// warrant a full state machine. We keep two ad-hoc dicts (``hfState`` and
// the model rows) and let each handler render its own slice.

import { bind } from '../components/bind.js';
import { icon } from '../components/icons.js';
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
import { cloneTemplate, confirmModal, fmtBytes, toast } from '../ui.js';

export async function mount(container) {
    container.appendChild(cloneTemplate('tpl-settings'));
    const ui = bind(container);

    // active downloads, keyed by model spec key
    const downloadCtrls = new Map();

    // Local state for the HF card:
    //   - whether the wizard is open even though the token is valid
    //   - avoid double-fetch when "recheck" + "refresh" fire in sequence
    const hfState = {
        status: null,      // { configured, valid, source, username, masked, error, config_path }
        access: null,      // { models: [...], has_token }
        editing: false,    // true = show wizard even with a valid token
        saving: false,
    };

    ui('refresh-btn').on('click', () => refresh());

    wireHfCard();

    // Versão exibida no rodapé do card "About" — vem do backend pra evitar drift.
    getConfig()
        .then(cfg => { if (cfg?.version) ui('app-version').text(`v${cfg.version}`); })
        .catch(() => { /* nao bloqueia o resto da pagina por causa do rodape */ });

    await Promise.all([refresh(), loadHf()]);

    // =====================================================================
    // Model lists (Whisper + diarisation)
    // =====================================================================

    async function refresh() {
        const list = ui('whisper-list').node;
        const diList = ui('diarization-list').node;

        try {
            const data = await listModels();

            ui('storage-total').text(fmtBytes(data.total_bytes));
            ui('storage-path').text(data.cache_dir);
            ui('cache-dir').text(data.cache_dir);

            renderModelGroup(list, data.whisper);
            renderModelGroup(diList, data.diarization);

            // Diarisation needs a token — surface a warning when missing.
            const cfg = await getConfig().catch(() => null);
            const anyDiariNeedsToken = data.diarization.some(m => !m.downloaded);
            ui('diarization-token-warn').toggle(anyDiariNeedsToken && cfg && !cfg.diarization_available);
        } catch (err) {
            list.innerHTML = `<div class="alert-error">Falha ao carregar modelos: ${err.message}</div>`;
        }
    }

    function renderModelGroup(node, models) {
        node.innerHTML = '';
        models.forEach(m => node.appendChild(buildRow(m)));
    }

    function buildRow(model) {
        const frag = cloneTemplate('tpl-model-row');
        const row = frag.querySelector('.model-row');
        row.dataset.key = model.key;
        const rowUi = bind(row);

        rowUi('label').text(model.label);
        rowUi('description').text(model.description);
        rowUi('size').text(model.downloaded
            ? fmtBytes(model.size_bytes_actual)
            : `~${model.size_mb_estimate} MB`);
        rowUi('speed').text(model.speed_label);

        if (model.tag) {
            rowUi('tag')
                .text(model.tag.toUpperCase())
                .addClass(model.tag === 'novo' ? 'tag-new' : 'tag-best')
                .show();
        }

        if (model.downloaded) {
            rowUi('status').text('✓ Baixado').addClass('status-ok');
            rowUi('delete-btn').show();
        } else {
            rowUi('status').text('○ Disponível').addClass('status-off');
            rowUi('download-btn').show();
        }

        rowUi('download-btn').on('click', () => startDownload(model.key, row));
        rowUi('cancel-btn').on('click', () => cancelDownload(model.key));
        rowUi('delete-btn').on('click', () => askDelete(model));

        return row;
    }

    function startDownload(key, row) {
        if (downloadCtrls.has(key)) return;
        const rowUi = bind(row);

        rowUi('download-btn').hide();
        rowUi('cancel-btn').show();
        rowUi('progress').show();
        const progressUi = bind(rowUi('progress').node);
        progressUi('progress-bar').css('width', '0%');
        progressUi('progress-status').text('Conectando…');
        progressUi('progress-bytes').text('');

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
        const progressUi = bind(bind(row)('progress').node);

        switch (event.type) {
            case 'start':
                progressUi('progress-status').text('Baixando…');
                break;
            case 'progress': {
                const pct = event.total
                    ? Math.min(100, (event.downloaded / event.total) * 100)
                    : 0;
                progressUi('progress-bar').css('width', `${pct}%`);
                progressUi('progress-status').text(
                    event.stalled
                        ? 'Sem progresso (pode demorar para começar)…'
                        : `${pct.toFixed(0)}%`,
                );
                progressUi('progress-bytes').text(
                    `${fmtBytes(event.downloaded)} / ${fmtBytes(event.total)}`,
                );
                break;
            }
            case 'done': {
                progressUi('progress-bar').css('width', '100%');
                progressUi('progress-status').text('Concluído');
                progressUi('progress-bytes').text(fmtBytes(event.size_bytes));
                downloadCtrls.get(key)?.ctrl.close();
                downloadCtrls.delete(key);
                toast(`Modelo "${event.label}" baixado com sucesso.`, { type: 'success' });
                setTimeout(refresh, 600);
                break;
            }
            case 'error': {
                progressUi('progress-status').text('Erro');
                toast(event.message, { type: 'error', timeout: 9000 });
                downloadCtrls.get(key)?.ctrl.close();
                downloadCtrls.delete(key);
                // Auth-related: surface the HF card so the user can fix it.
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
            'Download cancelado no cliente. O servidor pode continuar até o ' +
            'próximo ponto de checagem — atualize a página em alguns segundos.',
            { type: 'info', timeout: 6000 },
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
                toast(
                    `"${model.label}" removido (${fmtBytes(model.size_bytes_actual)} liberados).`,
                    { type: 'success' },
                );
            } else {
                toast(`Nada a remover (${res.reason}).`, { type: 'info' });
            }
            refresh();
        } catch (err) {
            toast(`Falha ao remover: ${err.message}`, { type: 'error' });
        }
    }

    // =====================================================================
    // Hugging Face card (token + per-repo access)
    // =====================================================================

    function wireHfCard() {
        const input = ui('hf-input').node;
        const saveBtn = ui('hf-save').node;

        // Enable "Save" only when the input has content. Format validation
        // (must start with hf_) is done server-side so we get a clear error.
        ui('hf-input').on('input', () => {
            saveBtn.disabled = !input.value.trim() || hfState.saving;
            ui('hf-input-msg').hide();
        });
        ui('hf-input').on('keydown', (e) => {
            if (e.key === 'Enter' && !saveBtn.disabled) saveBtn.click();
        });

        // Show/hide token (password vs text input)
        ui('hf-toggle-vis').on('click', () => {
            input.type = input.type === 'password' ? 'text' : 'password';
        });

        ui('hf-toggle-edit').on('click', () => {
            hfState.editing = true;
            input.value = '';
            renderHfCard();
            setTimeout(() => input.focus(), 50);
        });
        ui('hf-cancel-edit').on('click', () => {
            hfState.editing = false;
            input.value = '';
            renderHfCard();
        });

        ui('hf-save').on('click', () => saveHfToken());
        ui('hf-recheck').on('click', () => loadHf({ keepEditing: false }));
        ui('hf-delete').on('click', () => askDeleteHfToken());
    }

    async function loadHf({ openWizard = false, keepEditing = false } = {}) {
        if (openWizard) hfState.editing = true;
        renderHfCard({ loading: true });
        try {
            const [status, access] = await Promise.all([
                getHfStatus(),
                getHfAccess().catch(() => ({ models: [], has_token: false })),
            ]);
            hfState.status = status;
            hfState.access = access;
            // No token -> wizard open. Valid token -> wizard closed unless
            // the user explicitly opened it. Invalid -> open for correction.
            if (!status.configured) hfState.editing = true;
            else if (status.valid === false) hfState.editing = true;
            else if (!openWizard && !keepEditing) hfState.editing = false;
            renderHfCard();
        } catch (err) {
            renderHfCard({ error: err.message });
        }
    }

    function renderHfCard({ loading = false, error = null } = {}) {
        const badge = ui('hf-status-badge');
        const title = ui('hf-status-title');
        const detail = ui('hf-status-detail');

        // Loading: keep the skeleton up while we wait for whoami.
        if (loading) {
            badge.removeClass('state-ok', 'state-warn', 'state-err').addClass('state-loading');
            badge.html('<div class="spinner"></div>');
            title.text('Verificando token…');
            detail.text('Consultando huggingface.co — geralmente leva 1 a 2 segundos.');
            ui('hf-toggle-edit').hide();
            ui('hf-wizard').hide();
            ui('hf-access-section').hide();
            ui('hf-footer').hide();
            ui('hf-loading-skeleton').show();
            return;
        }
        ui('hf-loading-skeleton').hide();

        if (error) {
            badge.removeClass('state-ok', 'state-warn', 'state-loading').addClass('state-err');
            badge.html(icon('alert'));
            title.text('Falha ao verificar');
            detail.text(error);
            ui('hf-toggle-edit').hide();
            ui('hf-wizard').hide();
            ui('hf-access-section').hide();
            ui('hf-footer').hide();
            return;
        }

        const status = hfState.status || {};
        const access = hfState.access || { models: [] };
        const configured = !!status.configured;
        const valid = status.valid === true;
        const invalid = status.configured && status.valid === false;

        // Status header
        if (!configured) {
            badge.removeClass('state-ok', 'state-err', 'state-loading').addClass('state-warn').html(icon('key'));
            title.text('Token não configurado');
            detail.text('Necessário para identificação de falantes (diarização). Siga o passo a passo abaixo.');
        } else if (invalid) {
            badge.removeClass('state-ok', 'state-warn', 'state-loading').addClass('state-err').html(icon('alert'));
            title.text('Token inválido');
            detail.text(status.error || 'O token não foi aceito pelo Hugging Face. Gere um novo e cole abaixo.');
        } else if (valid) {
            const allOk = access.models.length > 0 && access.models.every(m => m.accessible);
            if (allOk) {
                badge.removeClass('state-warn', 'state-err', 'state-loading').addClass('state-ok').html(icon('check'));
                title.text(`Pronto · ${status.username || 'conta autenticada'}`);
                detail.text('Token válido e termos aceitos para todos os modelos protegidos.');
            } else {
                badge.removeClass('state-ok', 'state-err', 'state-loading').addClass('state-warn').html(icon('alert'));
                const missing = access.models.filter(m => !m.accessible).length;
                title.text(`Conectado como ${status.username || 'conta'}, mas falta autorização`);
                detail.text(`Token válido. Falta aceitar os termos de ${missing} modelo${missing > 1 ? 's' : ''} (veja abaixo).`);
            }
        } else {
            badge.removeClass('state-ok', 'state-warn', 'state-err').addClass('state-loading').html('<div class="spinner"></div>');
            title.text('Verificando token…');
            detail.text('');
        }

        // "Trocar" button only when token is configured and we're not editing.
        ui('hf-toggle-edit').toggle(configured && !hfState.editing);

        const showWizard = hfState.editing || !configured;
        if (showWizard) {
            ui('hf-wizard').show();
            renderAccessList(ui('hf-access-list-wizard').node, access.models, status);
            if (status.config_path) ui('hf-config-path').text(status.config_path);
            ui('hf-cancel-edit').toggle(configured && !invalid);
        } else {
            ui('hf-wizard').hide();
        }

        if (configured && !showWizard) {
            ui('hf-access-section').show();
            renderAccessList(ui('hf-access-list').node, access.models, status);
            ui('hf-footer').show();
            const sourceUi = ui('hf-source-label');
            if (status.source === 'ui') {
                sourceUi.text('Salvo aqui na UI').removeClass('source-env').addClass('source-ui');
            } else if (status.source === 'env') {
                sourceUi.text('Carregado do .env').removeClass('source-ui').addClass('source-env');
            } else {
                sourceUi.text('').removeClass('source-ui', 'source-env');
            }
            ui('hf-masked').text(status.masked || '');
        } else {
            ui('hf-access-section').hide();
            ui('hf-footer').hide();
        }
    }

    function renderAccessList(node, models, status) {
        if (!node) return;
        node.innerHTML = '';
        if (!models || models.length === 0) {
            node.innerHTML = '<p class="text-xs text-slate-500">Nenhum modelo protegido a checar.</p>';
            return;
        }
        models.forEach(m => {
            const frag = cloneTemplate('tpl-hf-access-row');
            const row = frag.querySelector('.hf-access-row');
            row.dataset.repoId = m.repo_id;
            const rowUi = bind(row);

            rowUi('label').text(m.label);
            rowUi('repo-id').text(m.repo_id);
            rowUi('open-terms').attr('href', m.url);

            const iconEl = rowUi('state-icon');
            const statusEl = rowUi('status');
            const linkEl = rowUi('open-terms');

            if (m.accessible) {
                iconEl.removeClass('state-muted', 'state-warn').addClass('hf-access-icon', 'state-ok').html(icon('check'));
                statusEl.text('Acesso liberado').removeClass('state-muted', 'state-warn').addClass('state-ok');
                linkEl.hide();
            } else if (!status || !status.configured) {
                iconEl.removeClass('state-ok', 'state-warn').addClass('hf-access-icon', 'state-muted').html(icon('lock'));
                statusEl.text('Aguardando token').removeClass('state-ok', 'state-warn').addClass('state-muted');
                linkEl.show();
            } else if (status && status.valid === false) {
                iconEl.removeClass('state-ok', 'state-warn').addClass('hf-access-icon', 'state-muted').html(icon('lock'));
                statusEl.text('Token inválido').removeClass('state-ok', 'state-warn').addClass('state-muted');
                linkEl.show();
            } else {
                iconEl.removeClass('state-ok', 'state-muted').addClass('hf-access-icon', 'state-warn').html(icon('lock'));
                if (m.reason === 'gated') statusEl.text('Termos não aceitos');
                else if (m.reason === 'unauthorized') statusEl.text('Sem permissão');
                else statusEl.text('Sem acesso');
                statusEl.removeClass('state-ok', 'state-muted').addClass('state-warn');
                linkEl.show();
            }
            node.appendChild(row);
        });
    }

    async function saveHfToken() {
        const input = ui('hf-input').node;
        const saveBtn = ui('hf-save').node;
        const saveLabel = ui('hf-save-label').node;
        const msg = ui('hf-input-msg');

        const token = input.value.trim();
        if (!token) return;

        msg.hide();
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
            refresh();
        } catch (err) {
            msg.text(err.message).removeClass('hidden').addClass('state-err').show();
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
                { type: 'info' },
            );
            await loadHf();
            refresh();
        } catch (err) {
            toast(`Falha ao remover token: ${err.message}`, { type: 'error' });
        }
    }

    function scrollToHfCard() {
        const card = ui('hf-card').node;
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    return () => {
        for (const { ctrl } of downloadCtrls.values()) {
            try { ctrl.close(); } catch { /* ignore */ }
        }
        downloadCtrls.clear();
    };
}
