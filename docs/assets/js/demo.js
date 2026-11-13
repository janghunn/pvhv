/* Interactive PVHV/EDP demo loader (classic script, loaded with defer).
 * Mounts into #demo-root, replacing only its .slot-box child.  Without WebGL2 float
 * render targets it shows a static message; otherwise it loads demo/app.js as a module. */
(function () {
  'use strict';

  var script = document.currentScript;
  var scriptSrc = script && script.src ? script.src : '';

  var STRINGS = {
    en: {
      label: 'Re-implementation (2026), not the code used for the paper\'s experiments',
      noWebgl: 'This interactive demo needs WebGL2 with float render targets (EXT_color_buffer_float), ' +
        'which this browser does not provide.',
      scene: 'Scene: ',
      stopped: function (reason) { return 'The interactive demo stopped: ' + reason + '.'; },
      attributions: {}
    },
    ko: {
      label: '재구현(2026)이며, 논문 실험에 쓰인 코드가 아닙니다',
      noWebgl: '이 인터랙티브 데모에는 부동소수점 렌더 타깃(EXT_color_buffer_float)을 지원하는 WebGL2가 필요하지만, 이 브라우저는 지원하지 않습니다.',
      scene: '장면: ',
      stopped: function (reason) { return '인터랙티브 데모가 중단되었습니다: ' + reason + '.'; },
      attributions: {
        'Procedural blocks scene (part of this re-implementation)': '절차적으로 생성한 블록 장면 (이 재구현의 일부)'
      },
      reasons: [
        [/^the WebGL context was lost$/, function () { return 'WebGL 컨텍스트가 소실되었습니다'; }],
        [/^could not load (.+) \((\d+)\)$/, function (_, file, code) { return file + '을(를) 불러올 수 없습니다 (' + code + ')'; }],
        [/^could not decode (.+)$/, function (_, file) { return file + '을(를) 디코딩할 수 없습니다'; }],
        [/^(.+\/geometry\.bin) has the wrong size$/, function (_, file) { return file + '의 크기가 올바르지 않습니다'; }],
        [/^WebGL2 is not available$/, function () { return 'WebGL2를 사용할 수 없습니다'; }],
        [/^float render targets \(EXT_color_buffer_float\) are not available$/, function () { return '부동소수점 렌더 타깃(EXT_color_buffer_float)을 사용할 수 없습니다'; }],
        [/^the demo code could not be loaded$/, function () { return '데모 코드를 불러올 수 없습니다'; }],
        [/^the demo stopped$/, function () { return '데모가 중단되었습니다'; }]
      ]
    }
  };
  function lang() { return document.documentElement.lang === 'ko' ? 'ko' : 'en'; }
  function translatedReason(reason) {
    if (lang() !== 'ko') return reason;
    for (var entry of STRINGS.ko.reasons) {
      if (entry[0].test(reason)) return reason.replace(entry[0], entry[1]);
    }
    return reason;
  }
  var DEFAULT_ATTRIBUTION = {
    attribution_text: 'Chess Set by Riley Queen, Poly Haven',
    attribution_url: 'https://polyhaven.com/a/chess_set',
    licence: 'CC0 1.0',
    licence_url: 'https://creativecommons.org/publicdomain/zero/1.0/'
  };

  var CSS = [
    '.pvhv-demo{max-width:100%;min-width:0;color:var(--ink);font:inherit}',
    '.pvhv-demo *{box-sizing:border-box}',
    '.pvhv-demo-stage{min-width:0}',
    '.pvhv-demo-viewwrap{position:relative;max-width:100%}',
    '.pvhv-demo-view{display:block;width:100%;height:auto;aspect-ratio:16/9;touch-action:none;cursor:grab;' +
      'border-radius:var(--r-s);border:1px solid var(--line);background:var(--bg)}',
    '.pvhv-demo-view.pvhv-demo-dragging{cursor:grabbing}',
    '.pvhv-demo-bound{position:absolute;right:2%;bottom:3%;width:min(3.5rem,13%);height:auto;aspect-ratio:1;pointer-events:none;' +
      'border-radius:50%;background:color-mix(in srgb, var(--surface) 82%, transparent)}',
    '.pvhv-demo-bound circle.ring{fill:none;stroke:var(--muted);stroke-width:.06}',
    '.pvhv-demo-bound line{stroke:var(--control);stroke-width:.04}',
    '.pvhv-demo-bound circle.dot{fill:var(--accent)}',
    '.pvhv-demo-busy{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:1rem;' +
      'text-align:center;border-radius:var(--r-s);background:color-mix(in srgb, var(--bg) 85%, transparent);color:var(--ink)}',
    '.pvhv-demo-busy[hidden]{display:none}',
    '.pvhv-demo-help{margin:.55rem 0 0;font-size:.9em;color:var(--muted)}',
    '.pvhv-demo-controls{display:flex;flex-wrap:wrap;align-items:center;gap:.75rem 1.25rem;margin-top:.75rem}',
    '.pvhv-demo fieldset{display:flex;flex-wrap:wrap;gap:.3rem 1rem;min-width:0;max-width:100%;margin:0;' +
      'padding:.35rem .8rem .55rem;border:1px solid var(--line);border-radius:var(--r-s)}',
    '.pvhv-demo legend{padding:0 .3rem;font-size:.85em;color:var(--muted)}',
    '.pvhv-demo label{min-height:44px;display:inline-flex;align-items:center;gap:.4rem;max-width:100%;cursor:pointer}',
    '.pvhv-demo input{accent-color:var(--accent);margin:0}',
    '.pvhv-demo select,.pvhv-demo button{min-height:44px;font:inherit;color:var(--ink);background-color:var(--surface);' +
      'border:1px solid var(--control);border-radius:var(--r-s);padding:.3rem .7rem;max-width:100%}',
    '.pvhv-demo button{cursor:pointer}',
    '.pvhv-demo button:hover,.pvhv-demo select:hover{border-color:var(--accent)}',
    '.pvhv-demo :focus-visible{outline:2px solid var(--focus);outline-offset:2px}',
    '.pvhv-demo-status{margin:.65rem 0 0;font-size:.9em;color:var(--muted);font-variant-numeric:tabular-nums}',
    '.pvhv-demo-caption{margin-top:.75rem;padding-top:.6rem;border-top:1px solid var(--line);' +
      'font-size:.85em;color:var(--muted)}',
    '.pvhv-demo-caption p{margin:.15rem 0}',
    '.pvhv-demo-label{font-weight:700;color:var(--ink)}',
    '.pvhv-demo a{color:var(--accent)}',
    '.pvhv-demo-fallback{display:flex;flex-direction:column;justify-content:center;gap:.5rem;min-height:10rem;' +
      'padding:1.25rem 1.5rem;border:1px dashed var(--control);border-radius:var(--r-s);' +
      'background-color:var(--surface);color:var(--ink)}',
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

  function translateAttribution(node, meta) {
    node.firstChild.textContent = STRINGS[lang()].scene;
    node.childNodes[1].textContent = STRINGS[lang()].attributions[meta.attribution_text] || meta.attribution_text;
  }

  /** Fill the attribution line from scene metadata ({attribution_text, attribution_url, licence, licence_url}). */
  function setAttribution(node, meta) {
    while (node.firstChild) node.removeChild(node.firstChild);
    node.appendChild(document.createTextNode(STRINGS[lang()].scene));
    var text = STRINGS[lang()].attributions[meta.attribution_text] || meta.attribution_text;
    var work = meta.attribution_url ? el('a', '', text) : el('span', '', text);
    if (meta.attribution_url) work.href = meta.attribution_url;
    node.appendChild(work);
    if (meta.licence && meta.licence_url) {
      node.appendChild(document.createTextNode(', '));
      var lic = el('a', '', meta.licence);
      lic.href = meta.licence_url;
      node.appendChild(lic);
    }
  }

  function fallbackText(reason) {
    return reason ? STRINGS[lang()].stopped(translatedReason(reason)) : STRINGS[lang()].noWebgl;
  }

  function buildContainer(root) {
    var container = el('div', 'pvhv-demo');
    var stage = el('div', 'pvhv-demo-stage');
    var caption = el('div', 'pvhv-demo-caption');
    caption.appendChild(el('p', 'pvhv-demo-label', STRINGS[lang()].label));
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
    box.appendChild(el('p', '', fallbackText(reason)));
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
    var attribution = DEFAULT_ATTRIBUTION;
    var fallbackReason;
    var fallbackShown = false;
    var fallback = function (reason) {
      fallbackReason = reason;
      fallbackShown = true;
      showFallback(container, reason);
    };
    document.addEventListener('ws:lang', function () {
      container.querySelector('.pvhv-demo-label').textContent = STRINGS[lang()].label;
      translateAttribution(container.querySelector('.pvhv-demo-attrib'), attribution);
      if (fallbackShown) container.querySelector('.pvhv-demo-fallback p').textContent = fallbackText(fallbackReason);
    });
    if (!scriptSrc || !supported()) {
      fallback();
      return;
    }
    var base = new URL('.', scriptSrc).href;
    window.__PVHV_DEMO_BOOT__ = {
      root: root,
      container: container,
      base: base,
      fallback: fallback,
      setAttribution: function (meta) {
        attribution = meta;
        setAttribution(container.querySelector('.pvhv-demo-attrib'), meta);
      }
    };
    var mod = document.createElement('script');
    mod.type = 'module';
    mod.src = new URL('demo/app.js', base).href;
    mod.onerror = function () { fallback('the demo code could not be loaded'); };
    document.head.appendChild(mod);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
