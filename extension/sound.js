/* Tradzen alert sounds - synthesised with Web Audio (no audio files).
 *   call / put      tier A signal: a four-note bell arpeggio, RISING for BUY/CALL, FALLING (minor) for SELL/PUT
 *   callB / putB    tier B signal: short two-note version, softer
 *   win / loss      result cues: bright "coin" triple / soft low pair
 *   test            preview
 */
(() => {
  const F = { C4: 261.63, E4: 329.63, G4: 392.0, A4: 440.0, C5: 523.25, E5: 659.25, F5: 698.46, G5: 783.99, Ab5: 830.61,
              C6: 1046.5, E6: 1318.51 };
  // [note, start offset s, length s, relative gain]
  const PATTERNS = {
    call: [['C5', 0, 0.45, 1], ['E5', 0.11, 0.45, 1], ['G5', 0.22, 0.5, 1], ['C6', 0.34, 0.9, 1.1]],
    put: [['C6', 0, 0.45, 1], ['Ab5', 0.11, 0.45, 1], ['F5', 0.22, 0.5, 1], ['C5', 0.34, 0.9, 1.1]],
    callB: [['E5', 0, 0.3, 0.8], ['G5', 0.1, 0.5, 0.8]],
    putB: [['G5', 0, 0.3, 0.8], ['E5', 0.1, 0.5, 0.8]],
    win: [['G5', 0, 0.25, 0.9], ['C6', 0.08, 0.25, 0.9], ['E6', 0.16, 0.7, 1]],
    loss: [['E4', 0, 0.35, 0.8], ['C4', 0.17, 0.7, 0.8]],
    // trading guard: gwin = target reached (bright, rising), ginfo = soft heads-up, gwarn = attention (repeated mid tone), gstop = stop (falling, urgent)
    gwin: [['C5', 0, 0.3, 0.9], ['E5', 0.1, 0.3, 0.9], ['G5', 0.2, 0.3, 0.9], ['C6', 0.3, 0.9, 1.1]],
    ginfo: [['E5', 0, 0.3, 0.7], ['G5', 0.14, 0.5, 0.7]],
    gwarn: [['A4', 0, 0.22, 1], ['A4', 0.28, 0.22, 1], ['E5', 0.56, 0.6, 1]],
    gstop: [['G5', 0, 0.2, 1.05], ['E5', 0.2, 0.2, 1.05], ['C5', 0.4, 0.2, 1.05], ['G4', 0.6, 0.9, 1.15]],
  };
  PATTERNS.test = PATTERNS.call;
  let ctx = null;

  function bell(ac, out, freq, start, len, gain) {
    // fundamental + soft octave + inharmonic partial = a clean bell/chime timbre
    [[1, 1, 'sine'], [2, 0.28, 'sine'], [2.76, 0.12, 'triangle']].forEach(([mult, rel, type]) => {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = type; o.frequency.value = freq * mult;
      const t0 = ac.currentTime + start;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(gain * rel, t0 + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + len);
      o.connect(g); g.connect(out);
      o.start(t0); o.stop(t0 + len + 0.05);
    });
  }

  function play(kind, volume) {
    const pattern = PATTERNS[kind] || PATTERNS.call;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return false;
    try {
      ctx = ctx || new Ctx();
      if (ctx.state === 'suspended') ctx.resume();
      const master = ctx.createGain();
      master.gain.value = Math.max(0, Math.min(1, volume == null ? 0.7 : volume)) * 0.35;
      master.connect(ctx.destination);
      pattern.forEach(([n, s, l, g]) => bell(ctx, master, F[n], s, l, g));
      return true;
    } catch (_) { return false; }
  }

  window.QXSound = { play, kinds: Object.keys(PATTERNS) };
})();
