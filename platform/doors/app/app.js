// The owner's app (tasks 2, the owner app): a thin surface over the doors' owner routes
// (`/app/api/*`). It builds no feature editor (Direction 3): an agent is its files, which
// the owner reads and writes here as text, and everything else is one door's answer shown
// in plain words. Every request goes to this origin only.
import {
  call, follow, randomText, Refused, signIn, signOut, signedIn, whenSignedOut,
} from "/app/static/api.js";
import { action, bytes, field, fill, h, when } from "/app/static/view.js";

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
 * The doors this app knows are not on the platform yet, each with its slice: only these say
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

// -- Messages -------------------------------------------------------------------------

function say(text, kind = "good", ...extra) {
  fill(notice, h("div", { class: `message ${kind}` }, h("span", {}, text), ...extra));
}

/** A failure, in plain words; a change that needs a fresh sign-in offers one. */
function report(error) {
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
    fill(main, h("h1", {}, SECTIONS.find(([id]) => id === section)[1]));
    report(error);
  }
}

// -- Home: the plan, honestly, and what the platform told the owner -------------------

function planLine(name, numbers) {
  return h("tr", {}, h("td", {}, name), h("td", {}, String(numbers.runs)),
           h("td", {}, bytes(numbers.storage_bytes)));
}

async function home() {
  const [account, { notices }] = await Promise.all([call("account"), call("notices")]);
  const used = account.usage.chunk_bytes + account.usage.inline_bytes;
  const mine = account.tiers?.[account.tier] ?? null;
  const upgrade = new URLSearchParams(location.search).get("upgrade") === "1";
  fill(main,
       h("h1", {}, "Home"),
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
             h("li", {}, h("b", {}, n.type.replace(/_/g, " ")), ` ${n.detail} `,
               h("small", {}, when(n.at)))))));
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
  const engines = sources.filter((s) => s.unwired === null && usable(s).length > 0);
  const signinOnly = sources.filter((s) => s.unwired === null && s.credentials.length > 0
                                           && usable(s).length === 0);
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
  fill(main,
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
         field("Engine", engine, "What runs the agent: a provider's own app in your sandbox."),
         signinOnly.length === 0 ? null : h("p", { class: "muted" }, "Signing in with a " +
           `subscription is switched off for ${signinOnly.map((s) => s.display_name)
             .join(", ")}, so ${signinOnly.length === 1 ? "it" : "they"} cannot run an ` +
           "agent yet (Model sources says why)."),
         field("Runs on", credential),
         field("API key connection", connection, "Only for an agent that runs on an API key."),
         h("div", { class: "field" }, h("span", {}, "What its runs may do"),
           h("div", { class: "checks" }, checks)),
         action("Make the agent", create, report)));
}

/** The live conversation with each agent this page talks to. */
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
    : String(JSON.stringify(frame.event) ?? "");
  return h("div", { class: "frame" }, h("b", {}, frame.kind ?? "event"),
           event.length > 600 ? `${event.slice(0, 600)}…` : event);
}

async function agent(name) {
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
      `${run.error ? `: ${run.error}` : ""}`);
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
      talk.said.push(["agent", run.result ?? (run.error ? `(${run.error})` : `(the run ${run.state})`)]);
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
              edit(editor, entry.path, listFiles).catch(report);
            } }, entry.path.slice(agentDir(name).length + 1))
          : `${entry.path.slice(agentDir(name).length + 1)}/`,
        h("small", {}, `${bytes(entry.size)}, changed ${new Date(entry.mtime_ms).toLocaleString()}`)))));
  };
  const newFile = h("input", { type: "text", placeholder: "skills/notes/SKILL.md" });

  fill(main,
       h("p", {}, h("a", { href: "#agents" }, "All agents")),
       h("h1", {}, name),
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
           await edit(editor, path, listFiles, true);
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
  await listFiles();
}

/** A file's text in an editor, saved against the etag it was read at. */
async function edit(box, path, after, fresh = false) {
  let etag = "";
  let text = "";
  if (!fresh) {
    const read = await call("files/read", { path });
    if (read.text === null) {
      fill(box, h("p", { class: "muted" }, `${path} is not shown here: ${read.why}.`));
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
    await after();
  }, report)));
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
    return h("p", { class: "muted" }, "No main agent's app takes a pasted wake-up.");
  }
  const kind = h("select", {}, kinds.map((k) => h("option", { value: k.id }, k.agents)));
  const url = h("input", { type: "text", placeholder: "The address your app showed you", autocomplete: "off" });
  const secret = h("input", { type: "password", autocomplete: "off" });
  return h("div", { class: "card" },
    h("b", {}, agent.display_name),
    h("p", { class: "muted" }, "Paste the address and token its app showed you to wake it " +
      "when something changes for it. TinyAssets seals the token and never shows it again."),
    field("Its app", kind), field("Address", url), field("Token", secret),
    action("Save the wake-up", async () => {
      await call("agent/doorbell", { agent_id: agent.agent_id, kind: kind.value,
                                     url: url.value.trim(), secret: secret.value });
      secret.value = "";
      say(`Saved. ${agent.display_name} is woken when something changes for it.`);
    }, report));
}

// -- Rooms and board ------------------------------------------------------------------

function heads(rows) {
  return rows.length === 0 ? h("p", { class: "muted" }, "Nothing here yet.")
    : h("ul", {}, rows.map((row) => h("li", {},
      h("b", {}, `${row.collection} · ${row.row_id}`), " ",
      row.deleted ? h("span", { class: "muted" }, "deleted") : row.head,
      row.deleted ? null : h("small", {}, `by ${row.writer}`))));
}

async function rooms() {
  const [inbox, board, { owner_agents: owned }] = await Promise.all([
    call("tools/inbox_digest", { limit: 100, after: 0 }),
    call("tools/board_digest", { limit: 100, after: 0 }), call("tools/agents_list")]);
  const room = h("select", {}, owned.filter((a) => a.retired_at === null).map((a) =>
    h("option", { value: `dm:${a.agent_id}` }, `You and ${a.agent}`)));
  const body = h("textarea", {});
  const mail = inbox.mail.messages;
  fill(main,
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

// The vendor's own sign-in inside your sandbox (slice SIGNIN): TinyAssets starts the
// vendor's own CLI's login there and shows only what it prints for you to do: a link to the
// vendor's own sign-in page, which opens in your browser (TinyAssets never sees your
// password), a code to type there, and a box for the code the vendor's page gives back.

/** The live socket of the sign-in in progress, if any (one at a time in your sandbox). */
let signinSocket = null;

function signinPanel(record, present, live) {
  const box = h("div", { class: "signin" });
  if (!record.signin.enabled) {
    fill(box, h("p", { class: "muted" },
                `Signing in with your ${record.display_name} subscription is off for now: ` +
                `the vendor's terms for a platform hosting its sign-in are ` +
                `${record.signin.hosted_use.verdict}. `,
                h("small", {}, record.signin.hosted_use.citation)));
    return box;
  }
  const regionPick = record.signin.regions.length === 0 ? null
    : h("select", {}, record.signin.regions.map((region) =>
      h("option", { value: region.id }, region.display_name)));
  const show = (view) => {
    if (view === null) {
      fill(box,
           h("p", { class: "muted" }, present?.signed_in
             ? `Signed in with your ${record.display_name} subscription inside your sandbox.`
             : `Use your ${record.display_name} subscription: sign in inside your sandbox, ` +
               "on the vendor's own page."),
           regionPick === null ? null : field("Region", regionPick),
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
  const watch = async (view) => {
    show(view);
    signinSocket?.close();
    let current = view;
    signinSocket = await follow(view.id, (frame) => {
      const event = frame.event ?? {};
      if (event.type !== "ta.signin") {
        return;
      }
      if (["signed_in", "signed_out", "failed", "cancelled", "refused"].includes(event.state)) {
        say(event.state === "signed_in" ? `Signed in to ${record.display_name}.`
          : event.state === "signed_out" ? `Signed out of ${record.display_name}.`
            : event.state === "cancelled" ? "The sign-in was stopped."
              : event.state === "refused" ? "The sign-in was stopped: it showed something " +
                "that looked like a credential, which TinyAssets never passes on."
                : "The sign-in did not finish. Try again.", event.state.startsWith("signed")
            ? "good" : "bad");
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
    }, () => undefined);
    // What came before the socket attached: the sign-in's row as it stands.
    const { signins } = await call("signin/list");
    const now = signins.find((each) => each.id === view.id);
    if (now !== undefined) {
      current = now;
      show(now);
    }
  };
  const begin = async (mode) => {
    const view = await call("signin/start", {
      engine: record.id, ...(mode === "out" ? { mode } : {}),
      ...(mode === "in" && regionPick !== null ? { region: regionPick.value } : {}) });
    if (view.started === false) {
      say(view.code === "sandbox_waiting" ? "Your sandbox is waiting for room. Try again in a " +
          "moment." : `The sign-in could not start (${view.code}).`, "bad");
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

async function sources() {
  const [{ sources: records }, { connections }, { signins: live, engines }] = await Promise.all([
    call("sources"), call("connections/list"), call("signin/list")]);
  const presence = new Map(engines.map((engine) => [engine.engine, engine]));
  const shown = connections.filter((c) => c.status !== "revoked");
  const connect = (record) => {
    if (record.unwired !== null) {
      return h("p", { class: "muted" }, `Not available yet: ${record.unwired}`);
    }
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
  fill(main,
       h("h1", {}, "Model sources"),
       h("section", {},
         h("h2", {}, "Connected"),
         shown.length === 0 ? h("p", { class: "muted" }, "Nothing connected yet.")
           : h("div", { class: "scroll" }, h("table", {},
             h("thead", {}, h("tr", {}, ["Source", "State", "Models", "Since", ""].map((t) =>
               h("th", {}, t)))),
             h("tbody", {}, shown.map((c) => h("tr", {},
               h("td", {}, c.display_name), h("td", {}, c.status),
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
             connect(record)))));
}

// -- Connected apps: code mode, each client's tools, revokes --------------------------

async function apps() {
  const [policy, { clients }, { tools }, { agents: mains }, { kinds }] = await Promise.all([
    call("policy"), call("grants"), call("tools"), call("tools/agents_list"),
    call("agent/doorbell/kinds")]);
  const document_ = policy.policy;
  const codemode = h("input", { type: "checkbox", checked: document_.hooks?.codemode !== false });
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
  fill(main,
       h("h1", {}, "Connected apps"),
       h("section", {},
         h("h2", {}, "Code mode"),
         h("label", {}, codemode, " Let connected apps run one script of many tool calls"),
         h("p", { class: "muted" }, "Each call in a script is held to what that app may do."),
         action("Save", () => write({ ...document_,
                                      hooks: { ...(document_.hooks ?? {}), codemode: codemode.checked } }),
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
         mains.filter((a) => a.disabled_reason === null).length === 0
           ? h("p", { class: "muted" }, "No main agent has joined yet.")
           : mains.filter((a) => a.disabled_reason === null).map((a) => doorbell(a, kinds))),
       h("section", {},
         h("h2", {}, "Apps you connected"),
         outside.length === 0 ? h("p", { class: "muted" }, "No app is connected.")
           : h("div", { class: "scroll" }, h("table", {},
             h("thead", {}, h("tr", {}, ["App", "Access", "Tools", "Last used", ""].map((t) =>
               h("th", {}, t)))),
             h("tbody", {}, outside.map((c) => {
               const profile = h("select", {},
                 h("option", { value: "custom", selected: c.profile === "custom" }, "Your choice above"),
                 h("option", { value: "directory", selected: c.profile === "directory" },
                   "The public directory's set (no code mode)"));
               return h("tr", {}, h("td", {}, c.name || c.client.slice(0, 12)),
                 h("td", {}, c.mode === "full" ? "Everything" : `${c.scopes.length} scopes`),
                 h("td", {}, profile, action("Save", async () => {
                   await call("grants/set", { client: c.client, source: c.source, mode: c.mode,
                     scopes: c.scopes, expected_revision: c.revision, name: c.name,
                     profile: profile.value });
                   say("Saved.");
                   await apps();
                 }, report, { class: "quiet" })),
                 h("td", {}, when(c.last_seen)),
                 h("td", {}, action("Disconnect", async () => {
                   await call("grants/revoke", { client: c.client });
                   say(`Disconnected ${c.name || "the app"}.`);
                   await apps();
                 }, report, { class: "danger" })));
             }))))));
}

// -- API keys -------------------------------------------------------------------------

async function keys() {
  const [{ clients }, { capabilities }] = await Promise.all([call("grants"),
                                                             call("capabilities")]);
  const listed = clients.filter((c) => c.source === "api_key" && c.mode !== "revoked");
  const name = h("input", { type: "text", placeholder: "laptop" });
  const space = h("input", { type: "text", value: "main" });
  const agentName = h("input", { type: "text", value: "main" });
  const checks = capabilities.map((c) =>
    h("label", {}, h("input", { type: "checkbox", value: c.id }), c.words));
  const minted = h("div", {});
  fill(main,
       h("h1", {}, "API keys"),
       h("section", {},
         h("h2", {}, "Your keys"),
         listed.length === 0 ? h("p", { class: "muted" }, "No keys.")
           : h("ul", {}, listed.map((c) => h("li", {}, h("b", {}, c.name), " ",
             h("small", {}, `Made ${when(c.first_seen)}; ${c.scopes.length} scopes`),
             action("Turn off", async () => {
               await call("grants/revoke", { client: c.client });
               say(`Turned off ${c.name}.`);
               await keys();
             }, report, { class: "danger" }))))),
       h("section", {},
         h("h2", {}, "New key"),
         field("Name", name),
         field("Space", space, "The space the key's agent works in."),
         field("Agent", agentName, "The agent the key acts for."),
         h("div", { class: "checks" }, checks),
         action("Make the key", async () => {
           const scopes = checks.map((c) => c.firstChild).filter((c) => c.checked).map((c) =>
             ({ universe: space.value.trim(), agent: agentName.value.trim(), capability: c.value }));
           const key = await call("keys", { name: name.value.trim(), scopes });
           fill(minted, h("p", {}, "Copy this key now. It is not shown again."),
                h("p", { class: "secret" }, key.key));
         }, report),
         minted));
}

// -- Your data ------------------------------------------------------------------------

async function data(arg) {
  const dir = arg ?? "/workspace";
  const [{ entries }, account] = await Promise.all([
    call("tools/file_list", { dir, limit: 200 }), call("account")]);
  const editor = h("div", {});
  const parent = dir.slice(0, dir.lastIndexOf("/")) || null;
  fill(main,
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
                   edit(editor, entry.path, () => data(dir)).catch(report);
                 } }, entry.path.split("/").at(-1))
               : h("a", { href: `#data/${encodeURIComponent(entry.path)}` },
                   `${entry.path.split("/").at(-1)}/`),
             h("small", {}, bytes(entry.size))))),
         editor));
}

// -- Past runs: newest first, a page at a time ---------------------------------------

async function history(arg) {
  const before = arg === null ? undefined : Number(arg);
  const { runs, next } = await call("runs", before === undefined ? {} : { before });
  fill(main,
       h("h1", {}, "Past runs"),
       h("section", {},
         runs.length === 0 ? h("p", { class: "muted" }, "No runs yet.")
           : h("div", { class: "scroll" }, h("table", {},
             h("thead", {}, h("tr", {}, ["Agent", "State", "Engine", "Started", "Ended",
                                         "Conversation"].map((t) => h("th", {}, t)))),
             h("tbody", {}, runs.map((run) => h("tr", {},
               h("td", {}, h("a", { href: `#agents/${encodeURIComponent(run.agent)}` },
                             run.agent)),
               h("td", {}, run.code === null ? run.state : `${run.state} (${run.code})`),
               h("td", {}, run.engine),
               h("td", {}, run.started_at === null ? "—" : when(run.started_at)),
               h("td", {}, run.settled_at === null ? "—" : when(run.settled_at)),
               h("td", {}, h("code", {}, run.conversation_id))))))),
         next === null ? null : h("p", {}, h("a", { href: `#history/${next}` }, "Older runs"))));
}

const SCREENS = Object.freeze({ home, agents, history, rooms, sources, apps, keys, data });

whenSignedOut(() => render());
window.addEventListener("hashchange", () => render());
render();
