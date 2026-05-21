/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        base:    '#050505',
        surface: '#0D0D0D',
        raised:  '#111111',
        lift:    '#161616',
        ink: {
          primary:   '#EDEDED',
          secondary: '#8A8A8A',
          muted:     '#4A4A4A',
          dim:       '#242424',
        },
        ok:   '#4ADE80',
        warn: '#FBBF24',
        err:  '#F87171',
        info: '#60A5FA',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        'glow-sm': '0 0 0 1px rgba(255,255,255,0.08), 0 0 8px rgba(255,255,255,0.04)',
        'glow':    '0 0 0 1px rgba(255,255,255,0.14), 0 0 16px rgba(255,255,255,0.06)',
      },
    },
  },
  plugins: [],
}
