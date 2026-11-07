// The owner app's DOM helpers. Every node is built with createElement and text nodes: the
// page's CSP closes the DOM's script sinks (Trusted Types with no policy), so no HTML string
// is ever parsed here.

/** An element with its properties and children; `on<event>` props are listeners. */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) {
      continue;
    }
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (key === "class") {
      el.className = value;
    } else if (["value", "checked", "disabled", "selected"].includes(key)) {
      el[key] = value;
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) {
      continue;
    }
    el.append(child instanceof Node ? child : String(child));
  }
}

/** Replace `el`'s children. */
export function fill(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

/** A time in seconds since the epoch, in the viewer's own words. */
export function when(seconds) {
  if (typeof seconds !== "number") {
    return "";
  }
  return new Date(seconds * 1000).toLocaleString();
}

/** Bytes, in plain units. */
export function bytes(n) {
  if (typeof n !== "number") {
    return "";
  }
  const units = ["bytes", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return unit === 0 ? `${value} bytes` : `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/** A labelled field: the label wraps its input. */
export function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", {}, label), input,
           hint ? h("small", {}, hint) : null);
}

/** A button that runs `act`, disabled while it runs, its failure shown by `report`. */
export function action(text, act, report, props = {}) {
  const button = h("button", { type: "button", ...props }, text);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await act();
    } catch (error) {
      report(error);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

// -- the runner's events, in words ----------------------------------------------------
// The sandbox's runner sends its own events as codes and facts, never the words a person
// reads (the untangle's S69): these are those words, so a wording change ships with the
// app, not a runner. Each event is shown as it always was, its words in their place; any
// other event, or one whose facts are not of their shape (the VM is untrusted), as it came.

/** Why the sync agent skipped or refused a file, by its code. */
const SKIPPED = Object.freeze({
  changed: "changed while syncing; the next sync sends it",
  gone: "gone before it was sent",
  held_back: "held back by a sync-ignore line: your files keep the version they have",
});
const REFUSED = Object.freeze({ secret_bytes: "holds a secret's bytes" });

function whole(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function texts(value) {
  return Array.isArray(value) && value.every((each) => typeof each === "string");
}

/** Bytes as a person reads them, decimal (as storage is sold), as the scratch notice says. */
function scratchBytes(n) {
  const gb = n / 1e9;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;
}

function unappliedWords(event) {
  const { lines, list_bytes_past: past, automaton_states: states, list_bytes: max } = event;
  if (!Array.isArray(lines) || !whole(past) || !whole(states) || !whole(max)) {
    return null;
  }
  const over = lines.filter((each) => each?.reason === "states").length;
  const said = [
    over === 0 ? null : `${over} line${over === 1 ? "" : "s"} past the ${
      states.toLocaleString("en")} automaton states the lines with *, ? or [ ] ` +
      "may take together",
    past === 0 && lines.every((each) => each?.reason !== "bytes") ? null
      : `the list past its ${max.toLocaleString("en")} bytes (${
        past.toLocaleString("en")} more bytes)`,
  ].filter((each) => each !== null);
  return `In /workspace/.ta/sync-ignore, ${said.join(", and ")} ${
    said.length === 1 && over === 1 ? "is" : "are"} not applied, in file ` +
    "order: the files they name go on syncing.";
}

function unnamedWords({ count, paths }) {
  if (!whole(count) || !texts(paths)) {
    return null;
  }
  return `${count} file${count === 1 ? "" : "s"} or folder${
    count === 1 ? "" : "s"} in your sandbox ${count === 1 ? "has a name" :
    "have names"} that ${count === 1 ? "is" : "are"} not UTF-8, so ${
    count === 1 ? "it does" : "they do"} not sync to your files: ${
    paths.slice(0, 5).join(", ")}${count > 5 ? ", …" : ""}. Rename them to sync them.`;
}

function scratchWords({ evicted, expired, freed_bytes, free_bytes, total_bytes }) {
  if (!texts(evicted) || !texts(expired) || !whole(freed_bytes) || !whole(free_bytes)
      || !whole(total_bytes)) {
    return null;
  }
  const why = evicted.length > 0
    ? "The sandbox's working disk was running low"
    : "Some working files had not been used for a long time";
  return `${why}, so old scratch was cleared (${[...evicted, ...expired].join(", ")}; ${
    scratchBytes(freed_bytes)} freed; ${scratchBytes(free_bytes)} of ${
    scratchBytes(total_bytes)} free now). Scratch is a temporary cache: a cleared repository ` +
    "is cloned again when needed. Your storage is unaffected.";
}

function projectEnvWords({ code, path, helper, sets }) {
  if (code !== "project_env_refused" || typeof path !== "string") {
    return null;
  }
  return typeof helper === "string"
    ? `project_env_refused: ${path} names a credential helper (${helper})`
    : typeof sets === "string"
      ? `project_env_refused: ${path} sets ${sets}, which a run's environment never takes ` +
        "from the agent's files"
      : null;
}

/** A runner's event as a person is shown it: its words in their place. */
export function eventShown(event) {
  if (typeof event !== "object" || event === null || Array.isArray(event)) {
    return event;
  }
  const { type } = event;
  if (type === "ta.sync_ignore_unapplied") {
    const message = unappliedWords(event);
    return message === null ? event : { type, lines: event.lines, count: event.count,
                                        list_bytes_past: event.list_bytes_past, message };
  }
  if (type === "ta.sync_unnamed") {
    const message = unnamedWords(event);
    return message === null ? event : { type, paths: event.paths, count: event.count, message };
  }
  if (type === "ta.sync_ignore_slow" && whole(event.match_ms)) {
    return { type, match_ms: event.match_ms, message: `Matching /workspace/.ta/sync-ignore took ${
      Math.round(event.match_ms / 1000)} s of this sync; its lines with * or ? cost the most.` };
  }
  if (type === "ta.scratch_cleared") {
    const text = scratchWords(event);
    return text === null ? event : { type, text };
  }
  if (type === "ta.refused") {
    const reason = projectEnvWords(event);
    return reason === null ? event : { type, reason };
  }
  if (type === "ta.sync_refused" && Object.hasOwn(REFUSED, event.code)) {
    return { type, path: event.path, reason: REFUSED[event.code] };
  }
  if (type === "ta.sync_skipped" && Array.isArray(event.skipped)
      && event.skipped.every((each) => Object.hasOwn(SKIPPED, each?.code))) {
    return { type, files: event.files,
             skipped: event.skipped.map((each) => ({ path: each.path,
                                                    reason: SKIPPED[each.code] })) };
  }
  if (type === "ta.cli_refused" && event.code === "runner_type") {
    return { type, text: event.text,
             reason: "a CLI's line may not carry the runner's own event type (ta.…)" };
  }
  return event;
}
