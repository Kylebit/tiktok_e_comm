/* Historical engineering source binding; never a captured COMPLETE state. */
(() => {
  'use strict';
  const bootstrap = document.currentScript;
  const version = bootstrap.dataset.auditedVersion;
  const state = window.SUPPLY_CHAIN_AUDITED_STATE = {
    version, evidenceGrade: 'AUDITED_MANUAL_SNAPSHOT', status: 'LOADING', executionAuthority: false
  };
  document.documentElement.hidden = true;
  const unavailable = () => {
    state.status = 'UNAVAILABLE';
    const title = document.createElement('h1');
    title.textContent = '历史供应链快照暂不可用';
    const message = document.createElement('p');
    message.textContent = '来源或显示文件未能完整核对，请重新加载；未使用旧数据替代。';
    message.setAttribute('role', 'alert');
    document.body.replaceChildren(title, message);
    document.documentElement.hidden = false;
  };
  (async () => {
    let failed = false;
    let rejectPending;
    const onError = () => { failed = true; if (rejectPending) rejectPending(new Error('initialization failed')); };
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onError);
    try {
      if (!/^[0-9a-f]{64}$/.test(version)) throw new Error('invalid manual version');
      const scripts = JSON.parse(bootstrap.dataset.auditedScripts);
      if (!Array.isArray(scripts) || scripts.length < 4 || scripts.length > 8) throw new Error('invalid scripts');
      for (const item of scripts) {
        if (failed) throw new Error('initialization failed');
        const url = new URL(item.src, location.href);
        if (url.origin !== location.origin || !url.pathname.startsWith('/supply-chain/')) throw new Error('invalid resource');
        await new Promise((resolve, reject) => {
          rejectPending = reject;
          const script = document.createElement('script');
          script.src = url.href;
          if (item.integrity) script.integrity = item.integrity;
          script.onload = () => setTimeout(() => failed ? reject(new Error('initialization failed')) : resolve(), 0);
          script.onerror = reject;
          document.head.appendChild(script);
        });
        rejectPending = undefined;
      }
      if (failed) throw new Error('initialization failed');
      state.status = 'READY_MANUAL_DISPLAY';
      document.documentElement.dataset.auditedVersion = version;
      document.documentElement.hidden = false;
    } finally {
      rejectPending = undefined;
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onError);
    }
  })().catch(unavailable);
})();
