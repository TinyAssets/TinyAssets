/**
 * A tar writer for `refused.tar`: the names the files plane cannot store, kept verbatim as
 * bytes (a name that is not UTF-8 included), with their files' bytes, folders and links.
 *
 * POSIX ustar headers, and GNU's long-name (`L`) and long-link (`K`) records for a name or
 * link target past the header's 100 bytes, since a long name's bytes may be anything (GNU
 * tar and Python's `tarfile` read both). Deterministic: owner and group 0, no user or group
 * name, the node's own mode and mtime.
 */
import { closeSync, lstatSync, openSync, readdirSync, readlinkSync, readSync, writeFileSync } from "node:fs";

import { CHUNK_BYTES } from "../contract/src/limits";

import { openFile } from "./backup";
import type { Refusal } from "./survey";

const BLOCK = 512;
const LONG_NAME = "././@LongLink";

function octal(value: number, width: number): Buffer {
  return Buffer.from(`${value.toString(8).padStart(width - 1, "0")}\0`, "latin1");
}

/** A member's most bytes: what the header's 11 octal digits can say (8 GiB less a byte). */
export const TAR_MEMBER_MAX = 0o77777777777;

/** One 512-byte header; a member past `TAR_MEMBER_MAX` is refused, never written short. */
function header(name: Buffer, type: string, size: number, mode: number, mtime: number,
                link: Buffer = Buffer.alloc(0)): Buffer {
  if (size > TAR_MEMBER_MAX) {
    throw new Error(`a tar member of ${size} bytes is past the format's ${TAR_MEMBER_MAX}`);
  }
  const block = Buffer.alloc(BLOCK);
  name.copy(block, 0, 0, Math.min(name.length, 100));
  octal(mode & 0o7777, 8).copy(block, 100);
  octal(0, 8).copy(block, 108);
  octal(0, 8).copy(block, 116);
  octal(size, 12).copy(block, 124);
  const seconds = Math.floor(mtime);
  if (seconds < 0) {
    // GNU tar encodes pre-epoch seconds as a signed base-256 field, sign-extended to 12 bytes.
    block.fill(0xff, 136, 148);
    block.writeBigInt64BE(BigInt(seconds), 140);
  } else {
    octal(seconds, 12).copy(block, 136);
  }
  block.fill(0x20, 148, 156);
  block.write(type, 156, "latin1");
  link.copy(block, 157, 0, Math.min(link.length, 100));
  block.write("ustar\0", 257, "latin1");
  block.write("00", 263, "latin1");
  let sum = 0;
  for (const byte of block) {
    sum += byte;
  }
  octal(sum, 7).copy(block, 148);
  block[155] = 0x20;
  return block;
}

function padding(size: number): Buffer {
  return Buffer.alloc((BLOCK - (size % BLOCK)) % BLOCK);
}

/** A GNU long record (`L` a name, `K` a link target) for bytes past 100. */
function long(type: "L" | "K", bytes: Buffer): Buffer[] {
  const data = Buffer.concat([bytes, Buffer.from([0])]);
  return [header(Buffer.from(LONG_NAME, "latin1"), type, data.length, 0o644, 0), data,
          padding(data.length)];
}

/** Encode members on the archive descriptor owned by writeTar. */
class TarWriter {
  readonly #fd: number;

  constructor(fd: number) {
    this.#fd = fd;
  }

  #write(...parts: Buffer[]): void {
    for (const part of parts) {
      writeFileSync(this.#fd, part);
    }
  }

  #head(name: Buffer, type: string, size: number, mode: number, mtime: number,
        link: Buffer = Buffer.alloc(0)): void {
    if (name.length > 100) {
      this.#write(...long("L", name));
    }
    if (link.length > 100) {
      this.#write(...long("K", link));
    }
    this.#write(header(name, type, size, mode, mtime / 1000, link));
  }

  /**
   * The node at `source` as member `name`, and everything below it, never following a link.
   * A socket, FIFO or device is left out.
   */
  add(name: Buffer, source: Buffer): void {
    const stat = lstatSync(source);
    if (stat.isSymbolicLink()) {
      this.#head(name, "2", 0, stat.mode, stat.mtimeMs, readlinkSync(source, { encoding: "buffer" }));
      return;
    }
    if (stat.isDirectory()) {
      this.#head(Buffer.concat([name, Buffer.from("/")]), "5", 0, stat.mode, stat.mtimeMs);
      for (const child of readdirSync(source, { encoding: "buffer" }).sort(Buffer.compare)) {
        this.add(Buffer.concat([name, Buffer.from("/"), child]),
                 Buffer.concat([source, Buffer.from("/"), child]));
      }
      return;
    }
    if (!stat.isFile()) {
      return;
    }
    this.#head(name, "0", stat.size, stat.mode, stat.mtimeMs);
    const fd = openFile(source);
    try {
      const buffer = Buffer.allocUnsafe(CHUNK_BYTES);
      let left = stat.size;
      while (left > 0) {
        const n = readSync(fd, buffer, 0, Math.min(buffer.length, left), null);
        if (n === 0) {
          throw new Error("a refused file shrank while it was read");
        }
        this.#write(buffer.subarray(0, n));
        left -= n;
      }
    } finally {
      closeSync(fd);
    }
    this.#write(padding(stat.size));
  }
}

/** Write a complete archive, releasing its descriptor even when a member fails. */
export function writeTar(at: string, members: readonly Refusal[]): void {
  const fd = openSync(at, "wx", 0o600);
  try {
    const tar = new TarWriter(fd);
    for (const { member, source } of members) {
      tar.add(member, source);
    }
    writeFileSync(fd, Buffer.alloc(2 * BLOCK));
  } finally {
    closeSync(fd);
  }
}
