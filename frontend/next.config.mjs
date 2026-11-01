/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // Dev only: proxy API to a local backend. In production Caddy routes /api and /v1.
  async rewrites() {
    return process.env.SAH_DEV_API ? [{ source: "/api/:path*", destination: `${process.env.SAH_DEV_API}/api/:path*` }] : [];
  },
};
export default nextConfig;
