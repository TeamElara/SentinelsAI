import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // NEXT_PUBLIC_API_BASE is set in Vercel to the Render API's origin (e.g.
    // https://sentinels-api.onrender.com). This rewrite is what makes
    // API_BASE = "/api" in lib/api.ts actually reach that backend — the
    // browser only ever talks to this same origin, and Next's server relays
    // the request (cookies included) to Render behind the scenes.
    const backendOrigin = process.env.NEXT_PUBLIC_API_BASE;
    if (!backendOrigin || backendOrigin.startsWith("/")) return [];

    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin.replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};

export default nextConfig;
