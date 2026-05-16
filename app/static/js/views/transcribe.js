// View "Transcrever" — upload → SSE stream → result.
//
// Architecture notes
// ------------------
// We split this view into three concerns:
//
//   1. ``State`` (class) — single source of truth for everything the user
//      can change. State mutations dispatch a ``change`` event so the
//      render layer reacts without anyone calling it explicitly.
//   2. ``mount`` — wiring: clone template, bind DOM elements, attach
//      event handlers that call State mutators.
//   3. ``render*`` (functions) — pure read-from-state, write-to-DOM. They
//      never mutate state.
//
// Why this matters: in the old view, state, render and effects were
// interleaved in a single 550-line closure. Untangling them makes the
// transitions (upload → streaming → done | error) explicit, and lets the
// SSE handler trigger UI updates by *changing state* rather than reaching
// into the DOM directly.

import { bind } from '../components/bind.js';
import { icon } from '../components/icons.js';
import { startTranscribe, openStream, listModels } from '../api.js';
import { fmtBytes, fmtTime, cloneTemplate, toast } from '../ui.js';

// Catálogo estático pro picker — vem alinhado com ``app/models.py``.
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

// Discrete UI phases. Render switches sections based on this — easier to
// reason about than the previous "set five booleans in the right order".
const PHASE = {
    UPLOAD: 'upload',
    STREAMING: 'streaming',
    DONE: 'done',
    ERROR: 'error',
};

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

class State extends EventTarget {
    constructor(initialConfig) {
        super();
        this.file = null;
        this.model = DEFAULT_MODEL;
        this.diarize = false;
        this.numSpeakers = null;
        this.taskId = null;
        this.eventSource = null;
        this.streamFinished = false;
        this.segments = [];
        this.fullText = '';
        this.diarizationAvailable = initialConfig?.diarization_available ?? false;
        this.assignments = {};
        this.speakerLabels = {};
        this.speakers = [];
        this.downloadedModels = new Set();
        this.phase = PHASE.UPLOAD;
        this.progress = { current: 0, total: 0 };
        this.status = { text: '', meta: '' };
        this.errorMessage = null;
        this.errorKind = null;
    }

    _emit() { this.dispatchEvent(new Event('change')); }

    setFile(file)              { this.file = file;            this._emit(); }
    clearFile()                { this.file = null;            this._emit(); }
    setModel(key)              { this.model = key;            this._emit(); }
    setDiarize(flag)           { this.diarize = flag;         this._emit(); }
    setNumSpeakers(n)          { this.numSpeakers = n;        this._emit(); }
    setDownloadedModels(set)   { this.downloadedModels = set; this._emit(); }

    startStreaming(taskId, filename, model) {
        this.taskId = taskId;
        this.segments = [];
        this.fullText = '';
        this.assignments = {};
        this.speakerLabels = {};
        this.speakers = [];
        this.streamFinished = false;
        this.phase = PHASE.STREAMING;
        this.progress = { current: 0, total: 0 };
        this.status = { text: 'Carregando modelo…', meta: `${filename} · modelo ${model}` };
        this.errorMessage = null;
        this.errorKind = null;
        this._emit();
    }

    addSegment(seg) {
        this.segments.push(seg);
        this.fullText += (this.fullText ? '\n' : '') + seg.text;
        this._emit();
    }

    setProgress(current, total) { this.progress = { current, total }; this._emit(); }
    setStatus(text, meta = '')  { this.status = { text, meta };       this._emit(); }
    setInfo(language, prob, duration) {
        this.status = {
            text: 'Transcrevendo…',
            meta: `Duração ${fmtTime(duration)} · idioma detectado: ${language} (${(prob * 100).toFixed(0)}%)`,
        };
        this.progress.total = duration;
        this._emit();
    }
    markDone() {
        this.phase = PHASE.DONE;
        this.streamFinished = true;
        if (!this.diarize) {
            this.status = { text: 'Transcrição concluída', meta: this.status.meta };
        }
        this._emit();
    }
    applyDiarization(event) {
        this.streamFinished = true;
        this.assignments = {};
        Object.entries(event.assignments).forEach(([k, v]) => {
            this.assignments[Number(k)] = v;
        });
        const seen = new Set();
        this.speakers = [];
        this.segments.forEach(seg => {
            const sp = this.assignments[seg.index];
            if (sp && !seen.has(sp)) {
                seen.add(sp);
                this.speakers.push(sp);
            }
        });
        this.speakers.forEach((sp, i) => {
            if (!this.speakerLabels[sp]) this.speakerLabels[sp] = `Falante ${i + 1}`;
        });
        this.status = { text: `Concluído · ${this.speakers.length} falantes detectados`, meta: '' };
        this.phase = PHASE.DONE;
        this._emit();
    }
    renameSpeaker(raw, newName) {
        this.speakerLabels[raw] = newName;
        this._emit();
    }
    fail(message, kind = null) {
        this.phase = PHASE.ERROR;
        this.errorMessage = message;
        this.errorKind = kind;
        this.streamFinished = true;
        this._emit();
    }
    reset() {
        this.taskId = null;
        this.file = null;
        this.segments = [];
        this.fullText = '';
        this.phase = PHASE.UPLOAD;
        this.errorMessage = null;
        this.errorKind = null;
        this._emit();
    }
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

export async function mount(container, { config }) {
    container.appendChild(cloneTemplate('tpl-transcribe'));
    const ui = bind(container);
    const state = new State(config);

    // ---------- Model picker (rendered once at boot, re-rendered when
    //            the downloaded set changes) ----------
    listModels()
        .then(data => {
            state.setDownloadedModels(new Set(
                data.whisper.filter(m => m.downloaded).map(m => m.key),
            ));
        })
        .catch(() => state.setDownloadedModels(new Set()));

    function renderModelPicker() {
        const picker = ui('model-picker').node;
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
                // Don't switch model when the click landed inside the speed
                // tooltip — the tooltip is a sibling concern of the card.
                if (e.target.closest('.tooltip-wrap')) return;
                state.setModel(m.key);
            });
            picker.appendChild(card);
        });
    }

    // ---------- Wiring: DOM events → state mutations ----------

    // Diarisation toggle
    if (!state.diarizationAvailable) {
        ui('diarize-toggle').disabled(true);
        ui('diarize-card').addClass('disabled');
        ui('diarize-help').html(
            'Token HuggingFace não configurado. ' +
            '<a href="#/settings" data-route="/settings" class="link">Configure em Configurações.</a>',
        );
    }
    ui('diarize-toggle').on('change', (e) => state.setDiarize(e.target.checked));
    ui('num-speakers').on('input', (e) => {
        const v = parseInt(e.target.value, 10);
        state.setNumSpeakers(Number.isFinite(v) && v > 0 ? v : null);
    });

    // Drag & drop
    const dropzone = ui('dropzone').node;
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
        if (file) state.setFile(file);
    });
    ui('file-input').on('change', (e) => {
        const file = e.target.files[0];
        if (file) state.setFile(file);
    });
    ui('clear-file').on('click', (e) => {
        e.preventDefault();
        state.clearFile();
        ui('file-input').node.value = '';
    });

    // Start / new / retry buttons
    ui('start-btn').on('click', startTranscribeFlow);
    ui('new-btn').on('click', resetToUpload);
    ui('error-retry').on('click', resetToUpload);

    // Copy + view toggle (segments vs prose)
    ui('copy-btn').on('click', async () => {
        if (!state.fullText) return;
        try {
            await navigator.clipboard.writeText(state.fullText);
            ui('copy-label').text('Copiado!');
            setTimeout(() => ui('copy-label').html('Copiar<span class="hidden sm:inline"> texto</span>'), 1500);
        } catch {
            toast('Falha ao copiar — selecione manualmente', { type: 'error' });
        }
    });
    container.querySelectorAll('.view-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.view-toggle').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderTranscriptVisibility();
        });
    });

    // ---------- The render layer reacts to state changes ----------
    state.addEventListener('change', render);
    render();

    // ---------- Flow handlers ----------

    async function startTranscribeFlow() {
        if (!state.file) return;
        if (!state.downloadedModels.has(state.model)) {
            toast(
                `O modelo "${state.model}" ainda não foi baixado. ` +
                `O servidor vai baixar agora — pode levar alguns minutos.`,
                { type: 'info', timeout: 5500 },
            );
        }
        ui('start-btn').disabled(true).text('Enviando…');

        try {
            const data = await startTranscribe({
                file: state.file,
                model: state.model,
                diarize: state.diarize,
                numSpeakers: state.numSpeakers,
            });
            state.startStreaming(data.task_id, data.filename, data.model);
            connectStream(data.task_id);
        } catch (err) {
            state.fail(err.message);
            ui('start-btn').disabled(false).text('Iniciar transcrição');
        }
    }

    function connectStream(taskId) {
        if (state.eventSource) state.eventSource.close();
        state.eventSource = openStream(`/api/stream/${taskId}`, handleEvent, onSSEError);
    }

    function onSSEError() {
        if (state.streamFinished) return;
        state.fail(
            'Conexão com o servidor perdida durante a transcrição.\n' +
            'Causa provável: falta de RAM (especialmente com modelo large-v3, ' +
            'que precisa de ~3GB livres).\n' +
            'Tente um modelo menor (medium funciona bem em pt-br) ou olhe os ' +
            'logs do servidor.',
        );
    }

    function handleEvent(event) {
        switch (event.type) {
            case 'loading':
                state.setStatus(`Carregando modelo ${event.model}…`);
                break;
            case 'info':
                state.setInfo(event.language, event.language_probability, event.duration);
                break;
            case 'segment':
                state.addSegment(event.segment);
                break;
            case 'progress':
                state.setProgress(event.current, event.total);
                break;
            case 'done':
                state.markDone();
                break;
            case 'diarizing':
                state.setStatus('Identificando falantes…', 'Analisando timbres de voz com pyannote');
                break;
            case 'diarization_done':
                state.applyDiarization(event);
                if (state.eventSource) state.eventSource.close();
                break;
            case 'diarization_error':
                if (state.eventSource) state.eventSource.close();
                state.setStatus('Transcrição concluída (sem identificação de falantes)',
                                `Falha na diarização: ${event.message}`);
                state.streamFinished = true;
                state.phase = PHASE.DONE;
                state._emit();
                if (/token|acesso|autoriza|aceit/i.test(event.message)) {
                    toast(`${event.message} Configure o token em Configurações.`,
                        { type: 'error', timeout: 9000 });
                }
                break;
            case 'error':
                if (state.eventSource) state.eventSource.close();
                state.fail(event.message, event.error_kind);
                break;
        }
    }

    function resetToUpload() {
        if (state.eventSource) state.eventSource.close();
        state.reset();
        ui('file-input').node.value = '';
        ui('start-btn').disabled(true).text('Iniciar transcrição');
    }

    // ---------- Render ----------
    //
    // Each render call is idempotent — it reflects the *current* state into
    // the DOM. Renaming a speaker re-renders the whole transcript, which
    // sounds wasteful but is fine: segments are at most a few hundred lines
    // and innerHTML wiping is fast.

    function render() {
        // The picker shows downloaded-status dots that depend on state, so
        // re-render it each tick. 6 cards is cheap; keeps the code simple.
        renderModelPicker();

        // Selected-file card
        if (state.file) {
            ui('selected-name').text(state.file.name);
            ui('selected-size').text(fmtBytes(state.file.size));
            ui('selected-file').show();
            ui('start-btn').disabled(false);
        } else {
            ui('selected-file').hide();
            ui('start-btn').disabled(true);
        }

        // Num-speakers helper
        ui('num-speakers-box').toggle(state.diarize);

        // Steps
        ui('upload-step-marker'); // no-op; phases controlled below
        const steps = container.querySelectorAll('[data-step]');
        steps.forEach(s => s.classList.toggle('hidden', s.dataset.step !== state.phase
            && !(state.phase === PHASE.STREAMING && s.dataset.step === 'result')
            && !(state.phase === PHASE.DONE && s.dataset.step === 'result')));

        if (state.phase === PHASE.STREAMING || state.phase === PHASE.DONE) {
            renderResult();
        }
        if (state.phase === PHASE.ERROR) {
            renderError();
        }
    }

    function renderResult() {
        ui('status-text').text(state.status.text || '');
        ui('status-meta').text(state.status.meta || '');

        // Progress
        const pct = state.progress.total
            ? Math.min(100, (state.progress.current / state.progress.total) * 100)
            : 0;
        ui('progress-bar').css('width', `${pct}%`);
        ui('progress-current').text(fmtTime(state.progress.current));
        ui('progress-total').text(state.progress.total ? fmtTime(state.progress.total) : '--:--');

        // Status icon
        if (state.phase === PHASE.DONE) {
            ui('status-icon').html(`<span class="text-emerald-400">${icon('check')}</span>`);
        } else {
            ui('status-icon').html('<div class="spinner"></div>');
        }

        // Transcript content
        if (state.segments.length === 0) {
            ui('transcript-empty').show();
            ui('transcript-prose').hide();
            ui('transcript-segments').hide();
        } else {
            ui('transcript-empty').hide();
            rerenderTranscript();
            renderTranscriptVisibility();
        }

        // Actions bar + download links
        if (state.phase === PHASE.DONE || state.segments.length > 0) {
            ui('actions').show();
            ui('download-txt').show();
            ui('download-srt').show();
            updateDownloadLinks();
        }

        // Speakers (only when diarisation completed)
        if (state.speakers.length > 0) {
            renderSpeakerChips();
            ui('speakers-bar').show();
        } else {
            ui('speakers-bar').hide();
        }
    }

    function renderError() {
        const msg = state.errorMessage || '';
        const kind = state.errorKind;
        ui('error-text').text(msg);

        const titleEl = ui('error-title');
        const ctaBtn = ui('error-settings');
        const ctaLabel = ui('error-settings-label');

        if (kind === 'no_token') {
            titleEl.text('Token Hugging Face não configurado');
            ctaLabel.text('Configurar token');
            ctaBtn.show();
        } else if (kind === 'no_access') {
            titleEl.text('Você ainda não tem acesso a esse modelo');
            ctaLabel.text('Resolver em Configurações');
            ctaBtn.show();
        } else if (/token|hugging\s*face|diariza/i.test(msg)) {
            titleEl.text('Algo deu errado');
            ctaLabel.text('Abrir Configurações');
            ctaBtn.show();
        } else {
            titleEl.text('Algo deu errado');
            ctaBtn.hide();
        }
    }

    function speakerColor(speaker) {
        const idx = state.speakers.indexOf(speaker);
        return idx >= 0 ? `speaker-c${idx % 8}` : 'speaker-c0';
    }

    function renderSpeakerChips() {
        const list = ui('speakers-list').node;
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
                state.renameSpeaker(sp, newName);
            });
            nameEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); nameEl.blur(); }
            });
            list.appendChild(chip);
        });
    }

    function rerenderTranscript() {
        const proseEl = ui('transcript-prose').node;
        const segsEl = ui('transcript-segments').node;
        proseEl.innerHTML = '';
        segsEl.innerHTML = '';

        let lastSpeaker = null;
        let proseText = '';
        const hasDiar = state.speakers.length > 0;

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
            const speakerHTML = (hasDiar && speaker)
                ? `<span class="segment-speaker ${speakerColor(speaker)}"></span>`
                : '';
            segDiv.innerHTML = `
                <span class="segment-ts">${seg.start_ts.slice(3, 8)}</span>
                <span class="segment-text">${speakerHTML}<span class="segment-text-content"></span></span>
            `;
            if (hasDiar && speaker) {
                segDiv.querySelector('.segment-speaker').textContent = state.speakerLabels[speaker];
            }
            segDiv.querySelector('.segment-text-content').textContent = seg.text;
            segsEl.appendChild(segDiv);
        });

        proseEl.textContent = proseText.trim();
        // Keep the public fullText in sync with what's rendered so copy/download
        // include speaker headers when applicable.
        state.fullText = hasDiar ? proseText.trim() : state.segments.map(s => s.text).join('\n');

        // Auto-scroll to the latest segment while streaming.
        if (state.phase === PHASE.STREAMING) {
            const t = ui('transcript').node;
            t.scrollTop = t.scrollHeight;
        }
    }

    function renderTranscriptVisibility() {
        const active = container.querySelector('.view-toggle.active');
        const view = active ? active.dataset.view : 'segments';
        ui('transcript-prose').toggle(view === 'prose');
        ui('transcript-segments').toggle(view === 'segments');
    }

    function updateDownloadLinks() {
        if (!state.taskId) return;
        const labels = encodeURIComponent(JSON.stringify(state.speakerLabels));
        ui('download-txt').attr('href', `/api/download/${state.taskId}/txt?labels=${labels}`);
        ui('download-srt').attr('href', `/api/download/${state.taskId}/srt?labels=${labels}`);
    }

    // Cleanup ao trocar de rota
    return () => {
        if (state.eventSource) state.eventSource.close();
    };
}
