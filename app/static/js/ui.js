// UI utilities: query selectors data-attr-aware, formatters, toasts.

import { hydrateIcons } from './components/icons.js';

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Convenience: get an element by data-el="..." within a root container.
// Padroniza pra não precisar repetir o seletor todo dia.
export const el = (name, root = document) => root.querySelector(`[data-el="${name}"]`);
export const els = (name, root = document) => Array.from(root.querySelectorAll(`[data-el="${name}"]`));

export const show = (node) => node && node.classList.remove('hidden');
export const hide = (node) => node && node.classList.add('hidden');

// ------- Formatters -------

export function fmtBytes(bytes) {
    if (bytes == null || isNaN(bytes)) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function fmtTime(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// ------- Toasts -------

let toastStack;
function ensureStack() {
    if (!toastStack) toastStack = document.getElementById('toast-stack');
    return toastStack;
}

export function toast(message, { type = 'info', timeout = 4000 } = {}) {
    const stack = ensureStack();
    if (!stack) return;

    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.innerHTML = `
        <span class="toast-icon">${type === 'error' ? '⚠' : type === 'success' ? '✓' : 'ℹ'}</span>
        <span class="toast-msg"></span>
        <button class="toast-close" aria-label="Fechar">×</button>
    `;
    t.querySelector('.toast-msg').textContent = message;
    t.querySelector('.toast-close').addEventListener('click', () => dismiss(t));
    stack.appendChild(t);

    requestAnimationFrame(() => t.classList.add('in'));

    let timer;
    if (timeout > 0) {
        timer = setTimeout(() => dismiss(t), timeout);
    }

    function dismiss(node) {
        clearTimeout(timer);
        node.classList.remove('in');
        node.classList.add('out');
        setTimeout(() => node.remove(), 220);
    }

    return { dismiss: () => dismiss(t) };
}

// ------- Template instantiation -------

export function cloneTemplate(templateId) {
    const tpl = document.getElementById(templateId);
    if (!tpl) throw new Error(`Template não encontrado: ${templateId}`);
    const frag = tpl.content.cloneNode(true);
    // Templates declare icons via ``data-icon="name"`` placeholders. We
    // hydrate them here so callers don't have to remember to do it.
    hydrateIcons(frag);
    return frag;
}

// ------- Modal (confirmation dialog) -------

/**
 * Mostra um diálogo de confirmação dentro da própria app (substitui o `confirm()` nativo).
 *
 * @param {object} opts
 * @param {string} opts.title      — Título curto.
 * @param {string} opts.message    — Mensagem (pode ter \n).
 * @param {string} [opts.confirmText="Confirmar"]
 * @param {string} [opts.cancelText="Cancelar"]
 * @param {boolean} [opts.danger=false] — Se true, o botão de confirmação fica vermelho.
 * @returns {Promise<boolean>} resolve com true se confirmou, false se cancelou.
 */
export function confirmModal({
    title,
    message,
    confirmText = 'Confirmar',
    cancelText = 'Cancelar',
    danger = false,
} = {}) {
    return new Promise((resolve) => {
        const root = document.getElementById('modal-root');
        if (!root) { resolve(false); return; }

        const backdrop = document.createElement('div');
        backdrop.className = 'modal-backdrop';
        backdrop.setAttribute('role', 'dialog');
        backdrop.setAttribute('aria-modal', 'true');

        const dialog = document.createElement('div');
        dialog.className = 'modal-dialog';
        dialog.innerHTML = `
            <h3 class="modal-title"></h3>
            <p class="modal-message"></p>
            <div class="modal-actions">
                <button type="button" class="btn-secondary" data-act="cancel"></button>
                <button type="button" data-act="confirm"></button>
            </div>
        `;
        dialog.querySelector('.modal-title').textContent = title || '';
        dialog.querySelector('.modal-message').textContent = message || '';

        const cancelBtn = dialog.querySelector('[data-act="cancel"]');
        const confirmBtn = dialog.querySelector('[data-act="confirm"]');
        cancelBtn.textContent = cancelText;
        confirmBtn.textContent = confirmText;
        confirmBtn.className = danger ? 'btn-danger' : 'btn-primary';

        backdrop.appendChild(dialog);
        root.appendChild(backdrop);

        // Anima entrada
        requestAnimationFrame(() => backdrop.classList.add('in'));

        // Foco inicial — confirmação fica em destaque
        setTimeout(() => confirmBtn.focus(), 30);

        const close = (result) => {
            backdrop.classList.remove('in');
            document.removeEventListener('keydown', onKey);
            setTimeout(() => {
                backdrop.remove();
                resolve(result);
            }, 180);
        };

        cancelBtn.addEventListener('click', () => close(false));
        confirmBtn.addEventListener('click', () => close(true));
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) close(false);
        });

        function onKey(e) {
            if (e.key === 'Escape') { e.preventDefault(); close(false); }
            else if (e.key === 'Enter') { e.preventDefault(); close(true); }
        }
        document.addEventListener('keydown', onKey);
    });
}
