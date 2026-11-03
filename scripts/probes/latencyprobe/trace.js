/** The colo and location the calling code runs in, from Cloudflare's trace page. */
export async function traceColo() {
  try {
    const response = await fetch("https://www.cloudflare.com/cdn-cgi/trace",
                                 { headers: { "cache-control": "no-store" } });
    const text = await response.text();
    const field = (name) => new RegExp(`^${name}=(.*)$`, "m").exec(text)?.[1] ?? null;
    return { colo: field("colo"), loc: field("loc") };
  } catch (error) {
    return { colo: null, error: String(error).slice(0, 200) };
  }
}
