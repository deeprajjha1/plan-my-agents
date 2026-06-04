import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f8fafc",
          100: "#eef2f7",
          200: "#dde3ec",
          400: "#7d8aa3",
          600: "#445069",
          800: "#1f2433",
          900: "#0f111c",
        },
        accent: {
          50: "#eff5ff",
          100: "#dbe6ff",
          300: "#93b4fd",
          500: "#2563eb",
          600: "#1d4ed8",
        },
        success: {
          500: "#16a34a",
        },
        warn: {
          500: "#d97706",
        },
        danger: {
          500: "#dc2626",
        },
      },
    },
  },
  plugins: [],
};

export default config;
