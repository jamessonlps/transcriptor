// Thin wrapper sobre fetch() — concentra tratamento de erro e SSE.

async function _request(url, { timeoutMs, ...opts } = {}) {
    let res;
    // AbortController como defesa: se o servidor estiver pendurado por algum
    // motivo (rede sumindo no meio, processo travado), evita loading eterno na UI.
    let ctrl = null;
    let timer = null;
    if (timeoutMs && typeof AbortController !== 'undefined') {
        ctrl = new AbortController();
        timer = setTimeout(() => ctrl.abort(), timeoutMs);
        opts.signal = ctrl.signal;
    }
    try {
        res = await fetch(url, opts);
    } catch (netErr) {
        if (ctrl && ctrl.signal.aborted) {
            throw new Error(
                `O servidor não respondeu em ${(timeoutMs / 1000).toFixed(0)}s. ` +
                `Tente novamente.`
            );
        }
        throw new Error(
            `Não foi possível contatar o servidor (${netErr.message}).\n` +
            `Verifique se o Transcriptor está rodando.`
        );
    } finally {
        if (timer) clearTimeout(timer);
    }
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const j = await res.json();
            detail = j.detail || detail;
        } catch { /* ignore */ }
        throw new Error(detail);
    }
    return res;
}

export async function getConfig() {
    const r = await _request('/api/config');
    return r.json();
}

export async function getRuntimeInfo() {
    const r = await _request('/api/runtime');
    return r.json();
}

export async function listModels() {
    const r = await _request('/api/models');
    return r.json();
}

export async function deleteModel(key) {
    const r = await _request(`/api/models/${encodeURIComponent(key)}`, { method: 'DELETE' });
    return r.json();
}

// ---------- Hugging Face token & access ----------

// Backend ja tem timeout de ~8s por chamada HF. 20s aqui cobre o pior caso
// (status + access do mesmo endpoint, com folga) sem deixar o usuario esperando.
const HF_TIMEOUT_MS = 20_000;

export async function getHfStatus() {
    const r = await _request('/api/hf/status', { timeoutMs: HF_TIMEOUT_MS });
    return r.json();
}

export async function setHfToken(token) {
    const r = await _request('/api/hf/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
    });
    return r.json();
}

export async function deleteHfToken() {
    const r = await _request('/api/hf/token', { method: 'DELETE' });
    return r.json();
}

export async function getHfAccess() {
    const r = await _request('/api/hf/access', { timeoutMs: HF_TIMEOUT_MS });
    return r.json();
}

export async function startTranscribe({ file, model, diarize, numSpeakers }) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('model', model);
    fd.append('diarize', diarize ? 'true' : 'false');
    if (numSpeakers) fd.append('num_speakers', String(numSpeakers));
    const r = await _request('/api/transcribe', { method: 'POST', body: fd });
    return r.json();
}

/**
 * Open an EventSource and call onEvent for each parsed JSON event.
 * Returns a controller with .close().
 */
export function openStream(url, onEvent, onError) {
    const es = new EventSource(url);
    es.onmessage = (msg) => {
        try {
            onEvent(JSON.parse(msg.data));
        } catch (err) {
            console.error('Failed to parse SSE event', msg.data, err);
        }
    };
    es.onerror = (e) => {
        es.close();
        if (onError) onError(e);
    };
    return {
        close: () => es.close(),
        eventSource: es,
    };
}
