// Thin wrapper sobre fetch() — concentra tratamento de erro e SSE.

async function _request(url, opts = {}) {
    let res;
    try {
        res = await fetch(url, opts);
    } catch (netErr) {
        throw new Error(
            `Não foi possível contatar o servidor (${netErr.message}).\n` +
            `Verifique se o Transcriptor está rodando.`
        );
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

export async function listModels() {
    const r = await _request('/api/models');
    return r.json();
}

export async function deleteModel(key) {
    const r = await _request(`/api/models/${encodeURIComponent(key)}`, { method: 'DELETE' });
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
