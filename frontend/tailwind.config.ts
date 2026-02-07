import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        phoenix: {
          50: "#fef3e2",
          100: "#fde4b8",
          200: "#fcd48a",
          300: "#fbc35c",
          400: "#fab738",
          500: "#f9ab14",
          600: "#f59e0b",
          700: "#d97706",
          800: "#b45309",
          900: "#92400e",
        },
      },
    },
  },
  plugins: [],
};
export default config;
