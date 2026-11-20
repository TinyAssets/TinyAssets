import { SITE } from "../../lib/site";

export const dynamic = "force-static";

export function GET() {
  return new Response(`# ${new URL(SITE.origin).host} — the public site is open to search and AI-grounding crawlers.
#
# RFC 9309: a crawler obeys the MOST SPECIFIC matching group and does NOT fall
# back to "*". So every rule lives in ONE group that names every agent we care
# about plus the wildcard; splitting the named agents into their own groups
# would silently drop the exclusions below for exactly those crawlers.

User-agent: *
User-agent: Googlebot
User-agent: Google-Extended
User-agent: Bingbot
User-agent: OAI-SearchBot
User-agent: ChatGPT-User
User-agent: GPTBot
User-agent: ClaudeBot
User-agent: Claude-Web
User-agent: anthropic-ai
User-agent: PerplexityBot
User-agent: Perplexity-User
Allow: /
Disallow: /account
Disallow: /auth/
Disallow: /editor/
Disallow: /admin/

Sitemap: ${SITE.origin}/sitemap.xml
`, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
