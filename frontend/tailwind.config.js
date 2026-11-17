/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#fbf8f2',
          100: '#f5eee1',
          200: '#eadbc3',
          300: '#dec29f',
          400: '#d0a476',
          500: '#c58b54',
          600: '#b47444',
          700: '#955b38',
          800: '#794a32',
          900: '#633e2c',
          950: '#371f16',
        },
        quest: {
          bg: '#0A0908',
          surface: '#10100E',
          card: '#14110D',
          border: '#302319',
          borderStrong: '#453322',
          gold: '#C5A36A',
          parchment: '#E9D6B6',
          cream: '#F4EBDD',
          muted: '#817463',
          subtle: '#C0B19C',
        }
      },
      fontFamily: {
        serif: ['Cormorant Garamond', 'Instrument Serif', 'Fraunces', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['Geist Mono', 'JetBrains Mono', 'monospace'],
      },
      transitionTimingFunction: {
        pioneer: 'cubic-bezier(0.16, 1, 0.3, 1)',
        'pioneer-soft': 'cubic-bezier(0.25, 1, 0.5, 1)',
      },
      keyframes: {
        pioneerFade: {
          '0%': { opacity: '0', transform: 'scale(0.99)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        pioneerSlideUp: {
          '0%': { opacity: '0', transform: 'translateY(14px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(200%)' },
        },
        goldPulse: {
          '0%, 100%': { boxShadow: '0 0 15px -2px rgba(197, 163, 106, 0.2)' },
          '50%': { boxShadow: '0 0 28px 2px rgba(197, 163, 106, 0.45)' },
        },
      },
      animation: {
        'pioneer-fade': 'pioneerFade 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'pioneer-slide-up': 'pioneerSlideUp 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 2.5s infinite linear',
        'gold-pulse': 'goldPulse 3s ease-in-out infinite',
      },
      boxShadow: {
        'gold-glow': '0 0 20px -2px rgba(197, 163, 106, 0.3)',
        'gold-glow-lg': '0 0 35px -2px rgba(197, 163, 106, 0.5)',
        'card-pioneer': '0 8px 32px -4px rgba(0, 0, 0, 0.5), 0 0 16px -4px rgba(197, 163, 106, 0.15)',
        'card-pioneer-hover': '0 16px 40px -6px rgba(0, 0, 0, 0.6), 0 0 24px -2px rgba(197, 163, 106, 0.35)',
      },
    },
  },
  plugins: [],
}
