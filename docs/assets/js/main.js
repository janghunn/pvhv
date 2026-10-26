(() => {
  'use strict';

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

  const load = document.getElementById('video-load');
  const facade = document.getElementById('video-facade');
  if (load && facade) {
    load.addEventListener('click', () => {
      const frame = document.createElement('iframe');
      frame.src = 'https://www.youtube-nocookie.com/embed/cnR8iujBu7A?autoplay=1';
      frame.title = 'SIGGRAPH 2023: Potentially Visible Hidden-Volume Rendering for Multi-View Warping';
      frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
      frame.referrerPolicy = 'strict-origin-when-cross-origin';
      frame.allowFullscreen = true;
      frame.loading = 'lazy';
      frame.tabIndex = 0;
      facade.replaceChildren(frame);
      frame.focus();
    });
  }
})();
