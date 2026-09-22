(() => {
  if (window.__flowKitInjected) return;
  window.__flowKitInjected = true;

  /**
   * Injected into the page's MAIN world on flow.google.com (and an old pinned
   * labs.google tab) — has access to window.grecaptcha.
   *
   * The reCAPTCHA site key survived the September 2026 migration unchanged. The
   * TRPC fetch intercept below did not: it belongs to the labs.google frontend
   * and is inert on flow.google.com, where media urls come back inline on the
   * generate call and from the media rpc.
   */
  const SITE_KEY = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';

// ─── TRPC Response Monitor ─────────────────────────────────
// Monkey-patch fetch to intercept TRPC responses containing media URLs.
// Fresh signed GCS URLs are extracted and forwarded to the agent.

const _originalFetch = window.fetch;
window.fetch = async function (...args) {
  const response = await _originalFetch.apply(this, args);
  try {
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
    // Only intercept TRPC calls on labs.google that return project/flow data
    if (url.includes('/fx/api/trpc/') && response.ok) {
      const clone = response.clone();
      clone.text().then(text => {
        if (text.includes('storage.googleapis.com/ai-sandbox-videofx/')) {
          window.dispatchEvent(new CustomEvent('TRPC_MEDIA_URLS', {
            detail: { url, body: text },
          }));
        }
      }).catch(() => {});
    }
  } catch {}
  return response;
};


  const CAPTCHA_MINT_TAIL = '__flowKitCaptchaMintTail';

  async function mintCaptcha(pageAction) {
    const previous = (
      globalThis[CAPTCHA_MINT_TAIL] instanceof Promise
        ? globalThis[CAPTCHA_MINT_TAIL]
        : Promise.resolve()
    ).catch(() => {});
    let release;
    globalThis[CAPTCHA_MINT_TAIL] = new Promise((resolve) => { release = resolve; });
    await previous;
  try {
    await waitForGrecaptcha();
    return await window.grecaptcha.enterprise.execute(SITE_KEY, {
      action: pageAction,
    });
  } finally {
    release();
  }
}

window.addEventListener('GET_CAPTCHA', async ({ detail }) => {
  const { requestId, pageAction } = detail;
  try {
    const token = await mintCaptcha(pageAction);
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, token },
    }));
  } catch (e) {
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, error: e.message },
    }));
  }
});

function ensureGrecaptchaScript() {
  if (window.grecaptcha?.enterprise?.execute) return;
  const existing = document.querySelector('script[src*="recaptcha/enterprise.js"]');
  if (!existing) {
    const s = document.createElement('script');
    s.src = `https://www.google.com/recaptcha/enterprise.js?render=${SITE_KEY}`;
    s.async = true;
    (document.head || document.documentElement).appendChild(s);
  }
}

function waitForGrecaptcha(timeout = 22000) {   // it loads lazily; 10s was optimistic
  return new Promise((resolve, reject) => {
    ensureGrecaptchaScript();
    const start = Date.now();
    const check = () => {
      if (window.grecaptcha?.enterprise?.execute) {
        if (typeof window.grecaptcha.enterprise.ready === 'function') {
          window.grecaptcha.enterprise.ready(() => resolve());
          return;
        }
        return resolve();
      }
      if (Date.now() - start > timeout) return reject(new Error('grecaptcha not available'));
      setTimeout(check, 150);
    };
    check();
  });
}
})();
