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
 *   (`Tokens`), so the weight is known before the parse. A `\u` escape counts as
 *   non-ASCII: the parse makes its string two-byte (review V-H1).
 *
 * **When it is taken.** While it reads, a call holds ⌈`TEXT_COST`·read / unit⌉ units of a
 * lease (`SlotPool.lease`, wait-die), one more taken before the bytes that need it are kept,
 * so a slow sender holds only what it sent. Once the body is in, its weight's units
 * (`weightSlots`) are taken before the parse. A call is refused `egress_too_large` as soon as
 * its weight so far passes what one call may hold (the ceiling the costs derive: 15 MiB of a
 * body with a non-ASCII character, 28 MiB of an ASCII one, the egress door's 16 MiB first).
 * Each wait for a unit
 * is the pool's own (`busy` past it, the owners waiting taking turns and sharing the pool
 * under contention), and a holder that cannot grow at once and is not the oldest holder gives
 * everything back, `busy`, before its reserve, so nothing is charged.
 *
 * **How long.** Each unit's worth of body must arrive within `SLOT_WAIT_MS` of that unit's
 * grant (`readHeld`), so no unit is held longer than a waiter waits for one; its clock runs
 * only while the call waits on its sender, never during a wait for a unit. That is the
 * slowest a sender may be, `MIN_RATE` (about 0.18 MiB/s). The reserve may take the pool's
 * wait (`busy` past it), and the upload `heldDeadlineMs` (the pool's wait plus the body at
 * `MIN_RATE`, 100 s for 16 MiB), each counted from its own start. All of it is derived from
 * the pool, not measured: no sandbox's upload rate could be measured without a live sandbox
 * (owed live). The caller's stop ends every phase at once.
 */
import { utf8Decoder } from "../../contract/src/text";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import { SLOT_WAIT_MS } from "../../contract/src/limits";
import {
  type Isolate, isolatePool, isSlotBusy, type Lease, type SlotPool, UNIT_BYTES,
} from "../../contract/src/slots";

/** The gateway's pool: 16 slots of one 4 MiB unit, 64 MiB, beside its 12 MiB idle set. */
export const GATEWAY_POOL_SLOTS = 16;
/**
 * The gateway isolate's one pool, held at module scope: one of the platform's pinned
 * exemptions from the no-module-state rule (`owner/guards/module-state.test.ts`). It holds a
 * count of units in use and the calls waiting, never an owner's data.
 */
const ISOLATE: Isolate = { pool: null, tails: null };

/** The isolate's pool, at the gateway's capacity. */
export function gatewaySlots(): SlotPool {
  return isolatePool(ISOLATE, GATEWAY_POOL_SLOTS);
}

/** The text while it is read, a byte. */
export const TEXT_COST = 2.25;
/** The parse, stringify and encode's most, a byte, for a body with a non-ASCII character. */
export const BYTE_COST = 4.25;
/** The same for a body of ASCII bytes alone. */
export const ASCII_BYTE_COST = 2.25;
/** Each structural token's node in the tree. */
export const TOKEN_COST = 32;
/** The slowest a sender may be while it holds a slot, in bytes a second (see the header). */
export const MIN_RATE = UNIT_BYTES / TEXT_COST / (SLOT_WAIT_MS / 1000);

/** The longest the upload of `length` bytes may take: the pool's wait, and the body. */
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

/**
 * The structural tokens of a JSON text as its bytes stream in: each `{`, `[`, `,` and `:`
 * outside a string, and each string. UTF-8 never puts an ASCII byte inside a multi-byte
 * character, so a byte scan reads the same structure the parse will. `ascii` is whether the
 * parse's strings are all one-byte: every byte ASCII, and no `\u` escape (which may name a
 * character past Latin-1, and Python's `json.dumps` writes one for every non-ASCII
 * character: review V-H1).
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
    let wide = false;
    let high = 0;
    for (let at = 0; at < bytes.length; at++) {
      const byte = bytes[at]!;
      high |= byte;
      if (inString) {
        if (escaped) {
          escaped = false;
          if (byte === 0x75) {
            wide = true;
          }
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
    if (high >= 0x80 || wide) {
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
 * A call's units: a lease of the pool (`SlotPool.lease`, wait-die: a holder that cannot grow
 * at once waits only if it is the oldest holder, any other gives everything back and is
 * `busy`), each wait the pool's own and ended at once by the call's stop.
 */
export class Hold {
  readonly #lease: Lease;
  readonly #most: number;
  readonly #signal: AbortSignal;

  constructor(pool: SlotPool, owner: string, signal: AbortSignal) {
    this.#lease = pool.lease(owner);
    this.#most = pool.most;
    this.#signal = signal;
  }

  get units(): number {
    return this.#lease.units;
  }

  /** The most units one call may hold. */
  get most(): number {
    return this.#most;
  }

  /** At least `units` held, or `egress_too_large`, `busy` (all let go), or the stop's reason. */
  async grow(units: number): Promise<void> {
    if (units > this.#most) {
      throw new Refusal("egress_too_large");
    }
    try {
      await this.#lease.grow(units, this.#signal);
    } catch (error) {
      throw isSlotBusy(error) ? new Refusal("busy") : error;
    }
  }

  release(): void {
    this.#lease.release();
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

/** A promise that rejects with the sender's refusal once `ms` have passed. */
function senderLate(ms: number): { late: Promise<never>; clear: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Refusal("invalid_request")), Math.max(0, ms));
  });
  late.catch(() => undefined);
  return { late, clear: () => clearTimeout(timer) };
}

/**
 * The body's text, its byte length and its tokens: exactly `length` bytes read (a body
 * shorter or longer is `invalid_request`), each chunk decoded as it comes (a streaming fatal
 * `TextDecoder`) and counted (`Tokens`), so no copy of its bytes is gathered; `hold` grown
 * before each chunk is kept, to ⌈`TEXT_COST`·read / unit⌉; refused `egress_too_large` as soon
 * as its weight so far passes what one call may hold (review item 6); stopped by `signal`.
 *
 * **The sender's deadline** (review V-H2, V-M2): each unit's worth of body must arrive
 * within `SLOT_WAIT_MS` of that unit's grant, or the call is `invalid_request`, its units let
 * go. The clock runs only while the call waits on its sender: a wait for a unit is the pool's
 * own (`busy` past it), never counted against the sender.
 */
export async function readHeld(request: Request, length: number, hold: Hold | null,
                               signal: AbortSignal):
    Promise<{ text: string; bytes: number; tokens: Tokens }> {
  const tokens = new Tokens();
  if (request.body === null) {
    if (length !== 0) {
      throw new Refusal("invalid_request");
    }
    return { text: "", bytes: 0, tokens };
  }
  const most = hold?.most ?? Number.POSITIVE_INFINITY;
  const reader = request.body.getReader();
  const decoder = utf8Decoder("strip");
  let text = "";
  let bytes = 0;
  try {
    await hold?.grow(1);
    let granted = Date.now();
    for (;;) {
      const sender = senderLate(granted + SLOT_WAIT_MS - Date.now());
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await raced(Promise.race([reader.read(), sender.late]), signal);
      } finally {
        sender.clear();
      }
      if (chunk.done) {
        break;
      }
      const value = chunk.value;
      bytes += value.byteLength;
      if (bytes > length) {
        throw new Refusal("invalid_request");
      }
      tokens.feed(value);
      if (weightSlots(parseWeight(bytes, tokens.count, tokens.ascii)) > most) {
        throw new Refusal("egress_too_large");
      }
      const units = Math.ceil(TEXT_COST * bytes / UNIT_BYTES);
      if (hold !== null && units > hold.units) {
        await hold.grow(units);
        granted = Date.now();  // the new unit's clock starts at its grant
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    if (bytes !== length) {
      throw new Refusal("invalid_request");
    }
  } catch (error) {
    reader.cancel().catch(() => undefined);
    if (refusalOf(error) !== null || signal.aborted) {
      throw error;
    }
    throw new Refusal("invalid_request");  // not UTF-8, or the body failed
  }
  return { text, bytes, tokens };
}
