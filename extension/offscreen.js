// Offscreen document: plays alert sounds even when the Quotex tab is hidden or has had no user gesture.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.__qxsSound) window.QXSound.play(msg.__qxsSound.kind, msg.__qxsSound.volume);
});
