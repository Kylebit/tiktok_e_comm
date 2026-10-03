/* Same-origin, generation-pinned bootstrap; legacy HTML does not reference it. */
(() => {
  'use strict';
  const bootstrap = document.currentScript;
  const version = bootstrap.dataset.capturedVersion;
  const state = window.SUPPLY_CHAIN_CAPTURE_STATE = {version, status: 'LOADING'};
  document.documentElement.hidden = true;
  const unavailable = () => {
    state.status = 'UNAVAILABLE';
    const title = document.createElement('h1');
    title.textContent = '供应链数据正在刷新或暂不可用';
    const message = document.createElement('p');
    message.textContent = '请重新加载页面；未显示不完整数据。';
    message.setAttribute('role', 'alert');
    document.body.replaceChildren(title, message);
    document.documentElement.hidden = false;
  };
  (async () => {
    let failed = false;
    let rejectPending;
    const initializationError = () => {
      failed = true;
      if (rejectPending) rejectPending(new Error('initialization failed'));
    };
    // Observe only our initialization window; keep normal browser error reporting.
    window.addEventListener('error', initializationError);
    window.addEventListener('unhandledrejection', initializationError);
    try {
    if (!/^[0-9a-f]{64}$/.test(version)) throw new Error('invalid generation');
    const scripts = JSON.parse(bootstrap.dataset.capturedScripts);
    for (const item of scripts) {
      if (failed) throw new Error('initialization failed');
      const url = new URL(item.src, location.href);
      if (url.origin !== location.origin || !url.pathname.startsWith('/supply-chain/')) throw new Error('invalid resource');
      await new Promise((resolve, reject) => {
        rejectPending = reject;
        const script = document.createElement('script');
        script.src = url.href;
        if (item.integrity) script.integrity = item.integrity;
        // A load event can follow a runtime exception. Allow initialization's
        // microtask rejection reporting before advancing to the next consumer.
        script.onload = () => setTimeout(() => failed ? reject(new Error('initialization failed')) : resolve(), 0);
        script.onerror = reject;
        document.head.appendChild(script);
      });
      rejectPending = undefined;
    }
    if (failed) throw new Error('initialization failed');
    state.status = 'COMPLETE';
    document.documentElement.dataset.capturedVersion = version;
    document.documentElement.hidden = false;
    } finally {
      rejectPending = undefined;
      window.removeEventListener('error', initializationError);
      window.removeEventListener('unhandledrejection', initializationError);
    }
  })().catch(unavailable);
})();
