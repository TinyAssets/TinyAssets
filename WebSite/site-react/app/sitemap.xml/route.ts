import { SITE } from "../../lib/site";

export const dynamic = "force-static";

export function GET() {
  return new Response(`<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>${SITE.origin}/</loc><priority>1.0</priority><changefreq>weekly</changefreq></url>
  <url><loc>${SITE.origin}/start/</loc><priority>0.9</priority><changefreq>monthly</changefreq></url>
  <url><loc>${SITE.origin}/connect/</loc><priority>0.8</priority><changefreq>monthly</changefreq></url>
  <url><loc>${SITE.origin}/build/</loc><priority>0.8</priority><changefreq>monthly</changefreq></url>
  <url><loc>${SITE.origin}/commons/</loc><priority>0.7</priority><changefreq>weekly</changefreq></url>
  <url><loc>${SITE.origin}/developers/</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>
  <url><loc>${SITE.origin}/fine-print/</loc><priority>0.6</priority><changefreq>weekly</changefreq></url>
  <url><loc>${SITE.origin}/legal/</loc><priority>0.5</priority><changefreq>monthly</changefreq></url>
</urlset>
`, { headers: { "Content-Type": "application/xml; charset=utf-8" } });
}
