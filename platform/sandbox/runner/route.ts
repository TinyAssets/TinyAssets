/**
 * The route probe's verdict (`ta-runner route-probe`, at every template build;
 * codexurl_research.md Q4): what a keyed engine's CLI did, run as a run runs it with its
 * egress door on a loopback capture server, judged here. Pure: no process, no socket.
 *
 * It passes only when the CLI's first model request reached the door's own path for its run
 * and connection, carrying the run's placeholder key and its run secret where the record puts
 * it; no request carried any byte of the fake sign-in seeded in the engine's store; and the
 * CLI connected to nothing but loopback and the pinned resolver.
 */

/** One request the capture server took. */
export interface Captured {
  readonly method: string;
  /** The request's path and query, as sent. */
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
}

/** What one probe run is judged by. */
export interface RouteExpect {
  /** The door's base path for this run and connection (no origin), its slashes trimmed. */
  readonly prefix: string;
  /** The run's MCP door's paths, which are not model requests. */
  readonly mcp: readonly string[];
  readonly placeholder: string;
  readonly secret: string;
  /** Whether the record sends the run secret as the `x-ta-run` header, not in the path. */
  readonly secretInHeader: boolean;
  /** The fake sign-in's marker, which no request may carry. */
  readonly marker: string;
  /** The resolvers a CLI may reach besides loopback. */
  readonly resolvers: readonly string[];
}

/** Whether `path` is `prefix` or under it. */
export function underPrefix(path: string, prefix: string): boolean {
  const bare = prefix.replace(/\/+$/, "");
  const only = path.split("?")[0]!;
  return only === bare || only.startsWith(`${bare}/`);
}

function loopback(address: string): boolean {
  return /^127\./.test(address) || address === "::1" || /^::ffff:127\./.test(address);
}

/**
 * The connects an strace log (`strace -f -e trace=connect`) shows to anything but loopback
 * and the resolvers, as `address:port`: an IPv4 or IPv6 socket's, a Unix socket's never.
 */
export function outsideConnects(trace: string, resolvers: readonly string[]): string[] {
  const out: string[] = [];
  for (const line of trace.split("\n")) {
    if (!line.includes("connect(")) {
      continue;
    }
    const v4 = /sa_family=AF_INET,\s*sin_port=htons\((\d+)\),\s*sin_addr=inet_addr\("([^"]+)"\)/
      .exec(line);
    const v6 = /sa_family=AF_INET6,\s*sin6_port=htons\((\d+)\).*?inet_pton\(AF_INET6,\s*"([^"]+)"/
      .exec(line);
    const found = v4 ?? v6;
    if (found === null) {
      continue;
    }
    const [, port, address] = found;
    if (loopback(address!) || resolvers.includes(address!)) {
      continue;
    }
    out.push(`${address}:${port}`);
  }
  return [...new Set(out)];
}

/** Why the probe run failed, in plain words, or null when it passed. */
export function routeVerdict(requests: readonly Captured[], connects: readonly string[],
                             expect: RouteExpect): string | null {
  const leaked = requests.find((request) => [request.url, request.body,
                                             ...Object.values(request.headers)]
    .some((text) => text.includes(expect.marker)));
  if (leaked !== undefined) {
    return `a request carried the fake sign-in's bytes (${leaked.method} ${leaked.url})`;
  }
  if (connects.length > 0) {
    return `the CLI connected outside the sandbox: ${connects.join(", ")}`;
  }
  const model = requests.filter((request) =>
    !expect.mcp.some((path) => underPrefix(request.url, path)));
  const first = model[0];
  if (first === undefined) {
    return "no model request reached the door";
  }
  if (!underPrefix(first.url, expect.prefix)) {
    return `the first model request went to ${first.url}, not under ${expect.prefix}`;
  }
  if (![first.url, ...Object.values(first.headers)].some((text) =>
    text.includes(expect.placeholder))) {
    return "the first model request carried no placeholder key";
  }
  if (expect.secretInHeader ? first.headers["x-ta-run"] !== expect.secret
    : !first.url.includes(`/${expect.secret}/`)) {
    return "the first model request carried no run secret where the record puts it";
  }
  return null;
}
