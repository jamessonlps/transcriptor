// View "Transcrever" — fluxo: upload -> SSE stream -> resultado.

import { $, el, els, show, hide, fmtBytes, fmtTime, cloneTemplate, toast } from '../ui.js';
import { startTranscribe, openStream, listModels } from '../api.js';

// Catálogo estático pro picker — vem alinhado com app/models.py
const MODEL_CARDS = [
    { key: 'tiny',           label: 'Tiny',           size: '75 MB',  speed: '~10x' },
    { key: 'base',           label: 'Base',           size: '140 MB', speed: '~7x' },
    { key: 'small',          label: 'Small',          size: '460 MB', speed: '~4x' },
    { key: 'medium',         label: 'Medium',         size: '1.5 GB', speed: '~2x' },
    { key: 'large-v3-turbo', label: 'Large v3 Turbo', size: '1.5 GB', speed: '~3x' },
    { key: 'large-v3',       label: 'Large v3',       size: '3 GB',   speed: '~0.7x', tag: 'máx. precisão' },
];

const SPEED_TOOLTIP =
    'Velocidade aproximada de processamento. ~2x = o modelo transcreve 2 segundos de áudio por segundo de CPU. Tempos reais variam com o hardware.';

const DEFAULT_MODEL = 'large-v3';

export async function mount(container, { config }) {
    const frag = cloneTemplate('tpl-transcribe');
    container.appendChild(frag);

    const root = container;
    const state = {
        file: null,
        model: DEFAULT_MODEL,
        diarize: false,
        numSpeakers: null,
        taskId: null,
        eventSource: null,
        streamFinished: false,
        segments: [],
        fullText: '',
        diarizationAvailable: config?.diarization_available ?? false,
        assignments: {},
        speakerLabels: {},
        speakers: [],
        downloadedModels: new Set(),
    };

    // ---------- Bootstrap: load modelos baixados pra mostrar status no picker ----------
    listModels().then(data => {
        state.downloadedModels = new Set(
            data.whisper.filter(m => m.downloaded).map(m => m.key)
        );
        renderModelPicker();
    }).catch(() => {
        // Não bloqueia se falhar — só não vai ter o badge de status
        renderModelPicker();
    });

    // ---------- Renderiza model picker dinâmico ----------
    const picker = el('model-picker', root);

    function renderModelPicker() {
        picker.innerHTML = '';
        MODEL_CARDS.forEach(m => {
            const card = document.createElement('button');
            card.className = 'model-card';
            card.dataset.model = m.key;
            if (m.key === state.model) card.classList.add('active');

            const isDownloaded = state.downloadedModels.has(m.key);
            const dot = isDownloaded
                ? '<span class="model-card-dot ok" title="Baixado"></span>'
                : '<span class="model-card-dot off" title="Será baixado no primeiro uso"></span>';

            const tagHtml = m.tag
                ? `<span class="model-card-tag tag-${m.tag === 'novo' ? 'new' : 'best'}">${m.tag.toUpperCase()}</span>`
                : '';

            card.innerHTML = `
                <span class="model-card-row">
                    <span class="model-card-head">
                        ${dot}
                        <span class="model-card-name">${m.label}</span>
                    </span>
                    ${tagHtml}
                </span>
                <span class="model-card-meta">
                    ${m.size} · <span class="tooltip-wrap tooltip-underline" data-tooltip="${SPEED_TOOLTIP}" aria-label="${SPEED_TOOLTIP}">${m.speed}</span>
                </span>
            `;
            card.addEventListener('click', (e) => {
                // Não troca de modelo se o clique foi no tooltip da velocidade
                if (e.target.closest('.tooltip-wrap')) return;
                picker.querySelectorAll('.model-card').forEach(b => b.classList.remove('active'));
                card.classList.add('active');
                state.model = m.key;
            });
            picker.appendChild(card);
        });
    }

    // ---------- Diarização toggle ----------
    const diarizeToggle = el('diarize-toggle', root);
    const diarizeCard = el('diarize-card', root);
    const numSpeakersBox = el('num-speakers-box', root);
    const numSpeakersInput = el('num-speakers', root);

    if (!state.diarizationAvailable) {
        diarizeToggle.disabled = true;
        diarizeCard.classList.add('disabled');
        el('diarize-help', root).innerHTML =
            'Token HuggingFace não configurado. ' +
            '<a href="#/settings" data-route="/settings" class="link">Configure em Configurações.</a>';
    }

    diarizeToggle.addEventListener('change', (e) => {
        state.diarize = e.target.checked;
        if (state.diarize) show(numSpeakersBox); else hide(numSpeakersBox);
    });

    numSpeakersInput.addEventListener('input', (e) => {
        const v = parseInt(e.target.value, 10);
        state.numSpeakers = (Number.isFinite(v) && v > 0) ? v : null;
    });

    // ---------- Drag & drop ----------
    const dropzone = el('dropzone', root);
    const fileInput = el('file-input', root);

    ['dragenter', 'dragover'].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault(); e.stopPropagation();
            dropzone.classList.add('dragover');
        });
    });
    ['dragleave', 'drop'].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault(); e.stopPropagation();
            dropzone.classList.remove('dragover');
        });
    });
    dropzone.addEventListener('drop', (e) => {
        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
    });
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) handleFile(file);
    });

    function handleFile(file) {
        state.file = file;
        el('selected-name', root).textContent = file.name;
        el('selected-size', root).textContent = fmtBytes(file.size);
        show(el('selected-file', root));
        el('start-btn', root).disabled = false;
    }

    el('clear-file', root).addEventListener('click', (e) => {
        e.preventDefault();
        state.file = null;
        fileInput.value = '';
        hide(el('selected-file', root));
        el('start-btn', root).disabled = true;
    });

    // ---------- Start ----------
    el('start-btn', root).addEventListener('click', startBtnHandler);

    async function startBtnHandler() {
        if (!state.file) return;

        // Se o modelo não está baixado, avisa antes de iniciar.
        if (!state.downloadedModels.has(state.model)) {
            toast(
                `O modelo "${state.model}" ainda não foi baixado. ` +
                `O servidor vai baixar agora — pode levar alguns minutos.`,
                { type: 'info', timeout: 5500 }
            );
        }

        const startBtn = el('start-btn', root);
        startBtn.disabled = true;
        startBtn.textContent = 'Enviando…';

        try {
            const data = await startTranscribe({
                file: state.file,
                model: state.model,
                diarize: state.diarize,
                numSpeakers: state.numSpeakers,
            });

            state.taskId = data.task_id;
            state.segments = [];
            state.fullText = '';
            state.assignments = {};
            state.speakerLabels = {};
            state.speakers = [];

            resetResultUI(data);
            connectStream(data.task_id);
        } catch (err) {
            showError(err.message);
            startBtn.disabled = false;
            startBtn.textContent = 'Iniciar transcrição';
        }
    }

    function resetResultUI(data) {
        hide(stepEl('upload'));
        hide(stepEl('error'));
        show(stepEl('result'));

        hide(el('actions', root));
        hide(el('download-txt', root));
        hide(el('download-srt', root));
        hide(el('speakers-bar', root));
        show(el('transcript-empty', root));
        hide(el('transcript-prose', root));
        hide(el('transcript-segments', root));
        el('transcript-prose', root).innerHTML = '';
        el('transcript-segments', root).innerHTML = '';

        // Reset toggle
        root.querySelectorAll('.view-toggle').forEach(b => {
            b.classList.toggle('active', b.dataset.view === 'segments');
        });

        el('progress-bar', root).style.width = '0%';
        el('progress-current', root).textContent = '0:00';
        el('progress-total', root).textContent = '--:--';
        el('status-text', root).textContent = 'Carregando modelo…';
        el('status-meta', root).textContent = `${data.filename} · modelo ${data.model}`;
        el('status-icon', root).innerHTML = '<div class="spinner"></div>';
    }

    function stepEl(name) {
        return root.querySelector(`[data-step="${name}"]`);
    }

    // ---------- SSE Stream ----------
    function connectStream(taskId) {
        if (state.eventSource) state.eventSource.close();
        state.streamFinished = false;

        const ctrl = openStream(`/api/stream/${taskId}`, handleEvent, onSSEError);
        state.eventSource = ctrl;
    }

    function onSSEError() {
        if (!state.streamFinished) {
            showError(
                'Conexão com o servidor perdida durante a transcrição.\n' +
                'Causa provável: falta de RAM (especialmente com modelo large-v3, que precisa de ~3GB livres).\n' +
                'Tente um modelo menor (medium funciona bem em pt-br) ou olhe os logs do servidor.'
            );
        }
    }

    function handleEvent(event) {
        switch (event.type) {
            case 'loading':
                el('status-text', root).textContent = `Carregando modelo ${event.model}…`;
                break;
            case 'info':
                el('status-text', root).textContent = 'Transcrevendo…';
                el('status-meta', root).textContent =
                    `Duração ${fmtTime(event.duration)} · idioma detectado: ${event.language} ` +
                    `(${(event.language_probability * 100).toFixed(0)}%)`;
                el('progress-total', root).textContent = fmtTime(event.duration);
                break;
            case 'segment':
                addSegment(event.segment);
                break;
            case 'progress': {
                const pct = Math.min(100, (event.current / event.total) * 100);
                el('progress-bar', root).style.width = `${pct}%`;
                el('progress-current', root).textContent = fmtTime(event.current);
                break;
            }
            case 'done':
                finishTranscription(event.result);
                break;
            case 'diarizing':
                el('status-text', root).textContent = 'Identificando falantes…';
                el('status-meta', root).textContent = 'Analisando timbres de voz com pyannote';
                el('status-icon', root).innerHTML = '<div class="spinner"></div>';
                break;
            case 'diarization_done':
                applyDiarization(event);
                break;
            case 'diarization_error':
                state.streamFinished = true;
                if (state.eventSource) state.eventSource.close();
                el('status-text', root).textContent = 'Transcrição concluída (sem identificação de falantes)';
                el('status-meta', root).textContent = `Falha na diarização: ${event.message}`;
                // Diarização falhou mid-stream — mostra um toast com CTA.
                if (/token|acesso|autoriza|aceit/i.test(event.message)) {
                    toast(
                        `${event.message} Configure o token em Configurações.`,
                        { type: 'error', timeout: 9000 }
                    );
                }
                break;
            case 'error':
                state.streamFinished = true;
                if (state.eventSource) state.eventSource.close();
                showError(event.message, { kind: event.error_kind });
                break;
        }
    }

    function addSegment(seg) {
        if (state.segments.length === 0) {
            hide(el('transcript-empty', root));
            showCurrentView();
            show(el('actions', root));
        }
        state.segments.push(seg);
        state.fullText += (state.fullText ? '\n' : '') + seg.text;

        el('transcript-prose', root).textContent = state.fullText;

        const segDiv = document.createElement('div');
        segDiv.className = 'segment-item';
        segDiv.innerHTML = `
            <span class="segment-ts">${seg.start_ts.slice(3, 8)}</span>
            <span class="segment-text"></span>
        `;
        segDiv.querySelector('.segment-text').textContent = seg.text;
        el('transcript-segments', root).appendChild(segDiv);

        const t = el('transcript', root);
        t.scrollTop = t.scrollHeight;
    }

    function showCurrentView() {
        const active = root.querySelector('.view-toggle.active');
        const view = active ? active.dataset.view : 'segments';
        if (view === 'prose') {
            show(el('transcript-prose', root));
            hide(el('transcript-segments', root));
        } else {
            hide(el('transcript-prose', root));
            show(el('transcript-segments', root));
        }
    }

    function finishTranscription(result) {
        if (!state.diarize) {
            state.streamFinished = true;
            if (state.eventSource) state.eventSource.close();
        }
        el('progress-bar', root).style.width = '100%';
        if (!state.diarize) {
            el('status-text', root).textContent = 'Transcrição concluída';
            el('status-icon', root).innerHTML = checkmarkSVG();
        }
        show(el('actions', root));
        show(el('download-txt', root));
        show(el('download-srt', root));
        el('download-txt', root).href = `/api/download/${state.taskId}/txt`;
        el('download-srt', root).href = `/api/download/${state.taskId}/srt`;
    }

    function applyDiarization(event) {
        state.streamFinished = true;
        if (state.eventSource) state.eventSource.close();

        state.assignments = {};
        Object.entries(event.assignments).forEach(([k, v]) => { state.assignments[Number(k)] = v; });

        const seen = new Set();
        state.speakers = [];
        state.segments.forEach(seg => {
            const sp = state.assignments[seg.index];
            if (sp && !seen.has(sp)) {
                seen.add(sp);
                state.speakers.push(sp);
            }
        });
        state.speakers.forEach((sp, i) => {
            if (!state.speakerLabels[sp]) state.speakerLabels[sp] = `Falante ${i + 1}`;
        });

        el('status-text', root).textContent = `Concluído · ${state.speakers.length} falantes detectados`;
        el('status-meta', root).textContent = '';
        el('status-icon', root).innerHTML = checkmarkSVG();

        renderSpeakerChips();
        rerenderTranscript();
        updateDownloadLinks();
    }

    function speakerColor(speaker) {
        const idx = state.speakers.indexOf(speaker);
        return idx >= 0 ? `speaker-c${idx % 8}` : 'speaker-c0';
    }

    function renderSpeakerChips() {
        const list = el('speakers-list', root);
        list.innerHTML = '';
        state.speakers.forEach(sp => {
            const chip = document.createElement('span');
            chip.className = `speaker-chip ${speakerColor(sp)}`;
            chip.innerHTML = `
                <span class="speaker-dot"></span>
                <span class="speaker-name" contenteditable="true" spellcheck="false"></span>
            `;
            const nameEl = chip.querySelector('.speaker-name');
            nameEl.textContent = state.speakerLabels[sp];
            nameEl.addEventListener('blur', () => {
                const newName = nameEl.textContent.trim() || state.speakerLabels[sp];
                state.speakerLabels[sp] = newName;
                nameEl.textContent = newName;
                rerenderTranscript();
                updateDownloadLinks();
            });
            nameEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); nameEl.blur(); }
            });
            list.appendChild(chip);
        });
        show(el('speakers-bar', root));
    }

    function rerenderTranscript() {
        const proseEl = el('transcript-prose', root);
        const segsEl = el('transcript-segments', root);
        proseEl.innerHTML = '';
        segsEl.innerHTML = '';

        let lastSpeaker = null;
        let proseText = '';

        state.segments.forEach(seg => {
            const speaker = state.assignments[seg.index];
            const label = speaker ? state.speakerLabels[speaker] : null;

            if (label && label !== lastSpeaker) {
                proseText += (proseText ? '\n\n' : '') + `[${label}]\n`;
                lastSpeaker = label;
            }
            proseText += seg.text + '\n';

            const segDiv = document.createElement('div');
            segDiv.className = 'segment-item';
            const speakerHTML = speaker
                ? `<span class="segment-speaker ${speakerColor(speaker)}"></span>`
                : '';
            segDiv.innerHTML = `
                <span class="segment-ts">${seg.start_ts.slice(3, 8)}</span>
                <span class="segment-text">${speakerHTML}<span class="segment-text-content"></span></span>
            `;
            if (speaker) segDiv.querySelector('.segment-speaker').textContent = state.speakerLabels[speaker];
            segDiv.querySelector('.segment-text-content').textContent = seg.text;
            segsEl.appendChild(segDiv);
        });

        proseEl.textContent = proseText.trim();
        state.fullText = proseText.trim();
    }

    function updateDownloadLinks() {
        const labels = encodeURIComponent(JSON.stringify(state.speakerLabels));
        el('download-txt', root).href = `/api/download/${state.taskId}/txt?labels=${labels}`;
        el('download-srt', root).href = `/api/download/${state.taskId}/srt?labels=${labels}`;
    }

    function checkmarkSVG() {
        return `
            <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12" />
            </svg>
        `;
    }

    // ---------- Action buttons ----------
    el('copy-btn', root).addEventListener('click', async () => {
        if (!state.fullText) return;
        try {
            await navigator.clipboard.writeText(state.fullText);
            const label = el('copy-label', root);
            const original = label.innerHTML;
            label.textContent = 'Copiado!';
            setTimeout(() => { label.innerHTML = original; }, 1500);
        } catch {
            toast('Falha ao copiar — selecione manualmente', { type: 'error' });
        }
    });

    root.querySelectorAll('.view-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            root.querySelectorAll('.view-toggle').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            showCurrentView();
        });
    });

    el('new-btn', root).addEventListener('click', resetToUpload);
    el('error-retry', root).addEventListener('click', resetToUpload);

    function resetToUpload() {
        if (state.eventSource) state.eventSource.close();
        state.taskId = null;
        state.segments = [];
        state.fullText = '';
        state.file = null;
        fileInput.value = '';
        hide(el('selected-file', root));
        hide(stepEl('result'));
        hide(stepEl('error'));
        show(stepEl('upload'));
        el('start-btn', root).disabled = true;
        el('start-btn', root).textContent = 'Iniciar transcrição';
        el('status-icon', root).innerHTML = '<div class="spinner"></div>';
        // Reset visuals do step de erro (sem isso o título/CTA do erro anterior
        // pode "vazar" pra próxima execução).
        const ctaBtn = el('error-settings', root);
        if (ctaBtn) hide(ctaBtn);
    }

    function showError(msg, { kind = null } = {}) {
        hide(stepEl('result'));
        hide(stepEl('upload'));
        show(stepEl('error'));
        el('error-text', root).textContent = msg;

        // Customiza o título + CTA conforme o tipo de erro. Os kinds vêm do
        // backend (models.py) quando falhas de auth são identificadas.
        const titleEl = el('error-title', root);
        const ctaBtn = el('error-settings', root);
        const ctaLabel = el('error-settings-label', root);

        if (kind === 'no_token') {
            titleEl.textContent = 'Token Hugging Face não configurado';
            ctaLabel.textContent = 'Configurar token';
            show(ctaBtn);
        } else if (kind === 'no_access') {
            titleEl.textContent = 'Você ainda não tem acesso a esse modelo';
            ctaLabel.textContent = 'Resolver em Configurações';
            show(ctaBtn);
        } else {
            // Heurística: se a mensagem fala de token/diarização, ainda dá um botão.
            if (/token|hugging\s*face|diariza/i.test(msg || '')) {
                titleEl.textContent = 'Algo deu errado';
                ctaLabel.textContent = 'Abrir Configurações';
                show(ctaBtn);
            } else {
                titleEl.textContent = 'Algo deu errado';
                hide(ctaBtn);
            }
        }
    }

    // Cleanup ao trocar de rota
    return () => {
        if (state.eventSource) state.eventSource.close();
    };
}
