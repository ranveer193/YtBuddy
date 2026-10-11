/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        accent: '#7c83ff',
        accent2: '#a78bfa',
      }
    },
  },
  plugins: [],
}
