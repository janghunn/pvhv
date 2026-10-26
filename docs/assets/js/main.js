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
})();
