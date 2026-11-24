// A short tone made with the browser's own audio (no sound files). Used for the chart's pattern alerts.
let audio: AudioContext | null = null;

export function tone(seq: [number, number][]): void {
  try {
    audio = audio ?? new AudioContext();
    if (audio.state === 'suspended') void audio.resume();
    let t = audio.currentTime;
    for (const [hz, len] of seq) {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = hz; o.type = 'sine';
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(g).connect(audio.destination); o.start(t); o.stop(t + len + 0.02); t += len + 0.05;
    }
  } catch { /* no audio available (or not allowed before the first tap) */ }
}

export const SOUNDS: Record<'bull' | 'bear' | 'neutral', [number, number][]> = {
  bull: [[784, 0.12], [1046, 0.18]], bear: [[784, 0.12], [587, 0.18]], neutral: [[660, 0.15]],
};
