(() => {
  'use strict';

  const nav = document.getElementById('section-nav');
  if (nav && window.matchMedia) {
    const mobile = window.matchMedia('(max-width: 819px)');
    nav.addEventListener('keydown', event => {
      if (!mobile.matches || !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      const links = [...nav.querySelectorAll('a')];
      const index = links.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      links[(index + (event.key === 'ArrowDown' ? 1 : links.length - 1)) % links.length].focus();
    });
    // kit.js scrolls same-document menu links itself and replaces the history entry, so Back would leave the page.
    // Keep native navigation instead: close the menu here, stop the kit handler and let the browser follow the link.
    document.addEventListener('click', event => {
      const link = event.target.closest && event.target.closest('#section-nav a');
      if (!mobile.matches || !link || !link.hash || link.origin !== location.origin || link.pathname !== location.pathname ||
          link.search !== location.search || !document.getElementById(decodeURIComponent(link.hash.slice(1)))) return;
      event.stopPropagation();
      const menu = document.getElementById('menu-toggle');
      if (menu) menu.setAttribute('aria-expanded', 'false');
      nav.hidden = true;
    }, true);
  }

  const strings = {
    en: { zoom: 'Zoom to read labels', fit: 'Fit image to width', region: 'Enlarged image, scroll sideways' },
    ko: { zoom: '라벨을 읽도록 확대', fit: '이미지를 너비에 맞춤', region: '확대한 이미지, 옆으로 스크롤' }
  };
  const lang = () => document.documentElement.lang === 'ko' ? 'ko' : 'en';
  const zoomDesktop = window.matchMedia('(min-width: 860px)');
  const zooms = [];
  document.querySelectorAll('[data-zoom]').forEach(button => {
    const frame = button.previousElementSibling;
    const image = frame.querySelector('img');
    const label = () => {
      const zoomed = button.getAttribute('aria-pressed') === 'true';
      button.textContent = strings[lang()][zoomed ? 'fit' : 'zoom'];
      if (zoomed) frame.setAttribute('aria-label', strings[lang()].region);
    };
    const setZoom = zoomed => {
      button.setAttribute('aria-pressed', String(zoomed));
      frame.classList.toggle('is-zoomed', zoomed);
      image.style.width = zoomed ? button.dataset.zoomWidth + 'px' : '';
      if (zoomed) {
        frame.setAttribute('tabindex', '0');
        frame.setAttribute('role', 'region');
      } else {
        for (const name of ['tabindex', 'role', 'aria-label']) frame.removeAttribute(name);
        frame.scrollLeft = 0;
      }
      label();
    };
    button.hidden = false;
    button.addEventListener('click', () => setZoom(button.getAttribute('aria-pressed') !== 'true'));
    zoomDesktop.addEventListener('change', () => { if (zoomDesktop.matches) setZoom(false); });
    zooms.push(label);
  });

  const translate = () => {
    zooms.forEach(label => label());
    document.querySelectorAll('img[data-ko-alt]').forEach(image => {
      if (!image.hasAttribute('data-en-alt')) image.dataset.enAlt = image.alt;
      image.alt = image.dataset[lang() === 'ko' ? 'koAlt' : 'enAlt'];
    });
  };
  document.addEventListener('ws:lang', translate);
  translate();
})();
