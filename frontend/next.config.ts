import type { NextConfig } from "next";

/**
 * The UI is exported as static files and served by the FastAPI backend
 * (same origin => simple cookies/CSRF, no Node.js needed in production).
 * During `npm run dev`, API calls are proxied to the backend on :8000.
 */
const isDev = process.env.NODE_ENV === "development";
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: isDev ? undefined : "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
    },
  }),
};

export default nextConfig;
