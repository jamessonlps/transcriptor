// ``bind(root)`` — small helper that cuts the ``el('foo', root)`` boilerplate
// that was repeated dozens of times across views.
//
// Usage:
//
//   const ui = bind(root);
//   ui('start-btn').on('click', start);
//   ui('progress-bar').css('width', '50%');
//   ui('status-text').text('Loading…');
//   ui('errors').show();  // / hide()
//
// Each call returns a chainable handle around the matched element. Missing
// ``data-el`` names return a sentinel that no-ops every method — so a typo
// won't crash the page, it just silently does nothing (with a warning).

const NOOP_WARNED = new Set();

function noopHandle(name) {
    if (!NOOP_WARNED.has(name)) {
        console.warn(`[bind] data-el="${name}" not found in root`);
        NOOP_WARNED.add(name);
    }
    const proxy = new Proxy(
        { node: null },
        {
            get(target, prop) {
                if (prop === 'node') return null;
                // All chainable methods return the proxy itself.
                return () => proxy;
            },
        }
    );
    return proxy;
}

function makeHandle(node) {
    const h = {
        node,
        // Visibility
        show() { node.classList.remove('hidden'); return h; },
        hide() { node.classList.add('hidden'); return h; },
        toggle(force) {
            node.classList.toggle('hidden', force === undefined ? undefined : !force);
            return h;
        },

        // Content
        text(value) { node.textContent = value; return h; },
        html(value) { node.innerHTML = value; return h; },

        // Attributes & state
        attr(name, value) {
            if (value == null) node.removeAttribute(name);
            else node.setAttribute(name, value);
            return h;
        },
        prop(name, value) { node[name] = value; return h; },
        disabled(flag) { node.disabled = flag; return h; },
        css(prop, value) { node.style[prop] = value; return h; },
        addClass(...cls) { node.classList.add(...cls); return h; },
        removeClass(...cls) { node.classList.remove(...cls); return h; },

        // Events
        on(event, fn, opts) { node.addEventListener(event, fn, opts); return h; },
        off(event, fn) { node.removeEventListener(event, fn); return h; },

        // DOM
        append(child) { node.appendChild(child); return h; },
        clear() { node.innerHTML = ''; return h; },
    };
    return h;
}

/**
 * Bind to a root container and return a function ``ui(name)`` that resolves
 * elements by their ``data-el`` attribute.
 *
 * @param {ParentNode} root
 * @returns {(name: string) => ReturnType<typeof makeHandle>}
 */
export function bind(root) {
    return function ui(name) {
        const node = root.querySelector(`[data-el="${name}"]`);
        return node ? makeHandle(node) : noopHandle(name);
    };
}
