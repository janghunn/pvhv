(() => {
  'use strict';

  // Phone navigation: without this script the Menu button stays hidden and the section links stay visible.
  const menu = document.getElementById('menu-toggle');
  const nav = document.getElementById('section-nav');
  if (menu && nav && window.matchMedia) {
    const mobile = window.matchMedia('(max-width: 819px)');
    const setOpen = (open, returnFocus = false) => {
      menu.setAttribute('aria-expanded', String(open));
      nav.hidden = mobile.matches && !open;
      if (returnFocus) menu.focus({ preventScroll: true });
    };
    const layout = () => {
      const linkFocused = nav.contains(document.activeElement);
      menu.hidden = !mobile.matches;
      setOpen(false, mobile.matches && linkFocused);
    };
    menu.addEventListener('click', () => setOpen(menu.getAttribute('aria-expanded') !== 'true'));
    menu.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' && mobile.matches) {
        event.preventDefault();
        setOpen(true);
        nav.querySelector('a')?.focus();
      }
    });
    nav.addEventListener('keydown', event => {
      if (!mobile.matches || !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      const links = [...nav.querySelectorAll('a')];
      const index = links.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      links[(index + (event.key === 'ArrowDown' ? 1 : links.length - 1)) % links.length].focus();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') {
        event.preventDefault();
        setOpen(false, true);
      }
    });
    // A chosen section closes the menu; the link's own navigation still scrolls to it and moves focus there.
    nav.addEventListener('click', event => {
      if (mobile.matches && event.target.closest('a')) setOpen(false);
    });
    mobile.addEventListener('change', layout);
    layout();
  }

  const copy = document.getElementById('copy');
  const citation = document.getElementById('citation');
  const status = document.getElementById('copy-status');
  if (copy && citation && status) {
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(citation.textContent);
        status.textContent = 'BibTeX copied.';
      } catch {
        citation.focus();
        const selection = window.getSelection();
        if (selection) {
          const range = document.createRange();
          range.selectNodeContents(citation);
          selection.removeAllRanges();
          selection.addRange(range);
        }
        status.textContent = 'BibTeX selected. Press Ctrl+C or Command+C to copy.';
      }
    });
  }
})();
