/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          950: '#050B14',
          900: '#081220',
          800: '#0E1A2C',
          700: '#14243B',
          600: '#1A2F4C',
          500: '#233E63',
          400: '#345582',
          line: '#384C66',
        },
        titanium: {
          900: '#050B14',
          800: '#0E1A2C',
          700: '#14243B',
          600: '#1A2F4C',
          500: '#304963',
        },
        gold: {
          200: '#FDF1D2',
          300: '#F6DC9C',
          400: '#EAC67A',
          500: '#E2B963',
          600: '#CFA145',
          700: '#B2852E',
        }
      }
    },
  },
  plugins: [],
}
