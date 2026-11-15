# Tradzen Sniffer (Phase 0 – browser route)

Read-only Chrome extension. It wraps the page's own WebSocket so it can *observe* the frames your logged-in Quotex tab already
receives. It never logs in, clicks, trades or changes frames. Tokens, sessions, balances and emails are stripped before anything is
shown or saved, and URL query strings are dropped.

## Install (2 minutes)
1. Chrome → `chrome://extensions` → turn on **Developer mode** (top right).
2. **Load unpacked** → choose this `extension` folder.
3. Open Quotex in a normal tab and log in yourself as usual. If the tab was already open, **reload it** (the hook must load before the page's socket opens).
4. Open a chart (OTC pair, then a live pair if the market is open). Click the extension icon.

Badge number on the icon = how many assets are streaming ticks.

## What to look at in the popup
- **Sockets**: should show a `wss://…` URL in state `open`.
- **Instruments**: total / OTC / live counts (arrives once after load).
- **Assets streaming**: ticks per minute, last price, age, biggest gap, lag.
- **Events**: the message names Quotex uses.

Click **Save sample JSON** and send me the file (or the Events + Assets tables). Read it first – it's redacted, but you are the judge.

## If nothing shows up
- Sockets empty after reload → tell me the exact domain in the address bar (add it to `matches` in `manifest.json`), or Quotex runs its socket
  in a Web Worker / iframe and I'll switch to hooking that (or the DevTools protocol).
- Sockets open but no assets → send the Events table; the tick format differs from what I assumed.

## Dev
`node test_hook.js` replays fake socket.io frames (text + binary) through `hook.js` and checks parsing and redaction.
