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
