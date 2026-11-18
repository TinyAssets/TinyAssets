/** The UTF-8, unencoded ListObjectsV2 pages the window requests from R2. Only the
 * S3 element form is accepted. Unexpected markup stops the window before cleanup. */
type Tag = { name: string; text: string; children: Tag[] };

function invalid(): never {
  throw new Error("incomplete or invalid S3 object listing");
}

const entities: Readonly<Record<string, string>> = {
  amp: "&", lt: "<", gt: ">", apos: "'", quot: '"',
};

function decoded(raw: string): string {
  if (/\]\]>/.test(raw)) return invalid();
  return raw.replace(/&(?:([a-z]+);|#(\d+);|#x([a-fA-F0-9]+);)?/g, (_, name, decimal, hex) => {
    if (name !== undefined && Object.hasOwn(entities, name)) return entities[name]!;
    const n = decimal !== undefined ? Number(decimal) : parseInt(hex, 16);
    if (n === 9 || n === 10 || n === 13 || (n >= 32 && n <= 0xd7ff)
        || (n >= 0xe000 && n <= 0xfffd) || (n >= 0x10000 && n <= 0x10ffff)) {
      return String.fromCodePoint(n);
    }
    return invalid();
  });
}

/** The root may declare the S3 namespace. The fields and object metadata have no attributes. */
function envelope(xml: string): Tag {
  const declaration = /^<\?xml +version=(["'])1\.0\1(?: +encoding=(["'])[Uu][Tt][Ff]-8\2)?(?: +standalone=(["'])(?:yes|no)\3)? *\?>/;
  const body = xml.replace(/\r\n?/g, "\n").replace(declaration, "");
  const header = /^[ \t\n]*<ListBucketResult(?:[ \t\n]+xmlns=(["'])http:\/\/s3\.amazonaws\.com\/doc\/2006-03-01\/\1)?[ \t\n]*>/.exec(body);
  if (!header || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/u.test(body)) return invalid();
  const root: Tag = { name: "ListBucketResult", text: "", children: [] };
  const stack = [root];
  const tokens = /([^<]+)|<(\/)?([A-Za-z][A-Za-z0-9]*)[ \t\n]*(\/?)>/gy;
  tokens.lastIndex = header[0].length;
  while (stack.length) {
    const token = tokens.exec(body);
    if (!token) return invalid();
    const parent = stack[stack.length - 1]!;
    if (token[1] !== undefined) {
      parent.text += decoded(token[1]);
    } else if (token[2]) {
      if (token[3] !== parent.name || token[4]) return invalid();
      if (parent.children.length && /[^ \t\n]/.test(parent.text)) return invalid();
      stack.pop();
    } else {
      // Root, Contents, Owner/RestoreStatus, and their scalar fields.
      if (stack.length > 3) return invalid();
      const child: Tag = { name: token[3]!, text: "", children: [] };
      parent.children.push(child);
      if (!token[4]) stack.push(child);
    }
  }
  if (/[^ \t\n]/.test(body.slice(tokens.lastIndex))) return invalid();
  return root;
}

function field(parent: Tag, name: string, optional = false): string | null {
  const found = parent.children.filter((tag) => tag.name === name);
  if (optional && !found.length) return null;
  if (found.length !== 1 || found[0]!.children.length) return invalid();
  return found[0]!.text;
}

export function listPage(xml: string, bucket: string, prefix: string, cursor: string | null,
                         limit: number): { keys: string[]; next: string | null } {
  const root = envelope(xml);
  const entries = root.children.filter((tag) => tag.name === "Contents");
  const keys = entries.map((entry) => field(entry, "Key")!);
  const max = field(root, "MaxKeys", true);
  const echoed = field(root, "ContinuationToken", true);
  const truncated = field(root, "IsTruncated");
  const next = field(root, "NextContinuationToken", true);
  const expected = ["Name", "Prefix", "KeyCount", "MaxKeys", "IsTruncated", "ContinuationToken",
                    "NextContinuationToken", "Contents"];
  if (field(root, "Name") !== bucket || field(root, "Prefix") !== prefix
      || field(root, "KeyCount") !== String(keys.length) || keys.length > limit
      || (max !== null && max !== String(limit)) || (echoed !== null && echoed !== cursor)
      || root.children.some((tag) => !expected.includes(tag.name))
      || keys.some((key) => !key || !key.startsWith(prefix))) return invalid();
  if (truncated === "false" && next === null) return { keys, next };
  if (truncated !== "true" || !next) return invalid();
  return { keys, next };
}
