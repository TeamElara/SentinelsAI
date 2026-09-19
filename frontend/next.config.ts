import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // The value is configured in Vercel and is used only by the server-side
    // proxy; browser code calls the same-origin `/api` path instead.
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
