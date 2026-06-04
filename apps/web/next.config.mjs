/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Next 16 promoted `typedRoutes` out of `experimental`.
  typedRoutes: true,
  // Next 16 added a strict cross-origin check on dev resources
  // (HMR socket, RSC chunks, etc.) that defaults to whatever
  // hostname the dev server bound to. We bind to `localhost`
  // but every demo / README / docs link uses `127.0.0.1:3000`
  // (so curl + browser URLs stay portable across machines).
  // Without these origins explicitly allow-listed, Next 16
  // blocks the HMR socket *and* aborts the client runtime
  // mid-boot, leaving every "use client" component in its
  // server-rendered initial state forever — i.e. the homepage
  // <LiveEvidenceStrip /> stays in its loading skeleton and
  // the goal form never registers an `onChange` handler.
  // See https://nextjs.org/docs/app/api-reference/config/next-config-js/allowedDevOrigins
  allowedDevOrigins: ["127.0.0.1", "127.0.0.1:3000", "localhost", "localhost:3000"],
};

export default nextConfig;
