// ISOLATED-world relay: page (hook.js) -> extension background, and popup "reset" -> page.
window.addEventListener('message', (e) => {
  if (e.source !== window || !e.data) return;
  try {
    if (e.data.__qxs === 1) chrome.runtime.sendMessage({ __qxs: 1, snap: e.data.snap });
    else if (e.data.__qxs === 2) chrome.runtime.sendMessage({ __qxs: 2, items: e.data.items });
    else if (e.data.__qxsPing) window.postMessage({ __qxsReady: 1 }, '*');
  } catch (_) { /* extension reloaded */ }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.__qxsCmd === 'reset') window.postMessage({ __qxsCmd: 'reset' }, '*');
});

window.postMessage({ __qxsReady: 1 }, '*');     // tell hook.js the relay is listening (it holds data until then)
