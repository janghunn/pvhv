/* Interactive PVHV/EDP demo loader (classic script, loaded with defer).
 * Mounts into #demo-root, replacing only its .slot-box child.  Without WebGL2 float
 * render targets it shows a static message; otherwise it loads demo/app.js as a module. */
(function () {
  'use strict';

  var script = document.currentScript;
  var scriptSrc = script && script.src ? script.src : '';

  var LABEL = 'Re-implementation (2026), not the code used for the paper\'s experiments';
  var NO_WEBGL = 'This interactive demo needs WebGL2 with float render targets (EXT_color_buffer_float), ' +
    'which this browser does not provide.';
  var DEFAULT_ATTRIBUTION = {
    attribution_text: 'Chess Set by Riley Queen, Poly Haven',
    attribution_url: 'https://polyhaven.com/a/chess_set',
    licence: 'CC0 1.0',
    licence_url: 'https://creativecommons.org/publicdomain/zero/1.0/'
  };

  var CSS = [
    '.pvhv-demo{max-width:100%;min-width:0;color:var(--ink,#1d2127);font:inherit}',
    '.pvhv-demo *{box-sizing:border-box}',
    '.pvhv-demo-stage{min-width:0}',
    '.pvhv-demo-viewwrap{position:relative;max-width:100%}',
    '.pvhv-demo-view{display:block;width:100%;height:auto;aspect-ratio:16/9;touch-action:none;cursor:grab;' +
      'border-radius:var(--r,10px);border:1px solid var(--line,#ddd7cc);background:var(--bg,#f5f2ec)}',
    '.pvhv-demo-view.pvhv-demo-dragging{cursor:grabbing}',
    '.pvhv-demo-bound{position:absolute;right:2%;bottom:3%;width:min(3.5rem,13%);height:auto;aspect-ratio:1;pointer-events:none;' +
      'border-radius:50%;background:rgba(255,253,249,.82)}',
    '.pvhv-demo-bound circle.ring{fill:none;stroke:var(--muted,#5d6570);stroke-width:.06}',
    '.pvhv-demo-bound line{stroke:var(--line,#ddd7cc);stroke-width:.04}',
    '.pvhv-demo-bound circle.dot{fill:var(--accent,#0072b2)}',
    '.pvhv-demo-busy{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:1rem;' +
      'text-align:center;border-radius:var(--r,10px);background:rgba(245,242,236,.78);color:var(--ink,#1d2127)}',
    '.pvhv-demo-busy[hidden]{display:none}',
    '.pvhv-demo-help{margin:.55rem 0 0;font-size:.9em;color:var(--muted,#5d6570)}',
    '.pvhv-demo-controls{display:flex;flex-wrap:wrap;align-items:center;gap:.75rem 1.25rem;margin-top:.75rem}',
    '.pvhv-demo fieldset{display:flex;flex-wrap:wrap;gap:.3rem 1rem;min-width:0;max-width:100%;margin:0;' +
      'padding:.35rem .8rem .55rem;border:1px solid var(--line,#ddd7cc);border-radius:var(--r,10px)}',
    '.pvhv-demo legend{padding:0 .3rem;font-size:.85em;color:var(--muted,#5d6570)}',
    '.pvhv-demo label{display:inline-flex;align-items:center;gap:.4rem;max-width:100%;cursor:pointer}',
    '.pvhv-demo input{accent-color:var(--accent,#0072b2);margin:0}',
    '.pvhv-demo select,.pvhv-demo button{font:inherit;color:var(--ink,#1d2127);background:var(--surface,#fffdf9);' +
      'border:1px solid var(--control,#7d7466);border-radius:var(--r,10px);padding:.3rem .7rem;max-width:100%}',
    '.pvhv-demo button{cursor:pointer}',
    '.pvhv-demo button:hover,.pvhv-demo select:hover{border-color:var(--accent,#0072b2)}',
    '.pvhv-demo :focus-visible{outline:3px solid var(--paint,#98520b);outline-offset:3px}',
    '.pvhv-demo-status{margin:.65rem 0 0;font-size:.9em;color:var(--muted,#5d6570);font-variant-numeric:tabular-nums}',
    '.pvhv-demo-caption{margin-top:.75rem;padding-top:.6rem;border-top:1px solid var(--line,#ddd7cc);' +
      'font-size:.85em;color:var(--muted,#5d6570)}',
    '.pvhv-demo-caption p{margin:.15rem 0}',
    '.pvhv-demo-label{font-weight:700;color:var(--ink,#1d2127)}',
    '.pvhv-demo a{color:var(--accent,#0072b2)}',
    '.pvhv-demo-fallback{display:flex;flex-direction:column;justify-content:center;gap:.5rem;min-height:10rem;' +
      'padding:1.25rem 1.5rem;border:1px dashed var(--control,#7d7466);border-radius:var(--r,10px);' +
      'background:var(--surface,#fffdf9);color:var(--ink,#1d2127)}',
    '.pvhv-demo-fallback p{margin:0}'
  ].join('\n');

  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text) e.textContent = text;
    return e;
  }

  function injectStyle() {
    if (document.getElementById('pvhv-demo-style')) return;
    var style = el('style');
    style.id = 'pvhv-demo-style';
    style.textContent = CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  /** Fill the attribution line from scene metadata ({attribution_text, attribution_url, licence, licence_url}). */
  function setAttribution(node, meta) {
    while (node.firstChild) node.removeChild(node.firstChild);
    node.appendChild(document.createTextNode('Scene: '));
    var work = meta.attribution_url ? el('a', '', meta.attribution_text) : el('span', '', meta.attribution_text);
    if (meta.attribution_url) work.href = meta.attribution_url;
    node.appendChild(work);
    if (meta.licence && meta.licence_url) {
      node.appendChild(document.createTextNode(', '));
      var lic = el('a', '', meta.licence);
      lic.href = meta.licence_url;
      node.appendChild(lic);
    }
  }

  function buildContainer(root) {
    var container = el('div', 'pvhv-demo');
    var stage = el('div', 'pvhv-demo-stage');
    var caption = el('div', 'pvhv-demo-caption');
    caption.appendChild(el('p', 'pvhv-demo-label', LABEL));
    var attrib = el('p', 'pvhv-demo-attrib');
    setAttribution(attrib, DEFAULT_ATTRIBUTION);
    caption.appendChild(attrib);
    container.appendChild(stage);
    container.appendChild(caption);
    var slot = root.querySelector('.slot-box');
    if (slot && slot.parentNode === root) root.replaceChild(container, slot);
    else root.insertBefore(container, root.firstChild);
    return container;
  }

  function showFallback(container, reason) {
    var stage = container.querySelector('.pvhv-demo-stage');
    while (stage.firstChild) stage.removeChild(stage.firstChild);
    var box = el('div', 'pvhv-demo-fallback');
    box.setAttribute('role', 'status');
    box.appendChild(el('p', '', reason ? 'The interactive demo stopped: ' + reason + '.' : NO_WEBGL));
    stage.appendChild(box);
  }

  function supported() {
    try {
      var canvas = document.createElement('canvas');
      var gl = canvas.getContext('webgl2');
      if (!gl) return false;
      var ok = !!gl.getExtension('EXT_color_buffer_float') && gl.getParameter(gl.MAX_DRAW_BUFFERS) >= 4;
      var lose = gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
      return ok;
    } catch (e) {
      return false;
    }
  }

  function init() {
    var root = document.getElementById('demo-root');
    if (!root) return;
    injectStyle();
    var container = buildContainer(root);
    if (!scriptSrc || !supported()) {
      showFallback(container);
      return;
    }
    var base = new URL('.', scriptSrc).href;
    window.__PVHV_DEMO_BOOT__ = {
      root: root,
      container: container,
      base: base,
      fallback: function (reason) { showFallback(container, reason); },
      setAttribution: function (meta) { setAttribution(container.querySelector('.pvhv-demo-attrib'), meta); }
    };
    var mod = document.createElement('script');
    mod.type = 'module';
    mod.src = new URL('demo/app.js', base).href;
    mod.onerror = function () { showFallback(container, 'the demo code could not be loaded'); };
    document.head.appendChild(mod);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
