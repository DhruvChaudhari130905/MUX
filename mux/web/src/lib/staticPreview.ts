// Renders a room's plain HTML/CSS/JS without a dev server: the page's local stylesheets, scripts and
// images are inlined so it can run in a sandboxed srcdoc iframe.
import { isBinaryContent, mimeFor } from './binaryFiles';

type Files = Map<string, { content: string }>;

const ENTRY_CANDIDATES = ['index.html', 'public/index.html', 'src/index.html', 'static/index.html', 'www/index.html'];

// Asks the parent to open another room page instead of navigating the sandboxed frame
const NAVIGATE_SCRIPT = `<script>
document.addEventListener('click', function (e) {
  var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
  if (!a) return;
  var href = a.getAttribute('href') || '';
  if (/^([a-z]+:|\\/\\/|#)/i.test(href)) return;
  e.preventDefault();
  parent.postMessage({ muxPreviewNavigate: href }, '*');
});
</script>`;

// Tells the parent about errors the page throws, so the coder can be asked to fix them
const ERROR_SCRIPT = `<script>
(function () {
  function report(text) { parent.postMessage({ muxPreviewError: String(text).slice(0, 1000) }, '*'); }
  window.addEventListener('error', function (e) {
    if (e.error || e.message) report((e.error && e.error.stack) || (e.message + ' (line ' + e.lineno + ')'));
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    report('Unhandled promise rejection: ' + ((r && (r.stack || r.message)) || r));
  });
})();
</script>`;

// The sandboxed frame has no storage, so reading localStorage throws; give it in-memory stand-ins
const STORAGE_SHIM = `<script>
(function () {
  function memory() {
    var data = {};
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
      setItem: function (k, v) { data[k] = String(v); },
      removeItem: function (k) { delete data[k]; },
      clear: function () { data = {}; },
      key: function (i) { return Object.keys(data)[i] || null; },
      get length() { return Object.keys(data).length; }
    };
  }
  ['localStorage', 'sessionStorage'].forEach(function (name) {
    try { window[name].length; } catch (e) { Object.defineProperty(window, name, { value: memory() }); }
  });
})();
</script>`;

// Images a page's scripts add later (`<img src="${product.image}">`) can't be inlined ahead of time: the
// frame is srcdoc, so a relative src would point nowhere. This swaps such srcs for the room's file as they
// appear. `assets` maps room paths to data URLs; `dir` is the page's folder.
function assetScript(assets: Record<string, string>, dir: string): string {
  return `<script>
(function () {
  var assets = ${JSON.stringify(assets).replace(/</g, '\\u003c')}, dir = ${JSON.stringify(dir)};
  function resolve(ref) {
    var clean = (ref || '').split(/[?#]/)[0];
    if (!clean || /^([a-z]+:|\\/\\/)/i.test(clean)) return null;
    var tries = clean.charAt(0) === '/' ? [clean.slice(1), dir ? dir + clean : clean.slice(1)] : [dir ? dir + '/' + clean : clean];
    for (var i = 0; i < tries.length; i++) {
      var parts = [];
      tries[i].split('/').forEach(function (p) { if (p === '..') parts.pop(); else if (p && p !== '.') parts.push(p); });
      var hit = assets[parts.join('/')];
      if (hit) return hit;
    }
    return null;
  }
  function fix(el) {
    if (!el || el.nodeType !== 1) return;
    ['src', 'poster'].forEach(function (attr) {
      var url = el.hasAttribute(attr) ? resolve(el.getAttribute(attr)) : null;
      if (url) el.setAttribute(attr, url);
    });
    if (el.hasAttribute('srcset')) {
      var first = resolve(el.getAttribute('srcset').split(',')[0].trim().split(/\s+/)[0]);
      if (first) { el.removeAttribute('srcset'); el.setAttribute('src', first); }
    }
    if (el.querySelectorAll) Array.prototype.forEach.call(el.querySelectorAll('[src],[srcset],[poster]'), fix);
  }
  new MutationObserver(function (records) {
    records.forEach(function (r) {
      if (r.type === 'attributes') fix(r.target);
      else Array.prototype.forEach.call(r.addedNodes, fix);
    });
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'srcset', 'poster'] });
})();
</script>`;
}

export function htmlPages(files: Files): string[] {
  return Array.from(files.keys()).filter(p => p.endsWith('.html') && !p.includes('node_modules/')).sort();
}

export function findEntryPage(files: Files): string | null {
  const found = ENTRY_CANDIDATES.find(p => files.get(p)?.content.trim());
  if (found) return found;
  const html = htmlPages(files).filter(p => files.get(p)?.content.trim());
  return html.sort((a, b) => a.split('/').length - b.split('/').length)[0] ?? null;
}

// Resolves a reference made from `fromPage` to a room file. Root-relative paths (`/styles.css`) are
// tried against the page's own folder too, since static sites often serve `public/` as the root, and
// against `public/` itself (the web root in Vite projects, where the agent's add_image saves photos).
export function resolveRef(files: Files, fromPage: string, ref: string): string | null {
  const clean = ref.split(/[?#]/)[0];
  if (!clean || /^([a-z]+:|\/\/)/i.test(clean)) return null;
  const dir = fromPage.includes('/') ? fromPage.slice(0, fromPage.lastIndexOf('/')) : '';
  const candidates = clean.startsWith('/')
    ? [clean.slice(1), dir ? `${dir}${clean}` : clean.slice(1), `public${clean}`]
    : [dir ? `${dir}/${clean}` : clean];
  for (const candidate of candidates) {
    const parts: string[] = [];
    for (const part of candidate.split('/')) {
      if (part === '..') parts.pop();
      else if (part && part !== '.') parts.push(part);
    }
    const path = parts.join('/');
    if (files.has(path)) return path;
  }
  return null;
}

// A room file as something the frame can load: binary files are already data URLs, SVG is text
function assetUrl(files: Files, path: string | null): string | null {
  const content = path ? files.get(path)?.content : undefined;
  if (!path || content === undefined) return null;
  if (isBinaryContent(content)) return content;
  if (mimeFor(path) === 'image/svg+xml') return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}`;
  return null;
}

// Points `url(...)` in CSS (backgrounds, @font-face) at inlined assets; `fromFile` is where the CSS lives
function inlineCssUrls(files: Files, fromFile: string, css: string): string {
  return css.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (match, quote: string, ref: string) => {
    const url = assetUrl(files, resolveRef(files, fromFile, ref.trim()));
    return url ? `url("${url}")` : match;
  });
}

export function buildStaticPage(files: Files, page: string): string {
  const doc = new DOMParser().parseFromString(files.get(page)?.content ?? '', 'text/html');

  // The page's own CSS; linked stylesheets are resolved from their own folder below
  doc.querySelectorAll('style').forEach(style => {
    style.textContent = inlineCssUrls(files, page, style.textContent ?? '');
  });
  doc.querySelectorAll('[style]').forEach(el => {
    el.setAttribute('style', inlineCssUrls(files, page, el.getAttribute('style')!));
  });

  doc.querySelectorAll('link[rel~="stylesheet"][href]').forEach(link => {
    const path = resolveRef(files, page, link.getAttribute('href')!);
    if (!path) return;
    const style = doc.createElement('style');
    style.textContent = inlineCssUrls(files, path, files.get(path)!.content);
    link.replaceWith(style);
  });

  doc.querySelectorAll('script[src]').forEach(script => {
    const path = resolveRef(files, page, script.getAttribute('src')!);
    if (!path) return;
    const inline = doc.createElement('script');
    if (script.getAttribute('type')) inline.setAttribute('type', script.getAttribute('type')!);
    // `</script` inside the code would end the inline tag early
    inline.textContent = files.get(path)!.content.replace(/<\/script/gi, '<\\/script');
    script.replaceWith(inline);
  });

  const ASSET_ATTRS: [string, string][] = [
    ['img[src]', 'src'], ['source[src]', 'src'], ['video[src]', 'src'], ['audio[src]', 'src'],
    ['video[poster]', 'poster'], ['input[type="image"][src]', 'src'], ['link[rel~="icon"][href]', 'href'],
  ];
  for (const [selector, attr] of ASSET_ATTRS) {
    doc.querySelectorAll(selector).forEach(el => {
      const url = assetUrl(files, resolveRef(files, page, el.getAttribute(attr)!));
      if (url) el.setAttribute(attr, url);
    });
  }
  // A srcset would win over the inlined src and point at a file the frame can't fetch
  doc.querySelectorAll('img[srcset], source[srcset]').forEach(el => {
    const first = el.getAttribute('srcset')!.split(',')[0]?.trim().split(/\s+/)[0];
    const url = first ? assetUrl(files, resolveRef(files, page, first)) : null;
    if (!url) return;
    el.removeAttribute('srcset');
    el.setAttribute('src', url);
  });

  // The shims must run before the page's own scripts
  const assets: Record<string, string> = {};
  files.forEach((_file, path) => {
    const url = assetUrl(files, path);
    if (url) assets[path] = url;
  });
  const dir = page.includes('/') ? page.slice(0, page.lastIndexOf('/')) : '';
  const shim = doc.createElement('div');
  shim.innerHTML = ERROR_SCRIPT + STORAGE_SHIM + (Object.keys(assets).length ? assetScript(assets, dir) : '');
  doc.head.prepend(...Array.from(shim.childNodes));

  return `<!doctype html>\n${doc.documentElement.outerHTML.replace('</body>', `${NAVIGATE_SCRIPT}</body>`)}`;
}
