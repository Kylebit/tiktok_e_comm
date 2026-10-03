/* Local asset failure feedback; compatible with strict script-src 'self'. */
(() => {
  'use strict';
  window.supplyChainLoadFailed = function () {
    const banner = document.getElementById('supplyLoadError');
    if (banner) banner.hidden = false;
  };
  window.addEventListener('error', function (event) {
    const asset = event.target;
    if ((asset && asset.tagName === 'SCRIPT' && asset.hasAttribute('data-supply-asset')) ||
        (event.error && event.filename && new URL(event.filename, location.href).pathname.startsWith('/supply-chain/'))) {
      window.supplyChainLoadFailed();
    }
  }, true);
  document.addEventListener('DOMContentLoaded', () => {
    const retry = document.getElementById('retrySupplyLoad');
    if (retry) retry.addEventListener('click', () => location.reload());
  }, { once: true });
})();
