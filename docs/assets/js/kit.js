/* web-style-gradient / kit.js
   Vanilla, dependency-free, ES5-compatible. One IIFE; the only global is window.PPK.
   Parts: theme toggle (+ system tracking), language toggle (in-page swap) and language links (twin pages),
   motion control, reveal-on-scroll, hero drift pause (hidden tab, hero off screen), step-flow controller,
   loop controller, selected table row, copy buttons, video handling with a pause/play control.
   Hooks (data attributes): data-theme-toggle (+ data-label-to-dark, data-label-to-light), data-lang-toggle,
   data-en / data-ko (+ data-i18n-html), data-en-aria-label / data-ko-aria-label, a[data-lang-link],
   data-motion-toggle (+ data-motion-label, data-label-pause, data-label-resume), .rv, data-flow
   (+ data-flow-node, data-flow-select, data-flow-mode, data-flow-modes, data-flow-state, data-flow-run,
   data-flow-step, data-flow-reset, data-flow-caption), data-loop (+ data-loop-node, data-loop-title,
   data-loop-text, data-loop-hub, data-loop-hand, data-loop-num, data-loop-run, data-loop-run-label,
   data-loop-step, data-loop-reset, data-loop-caption, data-interval, data-label-stage, data-label-run,
   data-label-pause), data-row-select (+ data-row, data-label-selected), data-copy (+ data-copy-status,
   data-copy-label), video[data-autoplay] (+ data-label-pause, data-label-play).
   A data-label-<key> attribute may also be given per language as data-label-<key>-en / -ko.
   Storage: ppk-theme, ppk-lang, ppk-motion. Events on document: ppk:lang, ppk:motion. */
(function () {
  'use strict';
  var doc = document;
  var root = doc.documentElement;
  var THEME_KEY = 'ppk-theme';
  var LANG_KEY = 'ppk-lang';
  var MOTION_KEY = 'ppk-motion';
  var STR = {
    en: {
      toDark: 'Switch to dark theme', toLight: 'Switch to light theme', langBtn: '한국어',
      copied: 'Copied to clipboard', copiedShort: 'Copied', copyFail: 'Copy failed. Select the text and copy it manually.',
      run: 'Run', pause: 'Pause', on: 'active', off: 'skipped',
      motionPause: 'Pause motion', motionResume: 'Resume motion', videoPause: 'Pause video', videoPlay: 'Play video',
      stage: 'Stage', selected: '(selected)'
    },
    ko: {
      toDark: '어두운 테마로 전환', toLight: '밝은 테마로 전환', langBtn: 'English',
      copied: '클립보드에 복사했습니다', copiedShort: '복사됨',
      copyFail: '복사하지 못했습니다. 텍스트를 직접 선택해 복사하세요.',
      run: '실행', pause: '일시정지', on: '사용', off: '생략',
      motionPause: '움직임 멈추기', motionResume: '움직임 다시 켜기', videoPause: '동영상 일시정지', videoPlay: '동영상 재생',
      stage: '{n}단계', selected: '(선택됨)'
    }
  };

  root.classList.add('js');

  function all(sel, ctx) { return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel)); }
  function closest(el, sel) {
    while (el && el.nodeType === 1) {
      if ((el.matches || el.msMatchesSelector || el.webkitMatchesSelector).call(el, sel)) return el;
      el = el.parentNode;
    }
    return null;
  }
  function media(q) { return window.matchMedia ? window.matchMedia(q) : { matches: false }; }
  function onMedia(m, fn) {
    if (m.addEventListener) m.addEventListener('change', fn);
    else if (m.addListener) m.addListener(fn);
  }
  function lang() { return root.lang === 'ko' ? 'ko' : 'en'; }
  function str(key) { return STR[lang()][key]; }
  /* page copy for a label: data-label-<key>-<lang>, then data-label-<key>; null when neither is set */
  function attrLabel(el, key) {
    if (!el) return null;
    return el.getAttribute('data-label-' + key + '-' + lang()) || el.getAttribute('data-label-' + key) || null;
  }
  function emit(name) {
    var ev;
    try { ev = new CustomEvent(name); } catch (e) { ev = doc.createEvent('CustomEvent'); ev.initCustomEvent(name, false, false, null); }
    doc.dispatchEvent(ev);
  }
  function readTheme() {
    try {
      var v = window.localStorage.getItem(THEME_KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) { return null; }
  }
  function writeTheme(v) {
    try { window.localStorage.setItem(THEME_KEY, v); } catch (e) { /* storage blocked: choice lasts for this page view */ }
  }

  /* Capture the English strings once, before anything (copy feedback, step-flow) changes the text. */
  all('[data-ko],[data-copy-label]').forEach(function (el) {
    if (!el.hasAttribute('data-en')) el.setAttribute('data-en', el.hasAttribute('data-i18n-html') ? el.innerHTML : el.textContent);
  });
  all('[data-ko-aria-label]').forEach(function (el) {
    if (!el.hasAttribute('data-en-aria-label')) el.setAttribute('data-en-aria-label', el.getAttribute('aria-label') || '');
  });

  var reduce = media('(prefers-reduced-motion: reduce)');
  var prefersDark = media('(prefers-color-scheme: dark)');

  /* still(): nothing may move, because the OS asks for reduced motion or the viewer paused motion
     (html.motion-off). Controllers register with onStill() and hear every change of still(). */
  function still() { return reduce.matches || root.classList.contains('motion-off'); }
  var stillFns = [];
  var wasStill = still();
  function onStill(fn) { stillFns.push(fn); }
  function checkStill() {
    var s = still();
    if (s === wasStill) return;
    wasStill = s;
    stillFns.forEach(function (fn) { fn(s); });
  }
  onMedia(reduce, checkStill);
  if (window.MutationObserver) new MutationObserver(checkStill).observe(root, { attributes: true, attributeFilter: ['class'] });

  /* ---- theme ---- */
  var themeDefault = root.getAttribute('data-theme-default') || 'system';
  var override = readTheme();

  function theme() { return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; }
  function systemTheme() { return prefersDark.matches ? 'dark' : 'light'; }
  function paintTheme() {
    var cur = theme();
    all('[data-theme-toggle]').forEach(function (b) {
      /* page copy (data-label-to-dark / data-label-to-light) when present, else the kit strings */
      b.setAttribute('aria-label', attrLabel(b, cur === 'dark' ? 'to-light' : 'to-dark') || str(cur === 'dark' ? 'toLight' : 'toDark'));
    });
    all('meta[name="color-scheme"]').forEach(function (m) { m.setAttribute('content', cur); });
  }
  function setTheme(next, persist) {
    next = next === 'dark' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    if (persist) { override = next; writeTheme(next); }
    paintTheme();
  }
  if (!root.getAttribute('data-theme')) {
    setTheme(override || (themeDefault === 'system' ? systemTheme() : themeDefault), false);
  } else {
    paintTheme();
  }
  onMedia(prefersDark, function () {
    if (!override && themeDefault === 'system') setTheme(systemTheme(), false);
  });

  /* ---- language ---- */
  function setLang(next) {
    next = next === 'ko' ? 'ko' : 'en';
    all('[data-en],[data-ko]').forEach(function (el) {
      var asHtml = el.hasAttribute('data-i18n-html');
      var v = el.getAttribute('data-' + next);
      if (v === null) return;
      if (asHtml) el.innerHTML = v; else el.textContent = v;
    });
    all('[data-ko-aria-label]').forEach(function (el) {
      el.setAttribute('aria-label', el.getAttribute('data-' + next + '-aria-label'));
    });
    root.lang = next;
    all('[data-lang-toggle]').forEach(function (b) {
      var label = b.querySelector('[data-lang-label]') || b;
      label.textContent = str('langBtn');
      b.setAttribute('lang', next === 'ko' ? 'en' : 'ko');
    });
    paintTheme();
    emit('ppk:lang');
  }

  /* ---- language links (twin pages: one page per language, e.g. / and /ko/) ----
     a[data-lang-link="ko|en"] is a real link to the other page. It keeps the CURRENT #hash: its
     fragment-free base is captured once and the href is rebuilt from location.hash right before any
     activation (pointer, touch, focus, context menu, keyboard, click, middle click) and whenever the hash
     or history entry changes. Following it saves the choice in localStorage "ppk-lang", which the EN
     page's twin-redirect snippet (theme-init.html) reads; "?lang=en" on the EN URL wins over it.
     Click and middle click save on this page; a link opened another way (context menu "open in new tab")
     is saved by the destination: a page that was opened from its twin (same-origin document.referrer whose
     origin + path equals one of its language links) saves its own language. A direct visit, a visit from
     any other page, a reload or a back/forward visit saves nothing; with a no-referrer policy only click
     and middle click save. */
  var langLinks = all('a[data-lang-link]');
  langLinks.forEach(function (a) {
    a.ppkBase = (a.getAttribute('href') || '').split('#')[0];
  });
  function syncLangLinks() {
    var h = window.location.hash;
    langLinks.forEach(function (a) { a.setAttribute('href', a.ppkBase + (h && h !== '#' ? h : '')); });
  }
  function saveLang(a) {
    var next = a.getAttribute('data-lang-link') === 'ko' ? 'ko' : 'en';
    try { window.localStorage.setItem(LANG_KEY, next); } catch (e) { /* storage blocked: the link still works */ }
  }
  langLinks.forEach(function (a) {
    ['mousedown', 'pointerdown', 'touchstart', 'focus', 'contextmenu', 'keydown'].forEach(function (t) {
      a.addEventListener(t, syncLangLinks, t === 'touchstart' ? { passive: true } : false);
    });
    a.addEventListener('click', function () { syncLangLinks(); saveLang(a); });
    a.addEventListener('auxclick', function (e) { syncLangLinks(); if (e.button === 1) saveLang(a); });
  });
  if (langLinks.length) {
    ['hashchange', 'popstate', 'pageshow'].forEach(function (t) { window.addEventListener(t, syncLangLinks); });
    syncLangLinks();
    (function () {
      var ref = doc.referrer;
      var P = window.performance;
      var nav = P && P.getEntriesByType ? P.getEntriesByType('navigation')[0] : null;
      if (!ref || (nav && nav.type !== 'navigate')) return;
      function page(href) {
        var u = doc.createElement('a');
        u.href = href;
        return u.protocol + '//' + u.host + (u.pathname.charAt(0) === '/' ? '' : '/') + u.pathname;
      }
      var from = page(ref);
      var fromTwin = from.indexOf(window.location.protocol + '//' + window.location.host + '/') === 0 &&
        langLinks.some(function (a) { return page(a.ppkBase || window.location.href) === from; });
      if (!fromTwin) return;
      try { window.localStorage.setItem(LANG_KEY, /^ko/i.test(root.lang) ? 'ko' : 'en'); } catch (e) { /* storage blocked */ }
    }());
  }
  /* PPK.setLang: the in-page swap wherever the page has a [data-lang-toggle] (or no language link);
     on a link-only page it follows the link to the requested language, if there is one */
  function apiSetLang(next) {
    if (langLinks.length && !doc.querySelector('[data-lang-toggle]')) {
      var a = doc.querySelector('a[data-lang-link="' + (next === 'ko' ? 'ko' : 'en') + '"]');
      if (a) { syncLangLinks(); a.click(); }
      return;
    }
    setLang(next);
  }

  /* ---- motion control: button[data-motion-toggle] pauses every animation on the page ----
     Adds html.motion-off (base.css: the reduced-motion picture), saves "ppk-motion" = "off" (removed on
     resume; theme-init.html applies it before first paint), and stops the step-flow, the loop and the
     videos through still(). Label: [data-motion-label] inside the button (else the button text, or its
     aria-label when it holds an icon), from data-label-pause / data-label-resume or the kit strings. */
  var motionBtns = all('button[data-motion-toggle]');
  function paintMotion() {
    var off = root.classList.contains('motion-off');
    motionBtns.forEach(function (b) {
      var t = attrLabel(b, off ? 'resume' : 'pause') || str(off ? 'motionResume' : 'motionPause');
      var label = b.querySelector('[data-motion-label]');
      if (label) label.textContent = t;
      else if (!b.children.length) b.textContent = t;
      else b.setAttribute('aria-label', t);
      b.setAttribute('aria-pressed', off ? 'true' : 'false');
    });
  }
  function setMotion(off) {
    root.classList.toggle('motion-off', off);
    try {
      if (off) window.localStorage.setItem(MOTION_KEY, 'off'); else window.localStorage.removeItem(MOTION_KEY);
    } catch (e) { /* storage blocked: the choice lasts for this page view */ }
    paintMotion();
    checkStill();
    emit('ppk:motion');
  }
  motionBtns.forEach(function (b) {
    b.addEventListener('click', function () { setMotion(!root.classList.contains('motion-off')); });
  });
  doc.addEventListener('ppk:lang', paintMotion);
  paintMotion();

  /* ---- copy buttons ---- */
  var globalStatus = null;
  function statusFor(btn) {
    var scope = closest(btn, '[data-copy-scope]') || btn.parentNode;
    var s = scope ? scope.querySelector('[data-copy-status]') : null;
    if (!s) {
      if (!globalStatus) {
        globalStatus = doc.createElement('span');
        globalStatus.className = 'vh';
        globalStatus.setAttribute('role', 'status');
        globalStatus.setAttribute('aria-live', 'polite');
        doc.body.appendChild(globalStatus);
      }
      s = globalStatus;
    }
    return s;
  }
  function copyText(text, done) {
    var settled = false;
    function finish(ok) { if (!settled) { settled = true; done(ok); } }
    function fallback() {
      if (settled) return;
      var ta = doc.createElement('textarea');
      var active = doc.activeElement;
      var ok = false;
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '0';
      ta.style.left = '0';
      ta.style.opacity = '0';
      doc.body.appendChild(ta);
      ta.select();
      try { ok = doc.execCommand('copy'); } catch (e) { ok = false; }
      doc.body.removeChild(ta);
      if (active && active.focus) active.focus();
      finish(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      setTimeout(fallback, 1500);
      navigator.clipboard.writeText(text).then(function () { finish(true); }, fallback);
    } else {
      fallback();
    }
  }
  function onCopy(btn) {
    var sel = btn.getAttribute('data-copy');
    var scope = closest(btn, '[data-copy-scope]');
    var src = sel ? doc.querySelector(sel) : (scope && scope.querySelector('pre, code'));
    var status = statusFor(btn);
    var label = btn.querySelector('[data-copy-label]');
    if (!src) return;
    status.textContent = '';
    copyText(src.textContent.replace(/\s+$/, ''), function (ok) {
      status.textContent = str(ok ? 'copied' : 'copyFail');
      if (label && ok) label.textContent = str('copiedShort');
      clearTimeout(btn.ppkTimer);
      btn.ppkTimer = setTimeout(function () {
        status.textContent = '';
        /* restore the label in the language shown now, never from a snapshot */
        if (label) label.textContent = label.getAttribute('data-' + lang()) || label.getAttribute('data-en') || '';
      }, 3000);
    });
  }

  doc.addEventListener('click', function (e) {
    var b = closest(e.target, '[data-theme-toggle],[data-lang-toggle],[data-copy]');
    if (!b || b.getAttribute('aria-disabled') === 'true') return;
    if (b.hasAttribute('data-theme-toggle')) setTheme(theme() === 'dark' ? 'light' : 'dark', true);
    else if (b.hasAttribute('data-lang-toggle')) setLang(lang() === 'ko' ? 'en' : 'ko');
    else onCopy(b);
  });

  /* ---- reveal on scroll ---- */
  function revealAll() { all('.rv').forEach(function (el) { el.classList.add('is-in'); }); }
  onStill(function (s) { if (s) revealAll(); });
  if (still() || !('IntersectionObserver' in window)) {
    revealAll();
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add('is-in'); io.unobserve(en.target); }
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0 });
    all('.rv').forEach(function (el) { io.observe(el); });
  }

  /* ---- hero drift: pause while the tab is hidden ---- */
  function syncHidden() {
    if (doc.hidden) root.classList.add('is-hidden'); else root.classList.remove('is-hidden');
  }
  doc.addEventListener('visibilitychange', function () { syncHidden(); syncVideos(); });
  syncHidden();
  /* ---- hero drift: also pause while the hero is off screen (.hero.is-off, see components/hero.html) ---- */
  if ('IntersectionObserver' in window) {
    var heroIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { en.target.classList.toggle('is-off', !en.isIntersecting); });
    });
    all('.hero').forEach(function (h) { heroIo.observe(h); });
  }

  /* ---- video[data-autoplay]: plays muted when motion is allowed, paused with controls otherwise ----
     Each video gets a pause/play button right after it (button.video-toggle, styled in base.css; an
     existing .video-toggle sibling is reused). A pause the viewer makes (that button, or the native
     controls) is remembered: a return to the tab never resumes that video. Pausing motion or reduced
     motion pauses every video, and under either the kit never starts playback itself: only the viewer's
     Play does, and a video the kit paused for a hidden tab stays paused on return. */
  var ICON_PAUSE = '<svg class="ico-pause" viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true" focusable="false"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
  var ICON_PLAY = '<svg class="ico-play" viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true" focusable="false"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10-6.5a1 1 0 0 0 0-1.72l-10-6.5A1 1 0 0 0 8 5.5z"/></svg>';
  /* pause() also runs on a paused video: it cancels a pending native autoplay; only a real change fires "pause" */
  function kitPause(v) {
    if (!v.paused) v.ppkKitPause = true;
    v.pause();
  }
  function kitPlay(v) {
    if (!v.paused || !v.play) return;
    v.ppkKitPlay = true;
    var p = v.play();
    /* blocked autoplay: show the native controls; an AbortError only means a pause came first */
    if (p && p.catch) p.catch(function (e) {
      v.ppkKitPlay = false;
      if (!e || e.name !== 'AbortError') v.controls = true;
      paintVideo(v);
    });
  }
  function paintVideo(v) {
    var b = v.ppkToggle;
    if (!b) return;
    b.classList.toggle('is-paused', v.paused);
    b.setAttribute('aria-label', attrLabel(v, v.paused ? 'play' : 'pause') || str(v.paused ? 'videoPlay' : 'videoPause'));
  }
  function setupVideo(v) {
    var b = v.nextElementSibling;
    if (!b || !b.classList.contains('video-toggle')) {
      b = doc.createElement('button');
      b.className = 'btn btn-icon video-toggle';
      b.innerHTML = ICON_PAUSE + ICON_PLAY;
      v.parentNode.insertBefore(b, v.nextSibling);
    }
    b.setAttribute('type', 'button');
    v.ppkToggle = b;
    /* ppkUser: "paused" / "played" by the viewer, or unset; kit-made pause/play events are skipped */
    b.addEventListener('click', function () {
      if (v.paused) { v.ppkUser = 'played'; kitPlay(v); } else { v.ppkUser = 'paused'; kitPause(v); }
      paintVideo(v);
    });
    v.addEventListener('pause', function () {
      if (v.ppkKitPause) v.ppkKitPause = false; else v.ppkUser = 'paused';
      paintVideo(v);
    });
    v.addEventListener('play', function () {
      if (v.ppkKitPlay) v.ppkKitPlay = false; else v.ppkUser = 'played';
      paintVideo(v);
    });
  }
  function syncVideos() {
    all('video[data-autoplay]').forEach(function (v) {
      v.muted = true;
      v.setAttribute('playsinline', '');
      if (!v.ppkToggle) setupVideo(v);
      if (doc.hidden || v.ppkUser === 'paused' || still()) {
        kitPause(v);
        if (reduce.matches) v.controls = true;
      } else {
        kitPlay(v);
      }
      paintVideo(v);
    });
  }
  syncVideos();
  onStill(syncVideos);
  doc.addEventListener('ppk:lang', function () { all('video[data-autoplay]').forEach(paintVideo); });

  /* ---- step-flow ---- */
  function initFlow(el) {
    var nodes = all('[data-flow-node]', el);
    var n = nodes.length;
    var step = 0;
    var timer = null;
    var interval = parseInt(el.getAttribute('data-flow-interval'), 10) || 1900;
    var runBtn = el.querySelector('[data-flow-run]');
    var stepBtn = el.querySelector('[data-flow-step]');
    var resetBtn = el.querySelector('[data-flow-reset]');
    var caption = el.querySelector('[data-flow-caption]');
    var modeBtns = all('[data-flow-select] [data-flow-mode]', el);
    var mode = '';
    modeBtns.forEach(function (b) { if (b.getAttribute('aria-pressed') === 'true') mode = b.getAttribute('data-flow-mode'); });
    if (!mode && modeBtns.length) mode = modeBtns[0].getAttribute('data-flow-mode');

    function text(src, base) {
      var l = lang();
      var keys = ['data-' + base + '-' + l + '-' + mode, 'data-' + base + '-' + l, 'data-' + base + '-en-' + mode, 'data-' + base + '-en'];
      for (var i = 0; i < keys.length; i++) if (src.hasAttribute(keys[i])) return src.getAttribute(keys[i]);
      return '';
    }
    function label(src, key) {
      return (src && src.getAttribute('data-label-' + key + '-' + lang())) || str(key);
    }
    function render(animate) {
      el.setAttribute('data-step', String(step));
      nodes.forEach(function (node, i) {
        var k = i + 1;
        node.classList.toggle('is-active', k === step);
        node.classList.toggle('is-done', k < step);
        node.classList.remove('is-feeding');
        if (k === step) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
        if (animate && !still() && k === step - 1) {
          void node.offsetWidth;
          node.classList.add('is-feeding');
        }
      });
      all('[data-flow-modes]', el).forEach(function (it) {
        var on = (' ' + it.getAttribute('data-flow-modes') + ' ').indexOf(' ' + mode + ' ') > -1;
        var st = it.querySelector('[data-flow-state]');
        it.classList.toggle('is-off', !on);
        var owner = closest(it, '[data-flow-node]');
        var k = owner ? nodes.indexOf(owner) + 1 : n;
        it.classList.toggle('is-on', on && step >= k);
        if (st) st.textContent = label(el, on ? 'on' : 'off');
      });
      if (caption) {
        var t = step === 0 ? text(el, 'cap-idle') : step > n ? text(el, 'cap-done') : text(nodes[step - 1], 'cap');
        caption.textContent = t;
      }
      if (runBtn) {
        var rl = runBtn.querySelector('[data-flow-run-label]') || runBtn;
        rl.textContent = label(runBtn, timer ? 'pause' : 'run');
        runBtn.setAttribute('aria-pressed', timer ? 'true' : 'false');
      }
    }
    function next() { step = step >= n + 1 ? 0 : step + 1; render(true); }
    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      render(false);
    }
    if (runBtn) runBtn.addEventListener('click', function () {
      if (timer) { stop(); return; }
      if (still()) { next(); return; }
      if (step >= n + 1) step = 0;
      timer = setInterval(function () {
        if (still() || step >= n + 1) { stop(); return; }
        next();
        if (step >= n + 1) stop();
      }, interval);
      next();
    });
    if (stepBtn) stepBtn.addEventListener('click', function () {
      if (timer) { clearInterval(timer); timer = null; }
      next();
    });
    if (resetBtn) resetBtn.addEventListener('click', function () {
      if (timer) { clearInterval(timer); timer = null; }
      step = 0;
      render(false);
    });
    modeBtns.forEach(function (b) {
      b.addEventListener('click', function () {
        mode = b.getAttribute('data-flow-mode');
        modeBtns.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        render(false);
      });
    });
    onMedia(reduce, function () { if (reduce.matches && timer) stop(); });
    onStill(function (s) { if (s && timer) stop(); });
    doc.addEventListener('ppk:lang', function () { render(false); });
    render(false);
  }
  all('[data-flow]').forEach(initFlow);

  /* ---- loop: stages in a cycle ([data-loop]) ----
     Nodes [data-loop-node] in order; exactly one is active (.is-active, aria-current="step"), the first
     at start. Run plays one full lap (one stage per data-interval ms, default 1600) and stops back where
     it started; while it plays the button reads Pause and pressing it stops. Step goes to the next stage
     and wraps from the last to the first; Reset goes back to the first. Under still() Run advances one
     stage and a running lap stops at once (also when motion changes at runtime). The caption
     ([data-loop-caption], role=status) reads "Stage n: title — text" from the node's [data-loop-title] and
     [data-loop-text]; data-label-stage may hold "{n}" for the number's place ("{n}단계", the KO default). Where the hub [data-loop-hub] is shown, the hand turns to the active node: --hand
     (deg, on the [data-loop] element) is measured from the hub and node centres, always grows (forward
     round the loop) and takes the shortest way on Reset and resize. The newly active node gets a
     one-shot .is-arrive class (700 ms; CSS animates transform/opacity only). */
  function initLoop(el) {
    var nodes = all('[data-loop-node]', el);
    var n = nodes.length;
    if (!n) return;
    var cur = 0;
    var hand = null;
    var left = 0;
    var timer = null;
    var arrived = null;
    var interval = parseInt(el.getAttribute('data-interval'), 10) || 1600;
    var runBtn = el.querySelector('[data-loop-run]');
    var stepBtn = el.querySelector('[data-loop-step]');
    var resetBtn = el.querySelector('[data-loop-reset]');
    var caption = el.querySelector('[data-loop-caption]');
    var num = el.querySelector('[data-loop-num]');
    var hub = el.querySelector('[data-loop-hub]');
    var aimedAt = -1;

    function part(node, sel) {
      var p = node.querySelector(sel);
      return p ? p.textContent.replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '') : '';
    }
    /* layout centre from the offset chain, which ignores transforms (the active node's lift) */
    function centre(node) {
      var x = node.offsetWidth / 2, y = node.offsetHeight / 2, e = node;
      while (e) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
      return { x: x, y: y };
    }
    /* clockwise angle from the hub centre to the active node centre. When the active stage advanced, it
       is raised by whole turns until it is not behind the previous one (forward round the loop); nearest
       (Reset, resize, language) = the shortest way; the same stage again (Pause, still()) = unchanged */
    function aim(nearest) {
      if (!hub || !hub.offsetParent) { hand = null; aimedAt = -1; return; }
      var moved = cur !== aimedAt;
      if (hand !== null && !moved && !nearest) return;
      aimedAt = cur;
      var h = centre(hub);
      var r = centre(nodes[cur]);
      var a = Math.atan2(r.x - h.x, h.y - r.y) * 180 / Math.PI;
      if (hand === null || nearest || !moved) {
        if (hand !== null) a += Math.round((hand - a) / 360) * 360;
      } else {
        while (a < hand - 0.5) a += 360;
      }
      hand = a;
      el.style.setProperty('--hand', a.toFixed(2) + 'deg');
    }
    function clearArrive() {
      clearTimeout(arrived);
      nodes.forEach(function (node) { node.classList.remove('is-arrive'); });
    }
    function render(animate, nearest) {
      nodes.forEach(function (node, i) {
        node.classList.toggle('is-active', i === cur);
        if (i === cur) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
      });
      clearArrive();
      if (animate && !still()) {
        void nodes[cur].offsetWidth;
        nodes[cur].classList.add('is-arrive');
        arrived = setTimeout(clearArrive, 700);
      }
      el.setAttribute('data-step', String(cur + 1));
      aim(!!nearest);
      if (num) num.textContent = String(cur + 1);
      if (caption) {
        var title = part(nodes[cur], '[data-loop-title]');
        var body = part(nodes[cur], '[data-loop-text]');
        /* stage word: "{n}" marks where the number goes ("{n}단계", "Stage {n}"); without it: "word n" */
        var w = attrLabel(el, 'stage') || str('stage');
        var stage = w.indexOf('{n}') > -1 ? w.replace('{n}', String(cur + 1)) : w + ' ' + (cur + 1);
        var t = stage + ': ' + title + (body ? ' \u2014 ' + body : '');
        if (caption.textContent !== t) caption.textContent = t;
      }
      if (runBtn) {
        var rl = runBtn.querySelector('[data-loop-run-label]') || runBtn;
        rl.textContent = attrLabel(runBtn, timer ? 'pause' : 'run') || attrLabel(el, timer ? 'pause' : 'run') || str(timer ? 'pause' : 'run');
        runBtn.setAttribute('aria-pressed', timer ? 'true' : 'false');
      }
    }
    function next(animate) {
      cur = (cur + 1) % n;
      render(animate);
    }
    function halt() { if (timer) { clearInterval(timer); timer = null; } }
    function stop() { halt(); render(false); }
    if (runBtn) runBtn.addEventListener('click', function () {
      if (timer) { stop(); return; }
      if (still()) { next(false); return; }
      left = n - 1;
      timer = setInterval(function () {
        if (still()) { stop(); return; }
        next(true);
        if (--left <= 0) stop();
      }, interval);
      next(true);
      if (left <= 0) stop();
    });
    if (stepBtn) stepBtn.addEventListener('click', function () { halt(); next(true); });
    if (resetBtn) resetBtn.addEventListener('click', function () {
      halt();
      cur = 0;
      render(false, true);
    });
    onStill(function (s) { if (s) { if (timer) stop(); clearArrive(); } });
    window.addEventListener('resize', function () { aim(true); });
    doc.addEventListener('ppk:lang', function () { render(false, true); });
    render(false);
  }
  all('[data-loop]').forEach(initLoop);

  /* ---- selected table row: [data-row-select="<tableId>"] ----
     A group of buttons with data-row="<key>" and aria-pressed. Selecting one sets .is-cur and
     aria-current="true" on #<tableId> tr[data-row="<key>"] (cleared on every other row) and puts a
     visually hidden marker (span.vh.row-mark, " (selected)" or data-label-selected) in that row's first
     cell, so the state is in text as well as colour. The initial pressed button, if any, selects. */
  function initRowSelect(group) {
    var table = doc.getElementById(group.getAttribute('data-row-select') || '');
    var btns = all('[data-row]', group);
    var key = null;
    function select(k) {
      key = k;
      btns.forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-row') === k ? 'true' : 'false'); });
      if (!table) return;
      all('tr[data-row]', table).forEach(function (tr) {
        var on = tr.getAttribute('data-row') === k;
        var mark = tr.querySelector('.row-mark');
        tr.classList.toggle('is-cur', on);
        if (on) tr.setAttribute('aria-current', 'true'); else tr.removeAttribute('aria-current');
        if (on && tr.cells.length) {
          if (!mark) {
            mark = doc.createElement('span');
            mark.className = 'vh row-mark';
            tr.cells[0].appendChild(mark);
          }
          mark.textContent = ' ' + (attrLabel(group, 'selected') || str('selected'));
        } else if (mark) {
          mark.parentNode.removeChild(mark);
        }
      });
    }
    btns.forEach(function (b) {
      b.addEventListener('click', function () { select(b.getAttribute('data-row')); });
      if (b.getAttribute('aria-pressed') === 'true') key = b.getAttribute('data-row');
    });
    doc.addEventListener('ppk:lang', function () { if (key !== null) select(key); });
    if (key !== null) select(key);
  }
  all('[data-row-select]').forEach(initRowSelect);

  onMedia(reduce, function () {
    if (reduce.matches) revealAll();
    syncVideos();
  });

  window.PPK = {
    setTheme: function (t) { setTheme(t, true); },
    setLang: apiSetLang,
    theme: theme,
    lang: lang
  };
}());
