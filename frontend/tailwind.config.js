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
    },
  },
  plugins: [],
}

