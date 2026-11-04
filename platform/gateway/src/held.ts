/**
 * A metered call's request, held whole by the gateway while it reads, parses, rewrites and
 * uploads it (egress shape research, A′, and its review, H1 and H2): weighed in slots of the
 * isolate's pool (`slots.ts`) by what it can cost, taken as the bytes arrive, and held to a
 * deadline and to its caller's stop.
 *
 * **What it costs** (measured in workerd, the pinned binary, live set after a swept
 * collection, 4 MiB bodies of 19 shapes: `owner/memory/egress.mjs` asserts it at each phase):
 * - while it reads, its text: at most 2.01 times its bytes (a two-byte string, which one
 *   non-ASCII character anywhere makes the whole text), held at `TEXT_COST` (2.25);
 * - at the parse (text and tree), the stringify (tree and output) and the encode (output and
 *   bytes), the most of the three: 4.00 times the bytes for a body with a non-ASCII
 *   character and few nodes, 2.00 for an ASCII one, plus each node of the tree: up to 30 B a
 *   structural token (`{ [ , :` and each string) for arrays nested two million deep, 17.5 B
 *   for empty objects, 30.4 B for objects of unique keys. So a call weighs
 *   `BYTE_COST` (4.25, or `ASCII_BYTE_COST` 2.25) a byte plus `TOKEN_COST` (32) a token;
 *   the tokens are counted as the bytes stream in, outside strings, by a byte scan
 *   (`Tokens`), so the weight is known before the parse.
 *
 * **When it is taken.** While it reads, a call holds ⌈`TEXT_COST`·read / unit⌉ slots, one
 * more taken before the bytes that need it are kept, so a slow sender holds only what it
 * sent. Once the body is in, its weight's slots (`weightSlots`) are taken before the parse; a
 * call that alone weighs more than the pool is refused `egress_too_large` (the ceiling the
 * costs derive: 15 MiB of a body with a non-ASCII character, 28 MiB of an ASCII one, the
 * egress door's 16 MiB first). Each wait for a slot is the pool's own (`busy` past it), the
 * owners waiting taking turns.
 *
 * **How long.** The phase from its first slot to its upload's last write is held to
 * `heldDeadlineMs`: the pool's own wait plus the body at `MIN_RATE`, the slowest a sender
 * may be while it holds a slot (one slot's worth of body each pool wait, so no unit is held
 * longer than a waiter waits for one: about 0.18 MiB/s, 100 s for 16 MiB). It is derived
 * from the pool, not measured: no sandbox's upload rate could be measured without a live
 * sandbox (owed live). The caller's stop ends the phase too.
 */
import { isolatePool, SLOT_WAIT_MS, type Slot, SlotBusy, type SlotPool, UNIT_BYTES }
  from "../../owner/src/slots";

/** The gateway's pool: 16 slots of one 4 MiB unit, 64 MiB, beside its 12 MiB idle set. */
export const GATEWAY_POOL_SLOTS = 16;
/** The text while it is read, a byte. */
export const TEXT_COST = 2.25;
/** The parse, stringify and encode's most, a byte, for a body with a non-ASCII character. */
export const BYTE_COST = 4.25;
/** The same for a body of ASCII bytes alone. */
export const ASCII_BYTE_COST = 2.25;
/** Each structural token's node in the tree. */
export const TOKEN_COST = 32;
/** The metered call's declared length's bound: the egress door's (`EGRESS_BODY_MAX`). */
export const BODY_MAX = 16 * 1024 * 1024;
/** The slowest a sender may be while it holds a slot, in bytes a second (see the header). */
export const MIN_RATE = UNIT_BYTES / TEXT_COST / (SLOT_WAIT_MS / 1000);

/** The longest a call of `length` bytes may hold its slots: the pool's wait, and the body. */
export function heldDeadlineMs(length: number): number {
  return SLOT_WAIT_MS + Math.ceil(1000 * length / MIN_RATE);
}

/** What a call's parse, stringify and encode can hold at most, in bytes. */
export function parseWeight(bytes: number, tokens: number, ascii: boolean): number {
  return (ascii ? ASCII_BYTE_COST : BYTE_COST) * bytes + TOKEN_COST * tokens;
}

/** The slots `weight` bytes take. */
export function weightSlots(weight: number): number {
  return Math.max(1, Math.ceil(weight / UNIT_BYTES));
}

/** Why a held call stopped, as the gateway answers it. */
export type HeldCode = "busy" | "egress_too_large" | "invalid_request" | "aborted";

export class HeldRefusal extends Error {
  readonly code: HeldCode;

  constructor(code: HeldCode) {
    super(code);
    this.code = code;
  }
}

/**
 * The structural tokens of a JSON text as its bytes stream in: each `{`, `[`, `,` and `:`
 * outside a string, and each string. UTF-8 never puts an ASCII byte inside a multi-byte
 * character, so a byte scan reads the same structure the parse will. `ascii` is whether
 * every byte was.
 */
export class Tokens {
  count = 0;
  ascii = true;
  #inString = false;
  #escaped = false;

  feed(bytes: Uint8Array): void {
    let count = this.count;
    let inString = this.#inString;
    let escaped = this.#escaped;
    let high = 0;
    for (let at = 0; at < bytes.length; at++) {
      const byte = bytes[at]!;
      high |= byte;
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (byte === 0x5c) {
          escaped = true;
        } else if (byte === 0x22) {
          inString = false;
        }
      } else if (byte === 0x22) {
        inString = true;
        count++;
      } else if (byte === 0x7b || byte === 0x5b || byte === 0x2c || byte === 0x3a) {
        count++;
      }
    }
    this.count = count;
    this.#inString = inString;
    this.#escaped = escaped;
    if (high >= 0x80) {
      this.ascii = false;
    }
  }
}

/** `pending`, or the reason `signal` aborted with, whichever comes first. */
export function raced<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) {
    return Promise.reject(signal.reason);
  }
  return new Promise<T>((resolve, reject) => {
    const stop = () => reject(signal.reason);
    signal.addEventListener("abort", stop, { once: true });
    pending.then((value) => {
      signal.removeEventListener("abort", stop);
      resolve(value);
    }, (error: unknown) => {
      signal.removeEventListener("abort", stop);
      reject(error);
    });
  });
}

/**
 * A call's slots: grown as its need grows, each wait the pool's own and the call's stop or
 * deadline, released once, whatever happens (a slot granted after the release is released
 * at once).
 */
export class Hold {
  readonly #pool: SlotPool;
  readonly #owner: string;
  readonly #signal: AbortSignal;
  readonly #slots: Slot[] = [];
  #units = 0;
  #released = false;

  constructor(owner: string, signal: AbortSignal,
              pool: SlotPool = isolatePool(GATEWAY_POOL_SLOTS)) {
    this.#pool = pool;
    this.#owner = owner;
    this.#signal = signal;
  }

  get units(): number {
    return this.#units;
  }

  /** At least `units` slots held, or `egress_too_large`, `busy`, or the stop's reason. */
  async grow(units: number): Promise<void> {
    if (units > this.#pool.capacity) {
      throw new HeldRefusal("egress_too_large");
    }
    while (!this.#released && this.#units < units) {
      const pending = this.#pool.acquire(units - this.#units, this.#owner);
      pending.then((slot) => {
        if (this.#released) {
          slot.release();
        } else {
          this.#slots.push(slot);
          this.#units += slot.units;
        }
      }, () => undefined);
      try {
        await raced(pending, this.#signal);
      } catch (error) {
        throw error instanceof SlotBusy ? new HeldRefusal("busy") : error;
      }
    }
  }

  release(): void {
    this.#released = true;
    for (const slot of this.#slots.splice(0)) {
      slot.release();
    }
    this.#units = 0;
  }
}

/**
 * The body read (`readHeld`) and weighed (`parseWeight`): its text in a holder its caller
 * empties once the text is parsed. A function of its own, so nothing of the read stays in the
 * caller's frame: a dead local of a suspended async function is still kept (measured in
 * workerd: the memory leg's parse case read a third copy while the read's result was a local).
 */
export async function weighedBody(request: Request, length: number, hold: Hold | null,
                                  signal: AbortSignal):
    Promise<{ text: string | null; bytes: number; tokens: number; ascii: boolean;
              weight: number }> {
  const body = await readHeld(request, length, hold, signal);
  return { text: body.text, bytes: body.bytes, tokens: body.tokens.count,
           ascii: body.tokens.ascii,
           weight: parseWeight(body.bytes, body.tokens.count, body.tokens.ascii) };
}

/**
 * The body's text, its byte length and its tokens: exactly `length` bytes read (a body
 * shorter or longer is `invalid_request`), each chunk decoded as it comes (a streaming fatal
 * `TextDecoder`) and counted (`Tokens`), so no copy of its bytes is gathered; `hold` grown
 * before each chunk is kept, to ⌈`TEXT_COST`·read / unit⌉; stopped by `signal`.
 */
export async function readHeld(request: Request, length: number, hold: Hold | null,
                               signal: AbortSignal):
    Promise<{ text: string; bytes: number; tokens: Tokens }> {
  const tokens = new Tokens();
  if (request.body === null) {
    if (length !== 0) {
      throw new HeldRefusal("invalid_request");
    }
    return { text: "", bytes: 0, tokens };
  }
  const reader = request.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false });
  let text = "";
  let bytes = 0;
  try {
    await hold?.grow(1);
    for (;;) {
      const { done, value } = await raced(reader.read(), signal);
      if (done) {
        break;
      }
      bytes += value.byteLength;
      if (bytes > length) {
        throw new HeldRefusal("invalid_request");
      }
      await hold?.grow(Math.ceil(TEXT_COST * bytes / UNIT_BYTES));
      tokens.feed(value);
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    if (bytes !== length) {
      throw new HeldRefusal("invalid_request");
    }
  } catch (error) {
    reader.cancel().catch(() => undefined);
    if (error instanceof HeldRefusal || signal.aborted) {
      throw error;
    }
    throw new HeldRefusal("invalid_request");  // not UTF-8, or the body failed
  }
  return { text, bytes, tokens };
}
