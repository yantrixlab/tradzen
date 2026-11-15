/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0a0e1f', panel: '#111830', panel2: '#0d1328', line: '#1e2747', fg: '#e6e8f2', mut: '#8a93b2',
        acc: '#8b5cf6', acc2: '#6d3fe0', up: '#22c55e', down: '#ef4444', warn: '#f59e0b',
      },
      fontFamily: { sans: ['Inter', 'Segoe UI', 'system-ui', '-apple-system', 'sans-serif'], mono: ['ui-monospace', 'Consolas', 'monospace'] },
    },
  },
  plugins: [],
};
