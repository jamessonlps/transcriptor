// Runtime badge — informa CPU/GPU no topnav e abre popover com detalhes.
//
// Estado é puxado uma única vez no boot (via /api/runtime) e não muda no
// runtime: a decisão é tomada quando o servidor sobe. Refresh = reload da página.

import { getRuntimeInfo } from '../api.js';
import { el } from '../ui.js';
import { hydrateIcons } from './icons.js';

/**
 * @typedef {{
 *   device: 'cpu' | 'cuda',
 *   compute_type: string,
 *   gpu_name: string | null,
 *   reason: string,
 *   cpu_threads: number | null
 * }} TranscriptionInfo
 *
 * @typedef {{
 *   device: 'cpu' | 'cuda' | 'mps',
 *   gpu_name: string | null,
 *   reason: string
 * }} DiarizationInfo
 *
 * @typedef {{
 *   platform: { system: string, machine: string },
 *   transcription: TranscriptionInfo,
 *   diarization: DiarizationInfo
 * }} RuntimeInfo
 */

const LABELS = {
    transcription: {
        cpu: 'Transcrição: CPU',
        cuda: 'Transcrição: GPU NVIDIA',
    },
    diarization: {
        cpu: 'Diarização: CPU',
        cuda: 'Diarização: GPU NVIDIA',
        mps: 'Diarização: GPU Apple',
    },
};

/**
 * @param {RuntimeInfo} info
 * @returns {{ icon: 'gpu' | 'cpu', label: string, state: 'cpu' | 'gpu' }}
 */
function summarise(info) {
    // O badge prioriza GPU em qualquer engine — se algo está rodando em GPU,
    // queremos celebrar isso visualmente.
    const tx = info.transcription.device;
    const di = info.diarization.device;
    const usingGpu = tx === 'cuda' || di === 'cuda' || di === 'mps';

    if (usingGpu) {
        // Texto curto pro pill: priorizamos a engine que ESTA na GPU.
        const gpuName =
            (tx === 'cuda' && info.transcription.gpu_name) ||
            (di !== 'cpu' && info.diarization.gpu_name) ||
            'GPU';
        return { icon: 'gpu', label: gpuName, state: 'gpu' };
    }
    return { icon: 'cpu', label: 'CPU', state: 'cpu' };
}

/**
 * @param {RuntimeInfo} info
 */
function openPopover(info) {
    const root = document.getElementById('modal-root');
    if (!root) return;

    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.setAttribute('role', 'dialog');
    backdrop.setAttribute('aria-modal', 'true');

    const dialog = document.createElement('div');
    dialog.className = 'runtime-popover';

    const tx = info.transcription;
    const di = info.diarization;
    const txDeviceLabel = LABELS.transcription[tx.device] || tx.device.toUpperCase();
    const diDeviceLabel = LABELS.diarization[di.device] || di.device.toUpperCase();

    const txAccel = tx.device === 'cuda';
    const diAccel = di.device !== 'cpu';

    dialog.innerHTML = `
        <h3>Hardware de inferência</h3>

        <div class="runtime-popover-row">
            <p class="runtime-popover-section-title">Transcrição (Whisper)</p>
            <dl>
                <dt>Onde</dt>
                <dd class="${txAccel ? 'pos' : ''}">${txDeviceLabel}</dd>
                ${tx.gpu_name ? `<dt>GPU</dt><dd>${tx.gpu_name}</dd>` : ''}
                <dt>Precisão</dt>
                <dd>${tx.compute_type}</dd>
                ${tx.cpu_threads ? `<dt>Threads</dt><dd>${tx.cpu_threads}</dd>` : ''}
                <dt>Motivo</dt>
                <dd style="font-family: inherit; font-size: 0.72rem; color: var(--color-muted);">${tx.reason}</dd>
            </dl>
        </div>

        <div class="runtime-popover-row">
            <p class="runtime-popover-section-title">Identificação de falantes (pyannote)</p>
            <dl>
                <dt>Onde</dt>
                <dd class="${diAccel ? 'pos' : ''}">${diDeviceLabel}</dd>
                ${di.gpu_name ? `<dt>GPU</dt><dd>${di.gpu_name}</dd>` : ''}
                <dt>Motivo</dt>
                <dd style="font-family: inherit; font-size: 0.72rem; color: var(--color-muted);">${di.reason}</dd>
            </dl>
        </div>

        <p class="runtime-popover-foot">
            Sistema: <code>${info.platform.system} ${info.platform.machine}</code>.
            Override via <code>TRANSCRIPTOR_DEVICE=cpu|cuda</code> e
            <code>TRANSCRIPTOR_COMPUTE=float16|int8_float16|int8</code>.
        </p>

        <div class="modal-actions" style="margin-top: 1rem;">
            <button type="button" class="btn-secondary" data-act="close">Fechar</button>
        </div>
    `;

    backdrop.appendChild(dialog);
    root.appendChild(backdrop);
    requestAnimationFrame(() => backdrop.classList.add('in'));

    const close = () => {
        backdrop.classList.remove('in');
        document.removeEventListener('keydown', onKey);
        setTimeout(() => backdrop.remove(), 180);
    };
    function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); close(); }
    }
    document.addEventListener('keydown', onKey);
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) close();
    });
    dialog.querySelector('[data-act="close"]').addEventListener('click', close);
}

/**
 * Inicializa o badge: chama /api/runtime e popula o pill no topnav.
 * Falha silenciosamente — se o endpoint sumir, o badge fica em "erro" mas
 * não bloqueia o resto da app.
 */
export async function initRuntimeBadge() {
    const badge = el('runtime-badge');
    const iconHost = el('runtime-icon');
    const labelHost = el('runtime-label');
    if (!badge || !iconHost || !labelHost) return;

    let info = null;
    try {
        info = await getRuntimeInfo();
    } catch {
        badge.classList.remove('state-loading');
        badge.classList.add('state-error');
        labelHost.textContent = 'runtime?';
        return;
    }

    const { icon: iconName, label, state } = summarise(info);
    iconHost.dataset.icon = iconName;
    iconHost.innerHTML = '';
    hydrateIcons(iconHost.parentElement || iconHost);
    labelHost.textContent = label;
    badge.classList.remove('state-loading');
    badge.classList.add(`state-${state}`);
    badge.title =
        `${LABELS.transcription[info.transcription.device]} · ` +
        `${LABELS.diarization[info.diarization.device]}`;

    badge.addEventListener('click', () => openPopover(info));
}
