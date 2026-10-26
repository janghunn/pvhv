// Entry point of `node --test demo/tests/`: Node 22 resolves the directory argument as a
// module (this file) instead of scanning it, so the test files are loaded from here.
'use strict';
Promise.all([import('./math.test.mjs'), import('./static.test.mjs')]).catch(error => {
  process.exitCode = 1;
  throw error;
});
