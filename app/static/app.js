// Transcriptor — frontend logic (vanilla JS).
// Fluxo: pick file -> POST /api/transcribe -> open SSE em /api/stream/{id} -> render eventos.

const $ = (sel) => document.querySelector(sel);

const state = {
    file: null,
    model: 'large-v3',
    diarize: false,
    numSpeakers: null,
    taskId: null,
    eventSource: null,
    streamFinished: false,
    segments: [],
    fullText: '',
    // diarização
    diarizationAvailable: false,
    assignments: {},      // {segIndex: "SPEAKER_00"}
    speakerLabels: {},    // {"SPEAKER_00": "Falante 1" | nome custom}
    speakers: [],         // ordem de aparição
};

// Carrega config do servidor (token disponível? quais modelos?)
fetch('/api/config').then(r => r.json()).then(cfg => {
    state.diarizationAvailable = cfg.diarization_available;
    if (!cfg.diarization_available) {
        const help = $('#diarize-help');
        const toggle = $('#diarize-toggle');
        toggle.disabled = true;
        toggle.parentElement.style.opacity = 0.5;
        help.innerHTML = '<span class="text-amber-400">Token HuggingFace não configurado.</span> Crie um arquivo <code class="font-mono">.env</code> em <code class="font-mono">apps/transcriptor/</code> com <code class="font-mono">HF_TOKEN=hf_...</code> e reinicie o servidor.';
    }
});

// ---------- Helpers ----------

function fmtBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function fmtTime(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function show(id) { $(id).classList.remove('hidden'); }
function hide(id) { $(id).classList.add('hidden'); }

// ---------- Model picker ----------

document.querySelectorAll('.model-card').forEach((btn) => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.model-card').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.model = btn.dataset.model;
    });
});

// Toggle diarização
$('#diarize-toggle').addEventListener('change', (e) => {
    state.diarize = e.target.checked;
    const box = $('#num-speakers-box');
    if (state.diarize) box.classList.remove('hidden');
    else box.classList.add('hidden');
});
$('#num-speakers').addEventListener('input', (e) => {
    const v = parseInt(e.target.value, 10);
    state.numSpeakers = (Number.isFinite(v) && v > 0) ? v : null;
});

// ---------- Drag & drop / file picker ----------

const dropzone = $('#dropzone');
const fileInput = $('#file-input');

['dragenter', 'dragover'].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add('dragover');
    });
});
['dragleave', 'drop'].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
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
    $('#selected-name').textContent = file.name;
    $('#selected-size').textContent = fmtBytes(file.size);
    show('#selected-file');
    $('#start-btn').disabled = false;
}

$('#clear-file').addEventListener('click', (e) => {
    e.preventDefault();
    state.file = null;
    fileInput.value = '';
    hide('#selected-file');
    $('#start-btn').disabled = true;
});

// ---------- Start transcription ----------

$('#start-btn').addEventListener('click', async () => {
    if (!state.file) return;

    $('#start-btn').disabled = true;
    $('#start-btn').textContent = 'Enviando…';

    const formData = new FormData();
    formData.append('file', state.file);
    formData.append('model', state.model);
    formData.append('diarize', state.diarize ? 'true' : 'false');
    if (state.numSpeakers) formData.append('num_speakers', String(state.numSpeakers));

    try {
        let res;
        try {
            res = await fetch('/api/transcribe', { method: 'POST', body: formData });
        } catch (netErr) {
            // "Failed to fetch" cai aqui — server caiu, sem rede, ou CORS.
            throw new Error(
                `Não foi possível contatar o servidor (${netErr.message}).\n` +
                `Verifique se ./run.sh está rodando e olhe os logs do terminal.`
            );
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        state.taskId = data.task_id;
        state.segments = [];
        state.fullText = '';
        state.assignments = {};
        state.speakerLabels = {};
        state.speakers = [];

        // Reset UI
        hide('#step-upload');
        hide('#step-error');
        hide('#actions');
        hide('#download-txt');
        hide('#download-srt');
        hide('#speakers-bar');
        show('#step-result');
        show('#transcript-empty');
        hide('#transcript-prose');
        hide('#transcript-segments');
        $('#transcript-prose').innerHTML = '';
        $('#transcript-segments').innerHTML = '';
        // Reset toggle: timestamps view e o default.
        document.querySelectorAll('.view-toggle').forEach((b) => {
            b.classList.toggle('active', b.dataset.view === 'segments');
        });
        $('#progress-bar').style.width = '0%';
        $('#progress-current').textContent = '0:00';
        $('#progress-total').textContent = '--:--';
        $('#status-text').textContent = 'Carregando modelo…';
        $('#status-meta').textContent = `${data.filename} · modelo ${data.model}`;

        connectStream(data.task_id);
    } catch (err) {
        showError(err.message);
        $('#start-btn').disabled = false;
        $('#start-btn').textContent = 'Iniciar transcrição';
    }
});

// ---------- SSE stream ----------

function connectStream(taskId) {
    if (state.eventSource) state.eventSource.close();
    const es = new EventSource(`/api/stream/${taskId}`);
    state.eventSource = es;
    state.streamFinished = false;

    es.onmessage = (msg) => {
        const event = JSON.parse(msg.data);
        handleEvent(event);
    };
    es.onerror = () => {
        es.close();
        // Se nao recebemos 'done', a conexao caiu no meio.
        // Causa mais comum: server crashou (OOM com large-v3, p.ex).
        if (!state.streamFinished) {
            showError(
                'Conexão com o servidor perdida durante a transcrição.\n' +
                'Causa provável: falta de RAM (especialmente com modelo large-v3, que precisa de ~3GB livres).\n' +
                'Tente um modelo menor (medium funciona bem em pt-br) ou olhe os logs no terminal onde o ./run.sh está rodando.'
            );
        }
    };
}

function handleEvent(event) {
    switch (event.type) {
        case 'loading':
            $('#status-text').textContent = `Carregando modelo ${event.model}…`;
            break;

        case 'info':
            $('#status-text').textContent = 'Transcrevendo…';
            $('#status-meta').textContent = `Duração ${fmtTime(event.duration)} · idioma detectado: ${event.language} (${(event.language_probability * 100).toFixed(0)}%)`;
            $('#progress-total').textContent = fmtTime(event.duration);
            break;

        case 'segment':
            addSegment(event.segment);
            break;

        case 'progress':
            const pct = Math.min(100, (event.current / event.total) * 100);
            $('#progress-bar').style.width = `${pct}%`;
            $('#progress-current').textContent = fmtTime(event.current);
            break;

        case 'done':
            finishTranscription(event.result);
            break;

        case 'diarizing':
            $('#status-text').textContent = 'Identificando falantes…';
            $('#status-meta').textContent = 'Analisando timbres de voz com pyannote';
            // volta o spinner enquanto diariza
            $('#status-icon').innerHTML = '<div class="spinner"></div>';
            break;

        case 'diarization_done':
            applyDiarization(event);
            break;

        case 'diarization_error':
            // diarização falha não invalida a transcrição — só avisa
            state.streamFinished = true;
            if (state.eventSource) state.eventSource.close();
            $('#status-text').textContent = 'Transcrição concluída (sem identificação de falantes)';
            $('#status-meta').textContent = `Falha na diarização: ${event.message}`;
            break;

        case 'error':
            state.streamFinished = true;
            if (state.eventSource) state.eventSource.close();
            showError(event.message);
            break;
    }
}

function addSegment(seg) {
    if (state.segments.length === 0) {
        hide('#transcript-empty');
        // Default: mostra a view com timestamps assim que o texto comeca a sair.
        // O usuario pode trocar pra "Texto" via toggle (que ja aparece com copy/toggle).
        showCurrentView();
        // Toggle + Copy ficam disponiveis durante o streaming.
        // Os downloads sao revelados em finishTranscription (quando o arquivo esta pronto).
        show('#actions');
    }
    state.segments.push(seg);
    state.fullText += (state.fullText ? '\n' : '') + seg.text;

    // Atualiza view "prose" (texto corrido)
    $('#transcript-prose').textContent = state.fullText;

    // Atualiza view "segments"
    const segDiv = document.createElement('div');
    segDiv.className = 'segment-item';
    segDiv.innerHTML = `
        <span class="segment-ts">${seg.start_ts.slice(3, 8)}</span>
        <span class="segment-text"></span>
    `;
    segDiv.querySelector('.segment-text').textContent = seg.text;
    $('#transcript-segments').appendChild(segDiv);

    // Auto-scroll suave
    $('#transcript').scrollTop = $('#transcript').scrollHeight;
}

// Mostra a view marcada como ativa no toggle (default = segments).
function showCurrentView() {
    const active = document.querySelector('.view-toggle.active');
    const view = active ? active.dataset.view : 'segments';
    if (view === 'prose') {
        show('#transcript-prose');
        hide('#transcript-segments');
    } else {
        hide('#transcript-prose');
        show('#transcript-segments');
    }
}

function finishTranscription(result) {
    // Se diarização vai rodar depois, NÃO marcamos streamFinished ainda
    // (caso contrário a queda da conexão SSE no fim seria interpretada como erro).
    if (!state.diarize) {
        state.streamFinished = true;
        if (state.eventSource) state.eventSource.close();
    }
    $('#progress-bar').style.width = '100%';
    if (!state.diarize) {
        $('#status-text').textContent = 'Transcrição concluída';
        $('#status-icon').innerHTML = checkmarkSVG();
    }
    show('#actions');
    show('#download-txt');
    show('#download-srt');
    $('#download-txt').href = `/api/download/${state.taskId}/txt`;
    $('#download-srt').href = `/api/download/${state.taskId}/srt`;
}

function checkmarkSVG() {
    return `
        <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12" />
        </svg>
    `;
}

// ---------- Diarização ----------

function applyDiarization(event) {
    state.streamFinished = true;
    if (state.eventSource) state.eventSource.close();

    state.assignments = {};
    Object.entries(event.assignments).forEach(([k, v]) => { state.assignments[Number(k)] = v; });

    // Determina ordem de aparição dos falantes (mais natural que ordem alfabética).
    const seen = new Set();
    state.speakers = [];
    state.segments.forEach(seg => {
        const sp = state.assignments[seg.index];
        if (sp && !seen.has(sp)) {
            seen.add(sp);
            state.speakers.push(sp);
        }
    });
    // Labels iniciais: "Falante 1", "Falante 2"...
    state.speakers.forEach((sp, i) => {
        if (!state.speakerLabels[sp]) state.speakerLabels[sp] = `Falante ${i + 1}`;
    });

    $('#status-text').textContent = `Concluído · ${state.speakers.length} falantes detectados`;
    $('#status-meta').textContent = '';
    $('#status-icon').innerHTML = checkmarkSVG();

    renderSpeakerChips();
    rerenderTranscript();
    updateDownloadLinks();
}

function speakerColor(speaker) {
    const idx = state.speakers.indexOf(speaker);
    return idx >= 0 ? `speaker-c${idx % 8}` : 'speaker-c0';
}

function renderSpeakerChips() {
    const list = $('#speakers-list');
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
    show('#speakers-bar');
}

function rerenderTranscript() {
    const proseEl = $('#transcript-prose');
    const segsEl = $('#transcript-segments');
    proseEl.innerHTML = '';
    segsEl.innerHTML = '';

    let lastSpeaker = null;
    let proseText = '';

    state.segments.forEach(seg => {
        const speaker = state.assignments[seg.index];
        const label = speaker ? state.speakerLabels[speaker] : null;

        // Prose view: prefixo de falante quando muda
        if (label && label !== lastSpeaker) {
            proseText += (proseText ? '\n\n' : '') + `[${label}]\n`;
            lastSpeaker = label;
        }
        proseText += seg.text + '\n';

        // Segments view
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
    $('#download-txt').href = `/api/download/${state.taskId}/txt?labels=${labels}`;
    $('#download-srt').href = `/api/download/${state.taskId}/srt?labels=${labels}`;
}

// ---------- Action buttons ----------

$('#copy-btn').addEventListener('click', async () => {
    if (!state.fullText) return;
    try {
        await navigator.clipboard.writeText(state.fullText);
        const label = $('#copy-label');
        // Preserva o markup interno (spans responsivos) restaurando innerHTML.
        const original = label.innerHTML;
        label.textContent = 'Copiado!';
        setTimeout(() => { label.innerHTML = original; }, 1500);
    } catch {
        alert('Falha ao copiar — copie manualmente.');
    }
});

document.querySelectorAll('.view-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.view-toggle').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        showCurrentView();
    });
});

$('#new-btn').addEventListener('click', resetToUpload);
$('#error-retry').addEventListener('click', resetToUpload);

function resetToUpload() {
    if (state.eventSource) state.eventSource.close();
    state.taskId = null;
    state.segments = [];
    state.fullText = '';
    state.file = null;
    fileInput.value = '';
    hide('#selected-file');
    hide('#step-result');
    hide('#step-error');
    show('#step-upload');
    $('#start-btn').disabled = true;
    $('#start-btn').textContent = 'Iniciar transcrição';
    $('#status-icon').innerHTML = '<div class="spinner"></div>';
}

function showError(msg) {
    hide('#step-result');
    hide('#step-upload');
    show('#step-error');
    $('#error-text').textContent = msg;
}
