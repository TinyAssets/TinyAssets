// The owner's app: a thin surface over the doors' owner routes
// (`/app/api/*`). It builds no feature editor (Direction 3): an agent is its files, which
// the owner reads and writes here as text, and everything else is one door's answer shown
// in plain words. Every request goes to this origin only.
import {
  call, follow, randomText, Refused, RESOURCE, signIn, signOut, signedIn, whenSignedOut,
} from "/app/static/api.js";
import { action, bytes, confirmed, eventShown, field, fill, h, when } from "/app/static/view.js";

/**
 * What the doors answer at an owner-surface path, as they type it (`call`).
 * @template {import("../src/app").AppPath} P
 * @typedef {import("../src/app").AppAnswer<P>} AppAnswer
 */
/**
 * A run as the doors answer it: started, read or stopped.
 * @typedef {AppAnswer<"agent/submit"> | AppAnswer<"agent/status"> | AppAnswer<"agent/cancel">}
 *   Run
 */

const main = document.getElementById("main");
const nav = document.getElementById("nav");
const who = document.getElementById("who");
const notice = document.getElementById("notice");

const SECTIONS = Object.freeze([
  ["home", "Home"], ["agents", "Agents"], ["history", "Past runs"], ["rooms", "Rooms and board"],
  ["sources", "Model sources"], ["apps", "Connected apps"], ["keys", "API keys"],
  ["data", "Your data"],
]);

/**
 * The doors this app knows are not on the platform yet: only these say
 * "not here yet", shown before anything is clicked. Any other missing door is a bug, and its
 * refusal is shown as it came. Each entry goes when its door lands.
 */
const MISSING = Object.freeze({
  export: "Exporting everything you hold is not here yet: its door is being built.",
});

/** What the Talk section holds: this tab's conversation; the rest is under Past runs. */
const TALK_NOTE = "Only this tab's conversation is shown, and it is not kept across reloads: " +
  "your past runs are under Past runs. Each engine's own transcript is kept in your files.";

/** The credential of a subscription signed in inside the sandbox (its record's switch). */
const SIGNIN_CREDENTIAL = "vendor_login_in_sandbox";

const SESSION_NEEDED = "interactive owner session required";

/**
 * Why a sandbox start (a run's or a sign-in's) did not start, in plain words, by its code
 * (`START_CODES`, `contract/src/sandbox.ts`). Any other code is shown as it came.
 */
const NOT_STARTED = Object.freeze({
  sandbox_waiting: "every sandbox TinyAssets may run is in use just now; one frees soon",
  signin_required: "nothing is signed in to its engine inside the sandbox: sign in under " +
    "Model sources",
  sandbox_unconfigured: "TinyAssets cannot start sandboxes yet: its operator has not set " +
    "them up, and nothing you change here fixes it",
  sandbox_lost: "the sandbox it was to join had already gone: start it again",
});

/** A code in plain words: a start code's, else the code as it came. */
function inWords(code) {
  return NOT_STARTED[code] ?? code;
}

/**
 * Why a run stopped short, in plain words: the run's own error, else its code's.
 * @param {Run} run
 */
function runWhy(run) {
  return run.error ?? (run.code ? inWords(run.code) : null);
}

// -- Messages -------------------------------------------------------------------------

/** An answer in the notice, held in view until the next screen or the owner puts it away. */
function say(text, kind = "good", ...extra) {
  fill(notice, h("div", { class: `message ${kind}` }, h("span", {}, text), ...extra,
                 h("button", { type: "button", class: "quiet", onclick: () => fill(notice) },
                   "Dismiss")));
}

/**
 * Each render's turn, and the place it is for. A screen starts in the turn of the render
 * that drew it, and paints only while that turn is the latest and the owner is still at its
 * place, its section and its argument: a slower earlier screen, one a late event redraws (a
 * sign-in's end redrawing Model sources, a save re-listing its folder), or one still loading
 * when the owner signs out never replaces what the owner now sees. A screen past its turn
 * stops at its paint (`STALE`), said nowhere.
 */
const RENDER = { turn: 0 };
const STALE = new Error("a screen past its turn");

/** The turn a screen for `section` at `arg` starts in: the latest, while the owner is there. */
function turnOf(section, arg = null) {
  const at = place();
  return at.section === section && at.arg === (arg ?? null) ? RENDER.turn : -1;
}

/** `children` as the main screen, in turn `turn` only. */
function paint(turn, ...children) {
  if (turn !== RENDER.turn) {
    throw STALE;
  }
  fill(main, ...children);
}

/** A failure, in plain words; a change that needs a fresh sign-in offers one. */
function report(error) {
  if (error === STALE) {
    return;
  }
  if (error instanceof Refused && error.message === SESSION_NEEDED) {
    say("To change this, sign in again: changes need a sign-in from the last 8 hours.", "bad",
        h("button", { type: "button", onclick: () => signIn() }, "Sign in again"));
    return;
  }
  if (error instanceof Refused && error.status === 503) {
    say(`TinyAssets is down for maintenance right now: ${error.message}`, "bad");
    return;
  }
  say(error instanceof Error ? error.message : String(error), "bad");
}

// -- Routing --------------------------------------------------------------------------

/** Where the hash points; a hash that is not one of ours is home. */
function place() {
  const [section, ...rest] = location.hash.slice(1).split("/");
  const known = SECTIONS.some(([id]) => id === section);
  let arg = null;
  try {
    arg = rest.length > 0 ? decodeURIComponent(rest.join("/")) : null;
  } catch {
    return { section: "home", arg: null };
  }
  return { section: known ? section : "home", arg };
}

function signedOutView() {
  fill(nav);
  fill(who);
  fill(main,
       h("h1", {}, "Your TinyAssets"),
       h("section", {},
         h("p", {}, "Sign in to see your agents, connect a model source and watch your runs."),
         h("button", { type: "button", onclick: () => signIn() }, "Sign in")));
}

async function render() {
  // Every render takes a turn, the signed-out view's too: no screen still loading paints
  // over it.
  RENDER.turn += 1;
  const turn = RENDER.turn;
  fill(notice);
  if (!signedIn()) {
    signedOutView();
    return;
  }
  const { section, arg } = place();
  fill(nav, SECTIONS.map(([id, label]) =>
    h("a", { href: `#${id}`, "aria-current": id === section ? "page" : null }, label)));
  fill(who, action("Sign out", async () => {
    await signOut();
    render();
  }, report, { class: "quiet" }));
  fill(main, h("p", { class: "muted" }, "Loading…"));
  try {
    await SCREENS[section](arg);
  } catch (error) {
    if (error === STALE || turn !== RENDER.turn) {
      return;
    }
    fill(main, h("h1", {}, SECTIONS.find(([id]) => id === section)[1]));
    report(error);
  }
}

// -- Home: the plan, honestly, and what the platform told the owner -------------------

function planLine(name, numbers) {
  return h("tr", {}, h("td", {}, name), h("td", {}, String(numbers.runs)),
           h("td", {}, bytes(numbers.storage_bytes)));
}

async function home(arg = null) {
  const turn = turnOf("home", arg);
  if (arg !== null) {
    return waitingOne(arg);
  }
  const [account, { notices }, cards] = await Promise.all([call("account"), call("notices"),
                                                           waitingCards()]);
  const names = await namesFor(cards);
  const used = account.usage.chunk_bytes + account.usage.inline_bytes;
  const mine = account.tiers?.[account.tier] ?? null;
  const upgrade = new URLSearchParams(location.search).get("upgrade") === "1";
  paint(turn,
       h("h1", {}, "Home"),
       h("section", {},
         h("h2", {}, "Waiting for you"),
         h("p", { class: "muted" }, "What your agents and connected apps ask you to answer. " +
           "Nothing here happens until you say yes."),
         cards.length === 0 ? h("p", { class: "muted" }, "Nothing is waiting for your answer.")
           : cards.map((card) => asking(card, names, () => home()))),
       h("section", {},
         h("h2", {}, `Your plan: ${account.tier === "paid" ? "Paid" : "Free"}`),
         mine === null
           ? h("p", { class: "message bad" }, "The plan numbers cannot be read right now, so " +
               "no run can start until they can.")
           : [h("p", {}, `Runs at once: ${mine.runs}.`),
              h("p", {}, `Storage: ${bytes(used)} used of ${bytes(mine.storage_bytes)}.`)],
         account.tiers === null ? null : h("div", { class: "scroll" }, h("table", {},
           h("thead", {}, h("tr", {}, h("th", {}, "Plan"), h("th", {}, "Runs at once"),
                            h("th", {}, "Storage"))),
           h("tbody", {}, planLine("Free", account.tiers.free),
             planLine("Paid", account.tiers.paid)))),
         h("p", { class: "muted" }, "Every plan has the same app and the same security. Plans " +
           "differ only in how many runs go at once and how much you can store."),
         upgrade ? h("p", { class: "message bad" }, "Changing plan is not open yet.") : null),
       h("section", {},
         h("h2", {}, "Notices"),
         notices.length === 0 ? h("p", { class: "muted" }, "Nothing to tell you.")
           : h("ul", {}, notices.slice().reverse().map((n) =>
             h("li", {}, h("b", {}, n.type.replace(/_/g, " ")), " ", linked(n.detail), " ",
               h("small", {}, when(n.at)))))));
}

// -- Waiting for you: every card an agent or an app asks the owner to answer ----------

/** What the asking app is called: its connected app's name, else "an app". */
function appOf(client, names) {
  const app = names.clients.find((c) => c.client === client);
  return app === undefined ? "an app" : appName(app, names.known);
}

/**
 * One answer to a card: the route its id and digest are posted to, the button's words, and
 * what is said once it answered, from the route's answer and the card.
 * @template {import("../src/app").AppPath} P
 * @param {P} route
 * @param {string} words
 * @param {(answer: AppAnswer<P>, card: any) => string} said
 */
function answering(route, words, said) {
  return { route, words, said };
}

/**
 * The kinds of card that wait on the owner's answer, as data: the route listing a kind's
 * pending cards (each with its id, the digest shown and when it was filed), what its card
 * shows, and its two answers (`answering`). A kind's yes is in the owner's session.
 */
const KINDS = Object.freeze(/** @type {const} */ ([
  { list: "registrations",
    title: (card) => card.request.display_name,
    asker: (card, names) => appOf(card.client, names),
    lines: (card, names) => {
      const client = names.clients.find((c) => c.client === card.client);
      const { delivery_method: delivery, recipe_id: recipe, command_center_id: space } =
        card.request;
      return [`Gets its mail by: ${delivery}`, `Recipe: ${recipe ?? "none"}`, `Space: ${space}`,
              `What its app may do: ${client === undefined ? "nothing now" : access(client)}`];
    },
    note: () => "Applying gives this app its own mailbox and rooms with you.",
    yes: answering("registrations/apply", "Apply",
                   (agent) => `Applied: ${agent.display_name} has its mailbox and rooms.`),
    no: answering("registrations/decline", "Decline",
                  (_, card) => `Declined: ${card.request.display_name} is not registered.`) },
  { list: "actions",
    title: (card) => card.says,
    asker: (card, names) => card.agent === null ? appOf(card.client, names)
      : card.client === null ? card.agent
      : `${card.agent}, started by ${appOf(card.client, names)}`,
    lines: (card) => [`Act: ${card.act}`, `At: ${card.door}`,
                      card.conversation === null ? null : `In conversation: ${card.conversation}`,
                      h("pre", {}, card.request),
                      card.cut ? "The call asks more than is shown here." : null],
    note: (card) => card.conversation === null
      ? "Allowing it lets this one call through, once, the next time its agent makes it."
      : "Allowing it lets this one call through, once, and resumes its run.",
    yes: answering("actions/approve", "Allow once", (answer) => answer.run === null
      ? "Allowed once: its agent's next try of this call goes through."
      : `Allowed once: its run resumes (${answer.run}).`),
    no: answering("actions/refuse", "Deny", () => "Denied: the call is not made.") },
  { list: "policy/proposals",
    title: () => "A change to your settings",
    asker: (card, names) => card.client === null ? "your own agent" : appOf(card.client, names),
    lines: (card) => [["Proposed: ", h("pre", {}, JSON.stringify(card.change, null, 2))]],
    note: () => "Applying makes only these changes, once, to your settings as they stand: the " +
      "settings it sets (null puts one back to its default, a limit off), the rules and hosts " +
      "it adds or removes. Nothing else changes. One that no longer applies is refused, and " +
      "waits for you to decline it.",
    yes: answering("policy/apply", "Apply", () => "Applied: your settings have the changes."),
    no: answering("policy/decline", "Decline", () => "Declined: your settings are unchanged.") },
]));

/** Every pending card of every kind, oldest first, each with its kind. */
async function waitingCards() {
  const lists = await Promise.all(KINDS.map((kind) => call(kind.list)));
  return lists.flatMap(({ cards }, at) => cards.map((card) => ({ card, kind: KINDS[at] })))
    .sort((a, b) => a.card.filed_at - b.card.filed_at);
}

/**
 * What the cards are shown with: the connected apps and the apps TinyAssets knows, which name
 * their askers.
 */
async function namesFor(cards) {
  if (cards.length === 0) {
    return { clients: [], known: [] };
  }
  const [{ clients }, { apps: known }] = await Promise.all([
    call("grants"), call("tools/agents_list")]);
  return { clients, known };
}

/**
 * One waiting card, as its kind shows it, with the owner's two answers, each posting the
 * card's id and the digest shown; `after` shows the screen again.
 */
function asking({ card, kind }, names, after) {
  const answer = { card_id: card.card_id, digest: card.digest };
  const reply = (side, props) => action(side.words, async () => {
    say(side.said(await call(side.route, answer), card));
    await after();
  }, report, props);
  return h("div", { class: "card" },
    h("b", {}, kind.title(card)), " ",
    h("small", {}, `asked by ${kind.asker(card, names)}, ${when(card.filed_at)}`),
    h("ul", {}, kind.lines(card, names).filter((line) => line !== null)
      .map((line) => h("li", {}, line))),
    h("p", { class: "muted" }, kind.note(card)),
    h("div", { class: "row" }, reply(kind.yes), reply(kind.no, { class: "danger" })));
}

/** Why a card's page shows no card: nothing waits under its id any more. */
const NOT_WAITING = "This is not waiting for your answer any more: it was answered, it was " +
  "asked again since, or what it was asked under changed (the asking app's access, or your " +
  "rules). If it is still wanted, its agent or app can ask again.";

/** One card's page, where a refusal, an `approve_url` and a notice lead. */
async function waitingOne(cardId) {
  const turn = turnOf("home", cardId);
  const cards = (await waitingCards()).filter(({ card }) => card.card_id === cardId);
  const names = await namesFor(cards);
  paint(turn,
       h("p", {}, h("a", { href: "#home" }, "Everything waiting")),
       h("h1", {}, "Waiting for your answer"),
       h("section", {}, cards.length === 0 ? h("p", { class: "muted" }, NOT_WAITING)
         : asking(cards[0], names, () => waitingOne(cardId))));
}

/** A notice's words, each page of this app it names a link to that page. */
function linked(text) {
  return text.split(/(\s+)/).map((word) => word.startsWith(`${RESOURCE}#`)
    ? h("a", { href: word }, word) : word);
}

// -- Agents ---------------------------------------------------------------------------

const AGENT_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const agentDir = (name) => `/workspace/agents/${name}`;

async function ensureDir(path) {
  const { entry } = await call("tools/file_stat", { path });
  if (entry === null) {
    await call("tools/file_mkdir", { path, expected_etag: null });
  }
}

async function writeNew(path, text) {
  // "" only creates: a file already there is a conflict, never overwritten.
  const written = await call("files/write", { path, expected_etag: "", text });
  if (!("entry" in written)) {
    throw new Error(`${path} is already there`);
  }
}

async function agents(arg) {
  const turn = turnOf("agents", arg);
  if (arg !== null) {
    return agent(arg);
  }
  const [{ owner_agents: owned }, { sources }, { connections }, { capabilities }] =
    await Promise.all([call("tools/agents_list"), call("sources"), call("connections/list"),
                       call("capabilities")]);
  const live = owned.filter((a) => a.retired_at === null);
  // Each engine's credentials are its record's; a subscription's only while its sign-in is
  // switched on.
  const usable = (record) => record.credentials.filter((kind) => kind !== SIGNIN_CREDENTIAL
                                                       || record.signin?.enabled === true);
  const engines = sources.filter((s) => usable(s).length > 0);
  const name = h("input", { type: "text", placeholder: "main", autocomplete: "off" });
  const engine = h("select", {}, h("option", { value: "auto" },
    "Your strongest connected source"), engines.map((s) => h("option", { value: s.id },
    s.display_name)));
  const credential = h("select", {});
  const words = { api_key: "An API key you connected",
                  [SIGNIN_CREDENTIAL]: "Your subscription, signed in inside your sandbox" };
  const credentials = () => {
    const record = engines.find((s) => s.id === engine.value);
    fill(credential, h("option", { value: "" }, "Whichever the engine has"),
         (record === undefined ? [] : usable(record)).map((kind) =>
           h("option", { value: kind }, words[kind] ?? kind)));
  };
  engine.addEventListener("change", credentials);
  credentials();
  const connection = h("select", {}, h("option", { value: "" }, "None"),
    connections.filter((c) => c.status === "active").map((c) =>
      h("option", { value: c.connection }, `${c.display_name} (${c.source})`)));
  // What a run may hold, as the object lists it; none is chosen for the owner.
  const checks = capabilities.map((c) =>
    h("label", {}, h("input", { type: "checkbox", value: c.id }), c.words));
  const create = async () => {
    const chosen = name.value.trim();
    if (!AGENT_NAME.test(chosen)) {
      throw new Error("An agent's name is lowercase letters, digits, - and _, starting " +
                      "with a letter or digit.");
    }
    const file = { engine: engine.value,
                   scopes: checks.map((c) => c.firstChild).filter((c) => c.checked)
                     .map((c) => c.value) };
    if (credential.value !== "") {
      file.credential = credential.value;
    }
    if (connection.value !== "") {
      file.connection = connection.value;
    }
    // The agent is its agent.json, so that file is written last, in one write: until it
    // lands there is no agent, and a failure before it leaves only a folder and its notes.
    await call("tools/file_mkdir", { path: agentDir(chosen), expected_etag: null });
    await writeNew(`${agentDir(chosen)}/AGENTS.md`,
                   `# ${chosen}\n\nWrite this agent's instructions here.\n`);
    await writeNew(`${agentDir(chosen)}/agent.json`, `${JSON.stringify(file, null, 2)}\n`);
    location.hash = `#agents/${encodeURIComponent(chosen)}`;
  };
  paint(turn,
       h("h1", {}, "Agents"),
       h("section", {},
         live.length === 0 ? h("p", { class: "muted" }, "You have no agents yet.")
           : h("ul", {}, live.map((a) =>
             h("li", {}, h("a", { href: `#agents/${encodeURIComponent(a.agent)}` }, a.agent),
               h("small", {}, `Since ${when(a.created_at)}`))))),
       h("section", {},
         h("h2", {}, "New agent"),
         h("p", { class: "muted" }, "An agent is a folder of files: its agent.json and its " +
           "AGENTS.md. This writes both; you change them later as files."),
         field("Name", name),
         field("Engine", engine, "What runs the agent: a provider's own app in your sandbox. " +
           "Each run is given the strongest model it can run on what you connected, shown on " +
           "the run's line; name a model in its agent.json to pin one."),
         field("Runs on", credential),
         field("API key connection", connection, "Only for an agent that runs on an API key."),
         h("div", { class: "field" }, h("span", {}, "What its runs may do"),
           h("div", { class: "checks" }, checks)),
         action("Make the agent", create, report)));
}

/** What `who`, a run of an agent, would be given now (`agent/model`): where, or why none. */
function modelLine(who, preview) {
  return h("p", { class: "muted" }, "refused" in preview ? `${who}: refused, ${preview.refused}`
    : `${who}: ${preview.engine} (${preview.model}) on ${preview.place}.`);
}

/**
 * This page's conversation with one agent: its id once started, its live socket, its run, what
 * was said each way, the run's frames, the view showing them, and the next read of the run.
 * @typedef {{ conversation: string | null, socket: { close(): void, readyState: number } | null,
 *   run: Run | null, said: [string, string][], frames: object[], listener: (frame: any) => void,
 *   timer?: ReturnType<typeof setTimeout> }} Talk
 */
/**
 * The live conversation with each agent this page talks to.
 * @type {Map<string, Talk>}
 */
const talks = new Map();

function talkOf(name) {
  if (!talks.has(name)) {
    talks.set(name, { conversation: null, socket: null, run: null, said: [], frames: [],
                      listener: () => undefined });
  }
  return talks.get(name);
}

function frameLine(frame) {
  const event = typeof frame.event === "string" ? frame.event
    : String(JSON.stringify(eventShown(frame.event)) ?? "");
  return h("div", { class: "frame" }, h("b", {}, frame.kind ?? "event"),
           event.length > 600 ? `${event.slice(0, 600)}…` : event);
}

async function agent(name) {
  const turn = turnOf("agents", name);
  const talk = talkOf(name);
  clearTimeout(talk.timer);
  const said = h("div", {});
  const frames = h("div", { class: "log", "aria-label": "Live run" });
  const state = h("p", { class: "muted" });
  const cancel = h("span", {});
  const message = h("textarea", { placeholder: `Say something to ${name}` });

  const showSaid = () => fill(said, talk.said.map(([from, text]) =>
    h("div", { class: `said ${from}` }, text)));
  const showRun = () => {
    const run = talk.run;
    fill(state, run === null ? "" : `Run ${run.state}` +
      `${run.engine ? ` on ${run.engine}` : ""}${run.model ? ` (${run.model})` : ""}` +
      `${runWhy(run) ? `: ${runWhy(run)}` : ""}`);
    const live = run !== null && ["waiting", "queued", "running"].includes(run.state);
    fill(cancel, live ? action("Stop this run", async () => {
      talk.run = await call("agent/cancel", { run_id: run.run_id });
      showRun();
    }, report, { class: "danger" }) : null);
  };
  const settle = async () => {
    if (talk.run === null) {
      return;
    }
    const run = await call("agent/status", { run_id: talk.run.run_id });
    const was = talk.run.state;
    talk.run = run;
    if (["succeeded", "failed", "unknown"].includes(run.state)
        && !["succeeded", "failed", "unknown"].includes(was)) {
      talk.said.push(["agent", run.result ?? `(${runWhy(run) ?? `the run ${run.state}`})`]);
      showSaid();
    }
    showRun();
    clearTimeout(talk.timer);
    if (["waiting", "queued", "running"].includes(run.state)) {
      // Frames show the run as it goes; its state is read again until it settles.
      talk.timer = setTimeout(() => settle().catch(report), 3000);
    }
  };
  const watch = async (conversation) => {
    if (talk.socket !== null && talk.socket.readyState <= 1) {
      return;
    }
    talk.socket = await follow(conversation, (frame) => {
      talk.frames.push(frame);
      talk.listener(frame);
    }, () => {
      talk.socket = null;
    }, () => {
      settle().catch(report);
    });
  };
  // The socket outlives this view: its frames go to whichever view of this agent is shown.
  talk.listener = (frame) => {
    frames.append(frameLine(frame));
    frames.scrollTop = frames.scrollHeight;
    if (frame.kind === "result" || frame.kind === "error") {
      settle().catch(report);
    }
  };
  const send = async () => {
    const text = message.value.trim();
    if (text === "") {
      return;
    }
    const run = await call("agent/submit", {
      agent_id: name, message: text, client_send_id: randomText(16), wait_ms: 0,
      ...(talk.conversation === null ? {} : { conversation_id: talk.conversation }) });
    message.value = "";
    talk.said.push(["me", text]);
    talk.run = run;
    talk.conversation = run.conversation_id;
    showSaid();
    showRun();
    await watch(run.conversation_id);
    await settle();
  };

  const files = h("div", {});
  const editor = h("div", {});
  const listFiles = async () => {
    const { entries } = await call("tools/file_list", { dir: agentDir(name), limit: 200 });
    fill(files, entries.length === 0 ? h("p", { class: "muted" }, "No files.")
      : h("ul", {}, entries.map((entry) => h("li", {},
        entry.kind === "file"
          ? h("a", { href: "#", onclick: (event) => {
              event.preventDefault();
              edit(editor, entry.path, shown).catch(report);
            } }, entry.path.slice(agentDir(name).length + 1))
          : `${entry.path.slice(agentDir(name).length + 1)}/`,
        h("small", {}, `${bytes(entry.size)}, changed ${new Date(entry.mtime_ms).toLocaleString()}`)))));
  };
  const newFile = h("input", { type: "text", placeholder: "skills/notes/SKILL.md" });
  const model = h("div", {});
  const showModel = async () => {
    // The read is the admission's: an agent it refuses (its agent.json not one) is refused
    // to both runs, and the page stays, its files there to fix it.
    const { attended, unattended } = await call("agent/model", { agent_id: name })
      .catch((error) => {
        if (!(error instanceof Refused)) {
          throw error;
        }
        return { attended: { refused: error.message }, unattended: { refused: error.message } };
      });
    // An outside client's run picks by its own sandbox's sign-ins: not these.
    fill(model, modelLine("A run you start now", attended),
         modelLine("A run on your schedule, now", unattended));
  };
  // A saved agent.json can change the model its runs are given.
  const shown = () => Promise.all([listFiles(), showModel()]);

  paint(turn,
       h("p", {}, h("a", { href: "#agents" }, "All agents")),
       h("h1", {}, name),
       model,
       h("section", {},
         h("h2", {}, "Talk"),
         h("p", { class: "muted" }, TALK_NOTE),
         said, field("Message", message),
         h("div", { class: "row" }, action("Send", send, report), cancel),
         state,
         h("h2", {}, "Live run"),
         frames),
       h("section", {},
         h("h2", {}, "Files"),
         h("p", { class: "muted" }, "What this agent is and can do lives in these files."),
         files,
         h("div", { class: "row" }, newFile, action("New file", async () => {
           const path = `${agentDir(name)}/${newFile.value.trim().replace(/^\/+/, "")}`;
           await edit(editor, path, shown, true);
         }, report, { class: "quiet" })),
         editor));
  showSaid();
  showRun();
  for (const frame of talk.frames) {
    frames.append(frameLine(frame));
  }
  if (talk.conversation !== null) {
    watch(talk.conversation).catch(report);
  }
  await shown();
}

/** A file's text in an editor, saved against the etag it was read at. */
async function edit(box, path, after, fresh = false) {
  let etag = "";
  let text = "";
  const kept = h("div", {});
  if (!fresh) {
    const read = await call("files/read", { path });
    if (read.text === null) {
      fill(box, h("p", { class: "muted" }, `${path} is not shown here: ${read.why}.`), kept);
      await versions(kept, path);
      return;
    }
    etag = read.entry.etag;
    text = read.text;
  }
  const area = h("textarea", { class: "editor", value: text, spellcheck: "false" });
  fill(box, h("h2", {}, path), area, h("div", { class: "row" }, action("Save", async () => {
    const parent = path.slice(0, path.lastIndexOf("/"));
    if (etag === "") {
      await ensureDirs(parent);
    }
    const written = await call("files/write", { path, expected_etag: etag, text: area.value });
    if (!("entry" in written)) {
      throw new Error("The file changed since you opened it. Open it again to see the new text.");
    }
    etag = written.entry.etag;
    say(`Saved ${path}.`);
    await Promise.all([after(), versions(kept, path)]);
  }, report)), kept);
  if (!fresh) {
    await versions(kept, path);
  }
}

/**
 * A file's kept versions, newest first: an older one's content removed (a secret written by
 * mistake), or every older one at once. Neither can be undone, and nothing else removes them
 * while you keep every version.
 */
async function versions(box, path) {
  const { changes, next } = await call("tools/file_history", { path, limit: 50 });
  const [current, ...older] = changes;
  const again = () => versions(box, path);
  fill(box, h("h3", {}, "Kept versions"),
       h("ul", {}, changes.map((c) => h("li", {}, `${when(c.at)}: ${c.op} by ${c.writer} `,
         c === current ? h("span", { class: "muted" }, "(current)")
           : c.content_id === null ? h("span", { class: "muted" }, "(no content kept)")
             : confirmed("Remove its content", "Remove this version's content? It cannot be " +
                 "read or restored from this version again; another version with the same " +
                 "content keeps its own. This cannot be undone.", "Remove it", async () => {
               await call("files/redact", { path, version: c.version });
               say(`Removed that version's content from ${path}.`);
               await again();
             }, report, { class: "danger" })))),
       next === null ? null : h("p", { class: "muted" }, "Older versions are not listed here."),
       older.length === 0 ? null : confirmed("Remove older versions", `Remove every version ` +
         `of ${path} but the current one, listed or not? This cannot be undone.`, "Remove them",
       async () => {
         await call("files/purge", { path });
         say(`Removed every older version of ${path}.`);
         await again();
       }, report, { class: "danger" }));
}

async function ensureDirs(dir) {
  const parts = dir.split("/").filter(Boolean);
  for (let i = 2; i <= parts.length; i++) {
    await ensureDir(`/${parts.slice(0, i).join("/")}`);
  }
}

/**
 * A main agent's wake-up: the address and token its vendor showed the owner (the kinds
 * whose records have the owner paste them), sealed by the gateway, never shown again.
 */
function doorbell(agent, kinds) {
  if (kinds.length === 0) {
    return [h("p", { class: "muted" }, "No main agent's app takes a pasted wake-up.")];
  }
  const kind = h("select", {}, kinds.map((k) => h("option", { value: k.id }, k.agents)));
  const url = h("input", { type: "text", placeholder: "The address your app showed you", autocomplete: "off" });
  const secret = h("input", { type: "password", autocomplete: "off" });
  return [
    h("p", { class: "muted" }, "Paste the address and token its app showed you to wake it " +
      "when something changes for it. TinyAssets seals the token and never shows it again."),
    field("Its app", kind), field("Address", url), field("Token", secret),
    action("Save the wake-up", async () => {
      await call("agent/doorbell", { agent_id: agent.agent_id, kind: kind.value,
                                     url: url.value.trim(), secret: secret.value });
      secret.value = "";
      say(`Saved. ${agent.display_name} is woken when something changes for it.`);
    }, report)];
}

/**
 * A main agent: its wake-up while it is on; turning it off, which keeps its mail with you
 * and frees what it had not read; and deleting it, its rooms' mail with it.
 */
function mainAgent(agent, kinds) {
  const name = agent.display_name;
  const on = agent.disabled_reason === null;
  return h("div", { class: "card" },
    h("b", {}, name),
    on ? doorbell(agent, kinds)
      : h("p", { class: "muted" }, "Turned off. It comes back when its app asks to join " +
          "again and you apply it."),
    on ? action("Turn off", async () => {
      await call("agent/disable", { agent_id: agent.agent_id, reason: "turned off by the owner" });
      say(`Turned off ${name}. Its mail stays with you.`);
      await apps();
    }, report, { class: "quiet" }) : null,
    confirmed("Delete", `Delete ${name} and all its mail with you and your agents? Its app ` +
      "stays connected until you disconnect it. This cannot be undone.", `Delete ${name}`,
    async () => {
      await call("agent/delete", { agent_id: agent.agent_id });
      say(`Deleted ${name} and all its mail.`);
      await apps();
    }, report, { class: "danger" }));
}

// -- Rooms and board ------------------------------------------------------------------

function heads(rows) {
  return rows.length === 0 ? h("p", { class: "muted" }, "Nothing here yet.")
    : h("ul", {}, rows.map((row) => h("li", {},
      h("b", {}, `${row.collection} · ${row.row_id}`), " ",
      row.deleted ? h("span", { class: "muted" }, "deleted") : row.head,
      row.deleted ? null : h("small", {}, `by ${row.writer}`))));
}

async function rooms(arg = null) {
  const turn = turnOf("rooms", arg);
  const [inbox, board, { owner_agents: owned }] = await Promise.all([
    call("tools/inbox_digest", { limit: 100 }),
    call("tools/board_digest", { limit: 100 }), call("tools/agents_list")]);
  const room = h("select", {}, owned.filter((a) => a.retired_at === null).map((a) =>
    h("option", { value: a.room_id }, `You and ${a.agent}`)));
  const body = h("textarea", {});
  const mail = inbox.mail.messages;
  paint(turn,
       h("h1", {}, "Rooms and board"),
       h("section", {},
         h("h2", {}, "Mail to you"),
         mail.length === 0 ? h("p", { class: "muted" }, "No new mail.")
           : h("ul", {}, mail.map((m) => h("li", {},
             h("b", {}, m.room_id), ` ${m.body ?? "(too long to show here)"} `,
             h("small", {}, `from ${m.sender_id}, ${when(m.created_at)}`)))),
         mail.length === 0 ? null : action("Mark all read", async () => {
           await call("tools/mailbox_ack", { through: mail.at(-1).seq });
           await rooms();
         }, report, { class: "quiet" })),
       h("section", {},
         h("h2", {}, "Post"),
         owned.length === 0 ? h("p", { class: "muted" }, "Make an agent first.")
           : [field("Room", room), field("Message", body),
              action("Post", async () => {
                await call("tools/room_post", { room_id: room.value,
                  client_message_id: randomText(16), body: body.value, parent_id: null });
                body.value = "";
                say("Posted. Mail to your own agent starts its run; its reply comes here.");
              }, report)]),
       h("section", {}, h("h2", {}, "Rooms"), heads(inbox.rooms.rows)),
       h("section", {}, h("h2", {}, "Board"), heads(board.rows)));
}

// -- Model sources --------------------------------------------------------------------

// The vendor's own sign-in inside your sandbox: TinyAssets starts the
// vendor's own CLI's login there and shows only what it prints for you to do: a link to the
// vendor's own sign-in page, which opens in your browser (TinyAssets never sees your
// password), a code to type there, and a box for the code the vendor's page gives back.

/** The live socket of the sign-in in progress, if any (one at a time in your sandbox). */
let signinSocket = null;

/** Why a sign-in was refused, in the words the platform's records give (`SIGNIN_REFUSALS`). */
const SIGNIN_REFUSED = Object.freeze({
  url: "it showed a link that is not the vendor's own sign-in page",
  code: "it showed a code that is not the vendor's kind of code",
  credential: "it showed something that looked like a credential",
  state: "it reported something a sign-in never reports",
});

/** The states that end a sign-in. */
const SIGNIN_ENDS = Object.freeze(["signed_in", "signed_out", "failed", "cancelled", "refused",
                                   "expired"]);

/** What the owner is told at a sign-in's end. */
function signinEnded(record, event) {
  switch (event.state) {
    case "signed_in": return `Signed in to ${record.display_name}.`;
    case "signed_out": return `Signed out of ${record.display_name}.`;
    case "cancelled": return "The sign-in was stopped.";
    case "expired": return "The code expired; start again.";
    case "refused": return `The sign-in was stopped: ${SIGNIN_REFUSED[event.reason]
      ?? "it showed something TinyAssets does not pass on"}. TinyAssets never passes such ` +
      "a thing on.";
    default: return "The sign-in did not finish. Try again.";
  }
}

function signinPanel(record, present, live) {
  const box = h("div", { class: "signin" });
  /** @param {AppAnswer<"signin/start"> | null} view */
  const show = (view) => {
    if (view === null && !record.signin.enabled) {
      // Off: no sign-in is offered, but a login already here can always be removed.
      fill(box, h("p", { class: "muted" },
                  `Signing in with your ${record.display_name} subscription is off for now: ` +
                  `the vendor's terms for a platform hosting its sign-in are ` +
                  `${record.signin.hosted_use.verdict}. `,
                  h("small", {}, record.signin.hosted_use.citation)),
           present?.signed_in ? h("div", { class: "row" }, action("Sign out",
             () => begin("out"), report, { class: "danger" })) : null);
      return;
    }
    if (view === null) {
      fill(box,
           h("p", { class: "muted" }, present?.signed_in
             ? `Signed in with your ${record.display_name} subscription inside your sandbox.`
             : `Use your ${record.display_name} subscription: sign in inside your sandbox, ` +
               "on the vendor's own page."),
           h("div", { class: "row" },
             action(present?.signed_in ? "Sign in again" : `Sign in to ${record.display_name}`,
                    () => begin("in"), report),
             present?.signed_in ? action("Sign out", () => begin("out"), report,
                                         { class: "danger" }) : null));
      return;
    }
    const code = h("input", { type: "text", autocomplete: "off", placeholder: "Code" });
    fill(box,
         h("p", {}, view.state === "starting" ? "Starting the sign-in in your sandbox…"
           : view.state === "code_sent" ? "Code sent. Finishing the sign-in…"
             : `Finish signing in on ${record.display_name}'s own page.`),
         view.url === null ? null : h("p", {}, h("a", {
           href: view.url, target: "_blank", rel: "noopener noreferrer" },
           `Open ${record.display_name}'s sign-in page`),
           ` You'll sign in on ${view.host ?? new URL(view.url).hostname}.`),
         view.code === null ? null : h("p", {}, "Type this code there: ",
                                       h("code", { class: "signin-code" }, view.code)),
         view.accepts_code ? h("div", { class: "row" }, code, action("Send code", async () => {
           await call("signin/code", { id: view.id, code: code.value.trim() });
           code.value = "";
         }, report)) : null,
         action("Stop", () => call("signin/cancel", { id: view.id }), report, { class: "quiet" }));
  };
  /** @param {AppAnswer<"signin/start">} view */
  const watch = async (view) => {
    show(view);
    signinSocket?.close();
    let current = view;
    // What came before a socket attached, the first one or one after a drop: the sign-in's
    // row as it stands; none, it ended meanwhile, and the panel offers a new one.
    const reread = async () => {
      const { signins } = await call("signin/list");
      const now = signins.find((each) => each.id === view.id);
      if (now !== undefined) {
        current = now;
        show(now);
      } else if (signinSocket !== null) {
        signinSocket.close();
        signinSocket = null;
        say("The sign-in ended before it could be shown. Start it again.", "bad");
        sources().catch(report);
      }
    };
    signinSocket = await follow(view.id, (frame) => {
      const event = frame.event ?? {};
      if (event.type !== "ta.signin") {
        return;
      }
      if (SIGNIN_ENDS.includes(event.state)) {
        say(signinEnded(record, event), event.state.startsWith("signed") ? "good" : "bad");
        signinSocket?.close();
        signinSocket = null;
        sources().catch(report);
        return;
      }
      current = event.state === "open"
        ? { ...current, state: "open", url: event.url, code: event.code,
            accepts_code: event.accepts_code }
        : { ...current, state: event.state };
      show(current);
    }, () => undefined, () => {
      reread().catch(report);
    });
    await reread();
  };
  const begin = async (mode) => {
    const view = await call("signin/start", {
      engine: record.id, ...(mode === "out" ? { mode } : {}) });
    if (view.started === false) {
      say(`The sign-in could not start: ${inWords(view.code)}.`, "bad");
      return;
    }
    await watch(view);
  };
  if (live === null) {
    show(null);
  } else {
    watch(live).catch(report);
  }
  return box;
}

async function sources(arg = null) {
  const turn = turnOf("sources", arg);
  const [{ sources: records }, { connections }, { signins: live, engines }] = await Promise.all([
    call("sources"), call("connections/list"), call("signin/list")]);
  const presence = new Map(engines.map((engine) => [engine.engine, engine]));
  const shown = connections.filter((c) => c.status !== "revoked");
  const connect = (record) => {
    const ways = [];
    if (record.connect.includes("api_key")) {
      const key = h("input", { type: "password", autocomplete: "off", placeholder: "API key" });
      ways.push(h("div", { class: "row" }, key, action("Connect with a key", async () => {
        const view = await call("connections/key", { source: record.id, key: key.value.trim() });
        key.value = "";
        say(`Connected ${view.display_name}.`);
        await sources();
      }, report)));
    }
    if (record.connect.includes("oauth")) {
      ways.push(action(`Sign in to ${record.display_name}`, async () => {
        const { authorize_url: url } = await call("connections/oauth", { source: record.id });
        location.assign(url);
      }, report));
    }
    if (record.connect.includes("cli") && record.credentials.includes(SIGNIN_CREDENTIAL)
        && record.signin !== null) {
      ways.push(signinPanel(record, presence.get(record.id) ?? null,
                            live.find((each) => each.engine === record.id
                                      && each.authority === "owner") ?? null));
    }
    if (record.connect.includes("cli") && record.credentials.includes("api_key")) {
      ways.push(h("p", { class: "muted" }, "An engine for your agents: it runs on an API key " +
        "you connect here."));
    }
    return ways;
  };
  paint(turn,
       h("h1", {}, "Model sources"),
       h("section", {},
         h("h2", {}, "Connected"),
         shown.length === 0 ? h("p", { class: "muted" }, "Nothing connected yet.")
           : h("div", { class: "scroll" }, h("table", {},
             h("thead", {}, h("tr", {}, ["Source", "State", "Models", "Since", ""].map((t) =>
               h("th", {}, t)))),
             h("tbody", {}, shown.map((c) => h("tr", {},
               h("td", {}, c.display_name),
               h("td", {}, c.status, c.pays === false
                 ? h("div", { class: "muted" }, "Free models only: this key has no credit.")
                 : null),
               h("td", {}, String(c.models.length)), h("td", {}, when(c.created_at)),
               h("td", {}, action("Disconnect", async () => {
                 await call("connections/revoke", { connection: c.connection });
                 say(c.provider_revocable ? `Disconnected ${c.display_name}.`
                   : `Disconnected ${c.display_name}. Delete the key at the provider too.`);
                 await sources();
               }, report, { class: "danger" })))))))),
       h("section", {},
         h("h2", {}, "Connect"),
         records.slice().sort((a, b) => Number(b.suggested) - Number(a.suggested)
                                        || a.display_name.localeCompare(b.display_name))
           .map((record) => h("div", { class: "card" }, h("b", {}, record.display_name),
             record.help_url ? [" ", h("a", { href: record.help_url, rel: "noreferrer",
                                               target: "_blank" }, "Where to get one")] : null,
             connect(record)))),
       h("section", {},
         h("h2", {}, "An MCP server's key"),
         h("p", { class: "muted" }, "A key for one of your agents' MCP servers, by the server's " +
           "URL exactly as its mcp.json names it. Its runs reach that server through TinyAssets, " +
           "which puts the key in; your agents never see it. Disconnect it above to stop it."),
         serverKey()));
}

/** The connector door's form: the server's URL, how it takes its key, and the key. */
function serverKey() {
  const url = h("input", { type: "url", placeholder: "The server's URL" });
  // The MCP authorization spec's own way; a server that reads another header says so.
  const header = h("input", { type: "text", value: "authorization", "aria-label": "Header" });
  const scheme = h("input", { type: "text", value: "Bearer", "aria-label": "Scheme" });
  const key = h("input", { type: "password", autocomplete: "off", placeholder: "Key" });
  return h("div", { class: "card" }, h("div", { class: "row" }, url, header, scheme, key,
    action("Save the key", async () => {
      const view = await call("connections/connector", {
        url: url.value.trim(), key: key.value.trim(), header: header.value.trim(),
        scheme: scheme.value.trim() });
      key.value = "";
      say(`Saved a key for ${view.display_name}.`);
      await sources();
    }, report)));
}

// -- Connected apps: code mode, each client's tools, revokes --------------------------

/** An app by the vendor its sign-in named (`apps.json`), unless the owner renamed it. */
function appName(client, known) {
  return client.name || known.find((app) => app.id === client.vendor)?.name
    || client.client.slice(0, 12);
}

/** What a client's grant lets it do, in words: everything, or each scope. */
function access(client) {
  return client.mode === "full" ? "Everything"
    : client.scopes.map((s) => `${s.capability} in ${s.universe} as ${s.agent}`).join("; ");
}

async function apps(arg = null) {
  const turn = turnOf("apps", arg);
  const [policy, { clients }, { tools }, { agents: mains, owner_agents: owned, apps: known },
         { kinds }] = await Promise.all([
    call("policy"), call("grants"), call("tools"), call("tools/agents_list"),
    call("agent/doorbell/kinds")]);
  const document_ = policy.policy;
  const codemode = h("input", { type: "checkbox", checked: policy.codemode.on });
  const custom = document_.tools?.custom ?? null;
  const every = h("input", { type: "checkbox", checked: custom === null });
  const picks = tools.map((tool) => h("label", {},
    h("input", { type: "checkbox", value: tool.name,
                 checked: custom === null || custom.includes(tool.name) }), tool.title));
  // While "Every tool" is on, the picks are all on and fixed: no choice is silently dropped.
  const held = () => {
    for (const pick of picks.map((p) => p.firstChild)) {
      pick.disabled = every.checked;
      if (every.checked) {
        pick.checked = true;
      }
    }
  };
  every.addEventListener("change", held);
  held();
  const write = async (changed) => {
    await call("policy/write", { expected_generation: policy.generation, policy: changed });
    say("Saved.");
    await apps();
  };
  const outside = clients.filter((c) => c.source !== "api_key" && c.mode !== "revoked");
  const disconnected = clients.filter((c) => c.source !== "api_key" && c.mode === "revoked");
  const named = (c) => appName(c, known);
  const unconnected = known.filter((app) => app.id !== null && !app.connected);
  paint(turn,
       h("h1", {}, "Connected apps"),
       h("section", {},
         h("h2", {}, "Code mode"),
         h("label", {}, codemode, " Let connected apps run one script of many tool calls"),
         h("p", { class: "muted" }, "Each call in a script is held to what that app may do."),
         action("Save", () => write({ ...document_, codemode: {
                                        ...(document_.codemode ?? {}), on: codemode.checked } }),
                report)),
       h("section", {},
         h("h2", {}, "Tools your connected apps see"),
         h("label", {}, every, " Every tool, including ones added later"),
         h("div", { class: "checks" }, picks),
         action("Save", () => {
           const { tools: _tools, ...rest } = document_;
           const chosen = picks.map((p) => p.firstChild).filter((c) => c.checked).map((c) => c.value);
           return write(every.checked ? rest : { ...rest, tools: { custom: chosen } });
         }, report)),
       h("section", {},
         h("h2", {}, "Your main agents"),
         h("p", { class: "muted" }, "A main agent is an app's own agent (a Claude scheduled " +
           "task, ChatGPT, a Grok routine) that joined your rooms and board."),
         mains.length === 0 ? h("p", { class: "muted" }, "No main agent has joined yet.")
           : mains.map((a) => mainAgent(a, kinds))),
       h("section", {},
         h("h2", {}, "Connect another assistant"),
         h("p", { class: "muted" }, "Every assistant you connect shares this same workspace: " +
           "what you tell one, the others know."),
         unconnected.length === 0
           ? h("p", { class: "muted" }, "Every assistant TinyAssets knows is connected.")
           : h("ul", { class: "apps" }, unconnected.map((app) => h("li", {},
             h("a", { href: app.connect_url, target: "_blank", rel: "noopener noreferrer" },
               `Connect ${app.name}`))))),
       h("section", {},
         h("h2", {}, "Apps you connected"),
         outside.length === 0 ? h("p", { class: "muted" }, "No app is connected.")
           : h("div", { class: "scroll" }, h("table", {},
             h("thead", {}, h("tr", {}, ["App", "Access", "Tools", "Agent", "Last used", ""]
               .map((t) => h("th", {}, t)))),
             h("tbody", {}, outside.map((c) => {
               const profile = h("select", {},
                 h("option", { value: "custom", selected: c.profile === "custom" }, "Your choice above"),
                 h("option", { value: "directory", selected: c.profile === "directory" },
                   "The public directory's set (no code mode)"));
               // Its own agent always among the choices, so a save never moves it unasked.
               const agent = h("select", {}, [...new Set([c.agent, ...owned
                 .filter((a) => a.retired_at === null).map((a) => a.agent)])].map((a) =>
                 h("option", { value: a, selected: a === c.agent },
                   a === c.agent && !c.agent_live ? `${a} (deleted)` : a)));
               return h("tr", {}, h("td", {}, named(c),
                 c.vendor !== null && c.vendor_verified === 0 && c.name === ""
                   ? h("span", { class: "muted" }, " (its own name, not verified)") : null),
                 h("td", {}, c.mode === "full" ? "Everything" : `${c.scopes.length} scopes`),
                 h("td", {}, profile),
                 h("td", {}, agent, c.agent_live ? null : h("p", { class: "muted" },
                   "This agent was deleted, so the app cannot read its instructions. Choose " +
                   "another, or put its agent.json back.")),
                 h("td", {}, when(c.last_seen)),
                 h("td", {}, action("Save", async () => {
                   await call("grants/set", { client: c.client, source: c.source, mode: c.mode,
                     scopes: c.scopes, expected_revision: c.revision, name: c.name,
                     profile: profile.value, agent: agent.value });
                   say("Saved.");
                   await apps();
                 }, report, { class: "quiet" }), action("Disconnect", async () => {
                   await call("grants/revoke", { client: c.client });
                   say(`Disconnected ${named(c)}.`);
                   await apps();
                 }, report, { class: "danger" })));
             })))),
         disconnected.length === 0 ? null : [
           h("h2", {}, "Apps you disconnected"),
           h("p", { class: "muted" }, "A disconnected app cannot connect again until you let " +
             "it. It comes back with everything; narrow it above once it is back."),
           h("ul", {}, disconnected.map((c) => h("li", {}, named(c), " ",
             action("Let it connect again", async () => {
               await call("grants/set", { client: c.client, source: c.source, mode: "full",
                 scopes: [], expected_revision: c.revision, name: c.name, profile: c.profile,
                 agent: c.agent });
               say(`${named(c)} may connect again: connect it from the app itself.`);
               await apps();
             }, report, { class: "quiet" }))))]));
}

// -- API keys -------------------------------------------------------------------------

async function keys(arg = null) {
  const turn = turnOf("keys", arg);
  const { clients } = await call("grants");
  const listed = clients.filter((c) => c.source === "api_key" && c.mode !== "revoked");
  const name = h("input", { type: "text", placeholder: "laptop" });
  const minted = h("div", {});
  paint(turn,
       h("h1", {}, "API keys"),
       h("section", {},
         h("h2", {}, "Your keys"),
         listed.length === 0 ? h("p", { class: "muted" }, "No keys.")
           : h("ul", {}, listed.map((c) => h("li", {}, h("b", {}, c.name), " ",
             h("small", {}, `Made ${when(c.first_seen)}; ${
               c.mode === "full" ? "everything" : `${c.scopes.length} scopes`}`),
             action("Turn off", async () => {
               await call("grants/revoke", { client: c.client });
               say(`Turned off ${c.name}.`);
               await keys();
             }, report, { class: "danger" }))))),
       h("section", {},
         h("h2", {}, "New key"),
         h("p", { class: "muted" }, "A key does what a connected app may: everything but " +
           "what needs you signed in here. Turn it off when you no longer use it."),
         field("Name", name),
         action("Make the key", async () => {
           const key = await call("keys", { name: name.value.trim(), scopes: [] });
           fill(minted, h("p", {}, "Copy this key now. It is not shown again."),
                h("p", { class: "secret" }, key.key));
         }, report),
         minted));
}

// -- Your data ------------------------------------------------------------------------

async function data(arg) {
  const turn = turnOf("data", arg);
  const dir = arg ?? "/workspace";
  const [{ entries }, account] = await Promise.all([
    call("tools/file_list", { dir, limit: 200 }), call("account")]);
  const editor = h("div", {});
  const parent = dir.slice(0, dir.lastIndexOf("/")) || null;
  paint(turn,
       h("h1", {}, "Your data"),
       h("section", {},
         h("h2", {}, "Export"),
         h("p", {}, `You hold ${bytes(account.usage.chunk_bytes + account.usage.inline_bytes)} ` +
           `in ${account.usage.entries} files and folders.`),
         h("p", { class: "muted" }, MISSING.export)),
       h("section", {},
         h("h2", {}, `Files in ${dir}`),
         parent !== null && dir !== "/workspace"
           ? h("p", {}, h("a", { href: `#data/${encodeURIComponent(parent)}` }, "Up a folder")) : null,
         entries.length === 0 ? h("p", { class: "muted" }, "Empty.")
           : h("ul", {}, entries.map((entry) => h("li", {},
             entry.kind === "file"
               ? h("a", { href: "#", onclick: (event) => {
                   event.preventDefault();
                   edit(editor, entry.path, () => data(arg)).catch(report);
                 } }, entry.path.split("/").at(-1))
               : h("a", { href: `#data/${encodeURIComponent(entry.path)}` },
                   `${entry.path.split("/").at(-1)}/`),
             h("small", {}, bytes(entry.size))))),
         editor));
}

// -- Past runs: newest first, a page at a time ---------------------------------------

/** A mail chain from one of its messages, oldest first, a page at a time, in `box`. */
async function chain(box, messageId, after = 0) {
  const { messages, more } = await call("mail/chain", { message_id: messageId, limit: 50,
                                                        after });
  fill(box, h("h2", {}, "The mail"),
       h("ul", {}, messages.map((m) => h("li", {},
         h("b", {}, m.sender_id), ` ${m.body ?? "(too long to show here)"} `,
         m.truncated ? h("span", { class: "muted" }, "(cut here) ") : null,
         h("small", {}, when(m.created_at))))),
       more ? action("Read on", () => chain(box, messageId, messages.at(-1).seq), report,
                     { class: "quiet" }) : null);
}

async function history(arg) {
  const turn = turnOf("history", arg);
  const before = arg === null ? undefined : Number(arg);
  const { runs, next } = await call("runs", before === undefined ? {} : { before });
  const mail = h("div", {});
  paint(turn,
       h("h1", {}, "Past runs"),
       h("section", {},
         runs.length === 0 ? h("p", { class: "muted" }, "No runs yet.")
           : h("div", { class: "scroll" }, h("table", {},
             h("thead", {}, h("tr", {}, ["Agent", "State", "Engine", "Started", "Ended",
                                         "Conversation", "Mail"].map((t) => h("th", {}, t)))),
             h("tbody", {}, runs.map((run) => h("tr", {},
               h("td", {}, h("a", { href: `#agents/${encodeURIComponent(run.agent)}` },
                             run.agent)),
               h("td", {}, run.code === null ? run.state : `${run.state} (${run.code})`),
               h("td", {}, run.engine),
               h("td", {}, run.started_at === null ? "—" : when(run.started_at)),
               h("td", {}, run.settled_at === null ? "—" : when(run.settled_at)),
               h("td", {}, h("code", {}, run.conversation_id)),
               h("td", {}, run.mail === null ? "—" : action("Read the mail",
                 () => chain(mail, run.mail), report, { class: "quiet" }))))))),
         next === null ? null : h("p", {}, h("a", { href: `#history/${next}` }, "Older runs")),
         mail));
}

const SCREENS = Object.freeze({ home, agents, history, rooms, sources, apps, keys, data });

whenSignedOut(() => render());
window.addEventListener("hashchange", () => render());
render();
