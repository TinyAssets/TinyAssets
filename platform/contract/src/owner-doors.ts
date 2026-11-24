/**
 * The owner object's doors as the doors call them: written by `npm run generate` from the
 * door table (`doors.json`), which says what each means, and `DoorTypes` (`owner.ts`),
 * which says what the table cannot. Edit those, never this file.
 */
import type { Binding } from "./binding.ts";
import type { DoorAnswer, DoorLoose, DoorRequest, DoorTypes } from "./owner.ts";
import type { DoorAnswers, DoorRequests } from "./requests.ts";

/** The owner's object, as the doors reach it (`binder.ts` alone looks one up by name). */
export interface OwnerDoors extends Rpc.DurableObjectBranded {
  /**
   * Read what is new for this agent in one call: its unacknowledged mail, oldest first, and the
   * rows of the owner's rooms changed after a cursor, each with the head of its body. Acknowledge
   * mail with mailbox_ack; a mail body cut to fit (truncated true) is read on with mailbox_read's
   * body_offset and message_id; read a whole row with collection_get. An answer with reset true
   * means a deletion past the cursor is no longer kept: read the rooms again whole, then go on from
   * the cursor given. A connection has mail once it is a registered agent: file one with
   * agent_register, for the owner to approve.
   */
  inboxDigest(binding: Binding, request: DoorRequest<"inboxDigest">):
    Promise<DoorAnswer<"inboxDigest">>;
  /**
   * Read the owner's board rows changed after a cursor in one call, each with its etag and the head
   * of its body. Claim a row with collection_write on its etag; read a whole row with
   * collection_get. An answer with reset true means a deletion past the cursor is no longer kept:
   * read the board again whole (collection_list of the board collection, after left out), then go
   * on from the cursor given.
   */
  boardDigest(binding: Binding, request: DoorRequest<"boardDigest">):
    Promise<DoorAnswer<"boardDigest">>;
  /**
   * Read this agent's unacknowledged mailbox messages, oldest first. One answer holds at most 50000
   * characters: a message whose body does not fit is cut, with truncated true and body_next_offset;
   * read the rest by calling again with body_offset set to it and message_id set to that message's,
   * before acknowledging it. A connection has a mailbox once it is a registered agent: file one
   * with agent_register, for the owner to approve.
   */
  mailboxRead(binding: Binding, request: DoorRequest<"mailboxRead">):
    Promise<DoorAnswer<"mailboxRead">>;
  /**
   * Acknowledge this agent's mailbox messages through a sequence number.
   */
  mailboxAck(binding: Binding, request: DoorRequest<"mailboxAck">):
    Promise<DoorAnswer<"mailboxAck">>;
  /**
   * Post a message to a room of the owner's account: the room_id agents_list shows for the agent to
   * reach. Mail to one of the owner's own agents starts its run, whose result is its reply.
   */
  roomPost(binding: Binding, request: DoorRequest<"roomPost">): Promise<DoorAnswer<"roomPost">>;
  /**
   * List the agents registered for this connection, the owner's own agents, each with the room_id
   * it is reached in (null until the owner approves this connection's agent registration), and
   * which of the owner's AI apps are connected to TinyAssets, each one not connected with its
   * connect_url, the page with its connect step. One answer holds as many as fit; when it carries
   * next, read again with after set to it for the rest.
   */
  outsideAgents(binding: Binding, request?: DoorRequest<"outsideAgents">):
    Promise<DoorAnswer<"outsideAgents">>;
  /**
   * File this connection's agent registration for the owner to approve. The answer's approve_url is
   * the owner's page to approve it: give it to your user, who opens it signed in to TinyAssets.
   * Once approved, the connection is that agent: it has a mailbox, and posts in its rooms. Filing
   * again replaces the one waiting.
   */
  registerPropose(binding: Binding, request: DoorRequest<"registerPropose">):
    Promise<DoorAnswer<"registerPropose">>;
  /**
   * Propose a change to the owner's settings, for the owner to apply or decline: name only what
   * changes (a rule or host to add or remove, a setting to set), as a "don't ask again" suggestion
   * does; applying it changes nothing else, whatever the owner has set. The answer's approve_url is
   * the owner's page of it: give it to your user, who opens it signed in to TinyAssets.
   */
  policyPropose(binding: Binding, request: DoorRequest<"policyPropose">):
    Promise<DoorAnswer<"policyPropose">>;
  /**
   * Read one row of a collection. One answer holds at most 50000 characters: a body that does not
   * fit is cut, with truncated true and body_next_offset; read the rest by calling again with
   * body_offset set to it and etag set to the row's etag, which refuses the read if the row changed
   * meanwhile.
   */
  collectionGet(binding: Binding, request: DoorRequest<"collectionGet">):
    Promise<DoorAnswer<"collectionGet">>;
  /**
   * List a collection's rows changed after a cursor, as many as one answer holds (at most 50000
   * characters); go on from the cursor it gives. A first row whose body does not fit is cut
   * (truncated true): read the rest with collection_get.
   */
  collectionPull(binding: Binding, request: DoorRequest<"collectionPull">):
    Promise<DoorAnswer<"collectionPull">>;
  /**
   * Read the kept history of one collection row since it was made, newest first by page; pass the
   * before it gives for the older entries when the answer says more. A newest entry whose body does
   * not fit one answer is cut (truncated true): call again with the same before and body_offset set
   * to its body_next_offset.
   */
  collectionHistory(binding: Binding, request: DoorRequest<"collectionHistory">):
    Promise<DoorAnswer<"collectionHistory">>;
  /**
   * Write one collection row against its current etag. A new public row (expected_etag "", private
   * false) is refused when the row exists, so creating one claims an act: on the board, the first
   * agent to create the row named for an act holds it, and a conflict means another agent has it.
   */
  collectionWrite(binding: Binding, request: DoorRequest<"collectionWrite">):
    Promise<DoorAnswer<"collectionWrite">>;
  /**
   * Delete one collection row against its current etag.
   */
  collectionDelete(binding: Binding, request: DoorRequest<"collectionDelete">):
    Promise<DoorAnswer<"collectionDelete">>;
  /**
   * Delete a collection's rows, or one writer's rows.
   */
  collectionClear(binding: Binding, request: DoorRequest<"collectionClear">):
    Promise<DoorAnswer<"collectionClear">>;
  /**
   * Read one file's or directory's entry, or null where nothing is, where file_read refuses a
   * missing path (not_found): so it tells whether to create a file (file_write with expected_etag
   * "").
   */
  fileStat(binding: Binding, request: DoorRequest<"fileStat">): Promise<DoorAnswer<"fileStat">>;
  /**
   * List a directory's entries by name. With head_chars, each small file also carries the start of
   * its text, enough to read its title and description.
   */
  fileList(binding: Binding, request: DoorRequest<"fileList">): Promise<DoorAnswer<"fileList">>;
  /**
   * Use this to read a file of the user's TinyAssets workspace by its path, such as their profile
   * /workspace/USER.md: its UTF-8 text from offset (0 when omitted), at most max_bytes (65536 when
   * omitted, up to 4194304) and at most what one answer holds (50000 characters), cut at a
   * character. next_offset is where the next part starts, or null at the end. Its entry carries the
   * etag file_edit and file_write take.
   */
  fileText(binding: Binding, request: DoorRequest<"fileText">): Promise<DoorAnswer<"fileText">>;
  /**
   * Use this when the user's request may depend on what they or their other agents already saved:
   * their details, preferences, goals, decisions, notes and projects. Searches the user's
   * TinyAssets workspace by words: file paths, titles, descriptions and tags, small files' text,
   * and index lines (/workspace/index.md maps the workspace). Answers the best results, each an id
   * for fetch, a title and a url. When next_cursor is set, only part of the files were searched:
   * search again with it for the rest.
   */
  fileSearch(binding: Binding, request: DoorRequest<"fileSearch">):
    Promise<DoorAnswer<"fileSearch">>;
  /**
   * Use this to read one of the user's TinyAssets files a search found, by its id, the file's path:
   * its title, its text (as much as one answer holds; when metadata.truncated, file_read reads on
   * from metadata.next_offset), its url and its metadata, the etag file_edit takes among them.
   */
  fileFetch(binding: Binding, request: DoorRequest<"fileFetch">): Promise<DoorAnswer<"fileFetch">>;
  /**
   * Use this to save what the user tells you that is worth keeping, so their other agents know it
   * too: adds text at the end of a file in their TinyAssets workspace, such as a directive at the
   * end of /workspace/USER.md or a note in their knowledge, and makes the file when it is absent.
   * It never changes what is already there. The text goes in exactly as given: begin it with a
   * newline where the file needs one. A JSON, TOML or YAML file is changed with file_edit instead.
   */
  fileAppend(binding: Binding, request: DoorRequest<"fileAppend">):
    Promise<DoorAnswer<"fileAppend">>;
  /**
   * Write a whole file of the user's TinyAssets workspace: its text replaces what is there, at the
   * file's current etag, or "" to create a file that is not there yet. A conflict answers the file
   * as it now stands. At most 4 MiB of text.
   */
  fileWrite(binding: Binding, request: DoorRequest<"fileWrite">): Promise<DoorAnswer<"fileWrite">>;
  /**
   * Use this to correct a file of the user's TinyAssets workspace in place, such as a directive in
   * /workspace/USER.md that stopped being true: replaces the one exact occurrence of old_text with
   * new_text, at the file's current etag (from fetch, file_read or file_stat). Text found nowhere,
   * or more than once, is refused: give enough of it to be unique. A conflict answers the file as
   * it now stands.
   */
  fileEdit(binding: Binding, request: DoorRequest<"fileEdit">): Promise<DoorAnswer<"fileEdit">>;
  /**
   * List a path's kept versions, newest first.
   */
  fileHistory(binding: Binding, request: DoorRequest<"fileHistory">):
    Promise<DoorAnswer<"fileHistory">>;
  /**
   * Read the owner's file usage and quotas.
   */
  fileUsage(binding: Binding): Promise<DoorAnswer<"fileUsage">>;
  /**
   * Make a directory against the path's current etag.
   */
  fileMkdir(binding: Binding, request: DoorRequest<"fileMkdir">): Promise<DoorAnswer<"fileMkdir">>;
  /**
   * Remove a file or directory against its current etag. A directory goes only with recursive true
   * (refused is_directory when empty, not_empty with its contents). A path removed since its etag
   * was read answers conflict null.
   */
  fileRemove(binding: Binding, request: DoorRequest<"fileRemove">):
    Promise<DoorAnswer<"fileRemove">>;
  /**
   * Rename a file or directory against its current etag.
   */
  fileRename(binding: Binding, request: DoorRequest<"fileRename">):
    Promise<DoorAnswer<"fileRename">>;
  /**
   * Make a kept version of a file its current content.
   */
  fileRestore(binding: Binding, request: DoorRequest<"fileRestore">):
    Promise<DoorAnswer<"fileRestore">>;
  /**
   * Send a message to one of the owner's agents: a run of its engine in a new conversation, or in
   * one of yours by conversation_id. A retry with the same client_send_id is the same run. Waits up
   * to wait_ms for the run to end, then answers the run as it stands; run_status reads it later.
   */
  agentSubmit(binding: Binding, request: DoorRequest<"agentSubmit">):
    Promise<DoorAnswer<"agentSubmit">>;
  /**
   * Launch one of the owner's agents on an input, in a fresh conversation of its own: now, at a
   * time, or at the times a cron names. Answers the run, or the schedule, at once.
   */
  agentRun(binding: Binding, request: DoorRequest<"agentRun">): Promise<DoorAnswer<"agentRun">>;
  /**
   * Read one of your runs, its state and its result once done, or one of your schedules.
   */
  agentStatus(binding: Binding, request: DoorRequest<"agentStatus">):
    Promise<DoorAnswer<"agentStatus">>;
  /**
   * Cancel one of your runs (its engine is stopped), or one of your schedules.
   */
  agentCancel(binding: Binding, request: DoorRequest<"agentCancel">):
    Promise<DoorAnswer<"agentCancel">>;
  /**
   * Run one JavaScript script that calls your other tools here as `await tools.<name>(args)`, each
   * held to your grant, and answer its return value and output: many tool calls in one. Its effects
   * are the effects of the tools it calls. Only while the owner's policy turns code mode on; a
   * script is stopped once you stop waiting for it, or past its compute or memory bound.
   */
  codeRun(binding: Binding, request: DoorRequest<"codeRun">, stop: ReadableStream<Uint8Array>):
    Promise<DoorAnswer<"codeRun">>;
  sessionOpen(binding: Binding): Promise<DoorAnswer<"sessionOpen">>;
  sessionExtend(binding: Binding): Promise<DoorAnswer<"sessionExtend">>;
  sessionClose(binding: Binding): Promise<DoorAnswer<"sessionClose">>;
  sessionCloseAll(binding: Binding): Promise<DoorAnswer<"sessionCloseAll">>;
  sandboxBilled(...request: DoorLoose<"sandboxBilled">): Promise<DoorAnswer<"sandboxBilled">>;
  sandboxDays(binding: Binding, ...request: DoorLoose<"sandboxDays">):
    Promise<DoorAnswer<"sandboxDays">>;
  storageUsage(binding: Binding): Promise<DoorAnswer<"storageUsage">>;
  ownerExport(binding: Binding, request: DoorRequest<"ownerExport">):
    Promise<DoorAnswer<"ownerExport">>;
  exportChunks(binding: Binding, request: DoorRequest<"exportChunks">):
    Promise<DoorAnswer<"exportChunks">>;
  exportEnd(binding: Binding, ...request: DoorLoose<"exportEnd">): Promise<DoorAnswer<"exportEnd">>;
  dataKeyRewrap(binding: Binding): Promise<DoorAnswer<"dataKeyRewrap">>;
  gatewayReseal(binding: Binding): Promise<DoorAnswer<"gatewayReseal">>;
  policyRead(binding: Binding): Promise<DoorAnswer<"policyRead">>;
  policyReceipts(binding: Binding): Promise<DoorAnswer<"policyReceipts">>;
  policyWrite(binding: Binding, request: DoorRequest<"policyWrite">):
    Promise<DoorAnswer<"policyWrite">>;
  policyProposals(binding: Binding): Promise<DoorAnswer<"policyProposals">>;
  policyApply(binding: Binding, request: DoorRequest<"policyApply">):
    Promise<DoorAnswer<"policyApply">>;
  policyDecline(binding: Binding, request: DoorRequest<"policyDecline">):
    Promise<DoorAnswer<"policyDecline">>;
  policyWakeLimits(binding: Binding, request: DoorRequest<"policyWakeLimits">):
    Promise<DoorAnswer<"policyWakeLimits">>;
  actionRequests(binding: Binding): Promise<DoorAnswer<"actionRequests">>;
  actionApprove(binding: Binding, request: DoorRequest<"actionApprove">):
    Promise<DoorAnswer<"actionApprove">>;
  actionRefuse(binding: Binding, request: DoorRequest<"actionRefuse">):
    Promise<DoorAnswer<"actionRefuse">>;
  grantList(binding: Binding): Promise<DoorAnswer<"grantList">>;
  grantSet(binding: Binding, request: DoorRequest<"grantSet">): Promise<DoorAnswer<"grantSet">>;
  grantRevoke(binding: Binding, request: DoorRequest<"grantRevoke">):
    Promise<DoorAnswer<"grantRevoke">>;
  grantForget(binding: Binding, request: DoorRequest<"grantForget">):
    Promise<DoorAnswer<"grantForget">>;
  keyMint(binding: Binding, request: DoorRequest<"keyMint">): Promise<DoorAnswer<"keyMint">>;
  keyList(binding: Binding): Promise<DoorAnswer<"keyList">>;
  ownerErase(binding: Binding, request: DoorRequest<"ownerErase">):
    Promise<DoorAnswer<"ownerErase">>;
  erasureBegin(binding: Binding, request: DoorRequest<"erasureBegin">):
    Promise<DoorAnswer<"erasureBegin">>;
  erasureOutside(binding: Binding): Promise<DoorAnswer<"erasureOutside">>;
  erasureOutsideDone(binding: Binding): Promise<DoorAnswer<"erasureOutsideDone">>;
  inboundList(binding: Binding): Promise<DoorAnswer<"inboundList">>;
  inboundCreate(binding: Binding, ...request: DoorLoose<"inboundCreate">):
    Promise<DoorAnswer<"inboundCreate">>;
  inboundSet(binding: Binding, ...request: DoorLoose<"inboundSet">):
    Promise<DoorAnswer<"inboundSet">>;
  inboundRevoke(binding: Binding, ...request: DoorLoose<"inboundRevoke">):
    Promise<DoorAnswer<"inboundRevoke">>;
  inboundAccept(binding: Binding, request: DoorRequest<"inboundAccept">):
    Promise<DoorAnswer<"inboundAccept">>;
  fileUploadTarget(binding: Binding, request: DoorRequest<"fileUploadTarget">):
    Promise<DoorAnswer<"fileUploadTarget">>;
  fileUploadStored(binding: Binding, request: DoorRequest<"fileUploadStored">):
    Promise<DoorAnswer<"fileUploadStored">>;
  registerAgent(binding: Binding, request: DoorRequest<"registerAgent">):
    Promise<DoorAnswer<"registerAgent">>;
  registerProposals(binding: Binding): Promise<DoorAnswer<"registerProposals">>;
  registerDecline(binding: Binding, request: DoorRequest<"registerDecline">):
    Promise<DoorAnswer<"registerDecline">>;
  disableAgent(binding: Binding, request: DoorRequest<"disableAgent">):
    Promise<DoorAnswer<"disableAgent">>;
  agentDoorbell(binding: Binding, request: DoorRequest<"agentDoorbell">):
    Promise<DoorAnswer<"agentDoorbell">>;
  agentDoorbellEnd(binding: Binding, request: DoorRequest<"agentDoorbellEnd">):
    Promise<DoorAnswer<"agentDoorbellEnd">>;
  deleteAgent(binding: Binding, request: DoorRequest<"deleteAgent">):
    Promise<DoorAnswer<"deleteAgent">>;
  connectionPrepare(binding: Binding, ...request: DoorLoose<"connectionPrepare">):
    Promise<DoorAnswer<"connectionPrepare">>;
  connectionCommit(binding: Binding, ...request: DoorLoose<"connectionCommit">):
    Promise<DoorAnswer<"connectionCommit">>;
  connectionRevoke(binding: Binding, ...request: DoorLoose<"connectionRevoke">):
    Promise<DoorAnswer<"connectionRevoke">>;
  connectionList(binding: Binding): Promise<DoorAnswer<"connectionList">>;
  flowFinish(binding: Binding, ...request: DoorLoose<"flowFinish">):
    Promise<DoorAnswer<"flowFinish">>;
  noticeList(binding: Binding): Promise<DoorAnswer<"noticeList">>;
  starterReceipts(binding: Binding): Promise<DoorAnswer<"starterReceipts">>;
  admissionSet(binding: Binding, ...request: DoorLoose<"admissionSet">):
    Promise<DoorAnswer<"admissionSet">>;
  ownerState(binding: Binding): Promise<DoorAnswer<"ownerState">>;
  toolProfile(binding: Binding): Promise<DoorAnswer<"toolProfile">>;
  definitionRead(binding: Binding): Promise<DoorAnswer<"definitionRead">>;
  mailChain(binding: Binding, request: DoorRequest<"mailChain">): Promise<DoorAnswer<"mailChain">>;
  wakeEvents(binding: Binding, ...request: DoorLoose<"wakeEvents">):
    Promise<DoorAnswer<"wakeEvents">>;
  wakeSubscribe(binding: Binding, request: DoorRequest<"wakeSubscribe">):
    Promise<DoorAnswer<"wakeSubscribe">>;
  wakeUnsubscribe(binding: Binding, request: DoorRequest<"wakeUnsubscribe">):
    Promise<DoorAnswer<"wakeUnsubscribe">>;
  fileRead(binding: Binding, request: DoorRequest<"fileRead">): Promise<DoorAnswer<"fileRead">>;
  fileReadChunks(binding: Binding, request: DoorRequest<"fileReadChunks">):
    Promise<DoorAnswer<"fileReadChunks">>;
  fileUploadBegin(binding: Binding, request: DoorRequest<"fileUploadBegin">):
    Promise<DoorAnswer<"fileUploadBegin">>;
  fileUploadChunks(binding: Binding, request: DoorRequest<"fileUploadChunks">):
    Promise<DoorAnswer<"fileUploadChunks">>;
  fileUploadCommit(binding: Binding, request: DoorRequest<"fileUploadCommit">):
    Promise<DoorAnswer<"fileUploadCommit">>;
  fileUploadAbort(binding: Binding, request: DoorRequest<"fileUploadAbort">):
    Promise<DoorAnswer<"fileUploadAbort">>;
  fileRedact(binding: Binding, request: DoorRequest<"fileRedact">):
    Promise<DoorAnswer<"fileRedact">>;
  fileHistoryPurge(binding: Binding, request: DoorRequest<"fileHistoryPurge">):
    Promise<DoorAnswer<"fileHistoryPurge">>;
  fileChanges(binding: Binding, request: DoorRequest<"fileChanges">):
    Promise<DoorAnswer<"fileChanges">>;
  runEvents(binding: Binding, request: DoorRequest<"runEvents">): Promise<DoorAnswer<"runEvents">>;
  runDecide(binding: Binding, request: DoorRequest<"runDecide">): Promise<DoorAnswer<"runDecide">>;
  signinStart(binding: Binding, request: DoorRequest<"signinStart">):
    Promise<DoorAnswer<"signinStart">>;
  signinEvents(binding: Binding, ...request: DoorLoose<"signinEvents">):
    Promise<DoorAnswer<"signinEvents">>;
  signinCode(binding: Binding, request: DoorRequest<"signinCode">):
    Promise<DoorAnswer<"signinCode">>;
  signinCancel(binding: Binding, request: DoorRequest<"signinCancel">):
    Promise<DoorAnswer<"signinCancel">>;
  signinList(binding: Binding): Promise<DoorAnswer<"signinList">>;
  runList(binding: Binding, request: DoorRequest<"runList">): Promise<DoorAnswer<"runList">>;
  agentModel(binding: Binding, request: DoorRequest<"agentModel">):
    Promise<DoorAnswer<"agentModel">>;
  signinReport(binding: Binding, request: DoorRequest<"signinReport">):
    Promise<DoorAnswer<"signinReport">>;
  sandboxRung(): Promise<DoorAnswer<"sandboxRung">>;
  socketTicket(binding: Binding, request: DoorRequest<"socketTicket">):
    Promise<DoorAnswer<"socketTicket">>;
  socketsEnd(binding: Binding): Promise<DoorAnswer<"socketsEnd">>;
  tierSet(binding: Binding, ...request: DoorLoose<"tierSet">): Promise<DoorAnswer<"tierSet">>;
  tierRead(binding: Binding): Promise<DoorAnswer<"tierRead">>;
  restoreSource(binding: Binding): Promise<DoorAnswer<"restoreSource">>;
  restoreBegin(binding: Binding, request: DoorRequest<"restoreBegin">):
    Promise<DoorAnswer<"restoreBegin">>;
  restoreStep(binding: Binding): Promise<DoorAnswer<"restoreStep">>;
  restorePin(binding: Binding, request: DoorRequest<"restorePin">):
    Promise<DoorAnswer<"restorePin">>;
  restoreWipe(binding: Binding, request: DoorRequest<"restoreWipe">):
    Promise<DoorAnswer<"restoreWipe">>;
  restoreRelease(binding: Binding): Promise<DoorAnswer<"restoreRelease">>;
  restoreNames(binding: Binding, request: DoorRequest<"restoreNames">):
    Promise<DoorAnswer<"restoreNames">>;
  restoreHold(binding: Binding, request: DoorRequest<"restoreHold">):
    Promise<DoorAnswer<"restoreHold">>;
  egressOpen(binding: Binding, ...request: DoorLoose<"egressOpen">):
    Promise<DoorAnswer<"egressOpen">>;
  egressReserve(binding: Binding, ...request: DoorLoose<"egressReserve">):
    Promise<DoorAnswer<"egressReserve">>;
  egressBeat(binding: Binding, ...request: DoorLoose<"egressBeat">):
    Promise<DoorAnswer<"egressBeat">>;
  egressSettle(binding: Binding, ...request: DoorLoose<"egressSettle">):
    Promise<DoorAnswer<"egressSettle">>;
  egressReceipts(binding: Binding): Promise<DoorAnswer<"egressReceipts">>;
  fileSyncCommit(binding: Binding, request: DoorRequest<"fileSyncCommit">):
    Promise<DoorAnswer<"fileSyncCommit">>;
  fileSyncUploadBegin(binding: Binding, request: DoorRequest<"fileSyncUploadBegin">):
    Promise<DoorAnswer<"fileSyncUploadBegin">>;
  fileSyncUploadChunks(binding: Binding, request: DoorRequest<"fileSyncUploadChunks">):
    Promise<DoorAnswer<"fileSyncUploadChunks">>;
  fileSyncUploadAbort(binding: Binding, request: DoorRequest<"fileSyncUploadAbort">):
    Promise<DoorAnswer<"fileSyncUploadAbort">>;
  importRows(binding: Binding, request: DoorRequest<"importRows">):
    Promise<DoorAnswer<"importRows">>;
  importFiles(binding: Binding, request: DoorRequest<"importFiles">):
    Promise<DoorAnswer<"importFiles">>;
  importFileBegin(binding: Binding, request: DoorRequest<"importFileBegin">):
    Promise<DoorAnswer<"importFileBegin">>;
  importFileStored(binding: Binding, request: DoorRequest<"importFileStored">):
    Promise<DoorAnswer<"importFileStored">>;
  importFileCommit(binding: Binding, request: DoorRequest<"importFileCommit">):
    Promise<DoorAnswer<"importFileCommit">>;
  importNotice(binding: Binding, request: DoorRequest<"importNotice">):
    Promise<DoorAnswer<"importNotice">>;
  importCheck(binding: Binding, request: DoorRequest<"importCheck">):
    Promise<DoorAnswer<"importCheck">>;
  importDiscard(binding: Binding): Promise<DoorAnswer<"importDiscard">>;
}

/** The doors whose rows leave their parameters to their modules. */
type Loose =
    "sandboxBilled" | "sandboxDays" | "exportEnd" | "inboundCreate" | "inboundSet" | "inboundRevoke"
      | "connectionPrepare" | "connectionCommit" | "connectionRevoke" | "flowFinish"
      | "admissionSet" | "wakeEvents" | "signinEvents" | "tierSet" | "egressOpen" | "egressReserve"
      | "egressBeat" | "egressSettle";
/** The doors the doors call no more, which the object still takes, its types with it. */
type Dropped = "grantForgetRevoked";
/** What `DoorTypes` may say of a door, by its row. */
type Parts<D> = (D extends keyof DoorAnswers ? "answer" : never)
  | (D extends keyof DoorRequests ? "values" | "handed"
    : D extends Loose ? "loose" : never);
/** The fields a door's row lists (any of a choice's). */
type Fields<D> = D extends keyof DoorRequests
  ? DoorRequests[D] extends infer R ? R extends unknown ? keyof R : never : never : never;
/** The fields of an answer, any of its arms'. */
type Keys<A> = A extends object ? keyof A : never;
/** What an entry of `DoorTypes` says that its row leaves no room for. */
type Stray<D extends keyof DoorTypes> = Exclude<keyof DoorTypes[D], Parts<D>>
  | (DoorTypes[D] extends { readonly values: infer V }
    ? Exclude<keyof V, Fields<D>> : never)
  | (DoorTypes[D] extends { readonly answer: infer A } ? D extends keyof DoorAnswers
    ? Exclude<Keys<A>, Keys<DoorAnswers[D]>>
      | ([DoorAnswer<D>] extends [DoorAnswers[D]] ? never : "a type wider than its row's")
    : never : never);

/**
 * The doors `DoorTypes` names: `tsc` refuses one that is no door of the table, or one whose
 * entry says what its row leaves no room for (a part, or a field its row does not list).
 */
type Doors<D extends keyof OwnerDoors | Dropped> = D;
type None<D extends never> = D;
type Strays = { [D in keyof DoorTypes]: [Stray<D>] extends [never] ? never : D };
export type TypedDoors = Doors<keyof DoorTypes> | None<Strays[keyof DoorTypes]>;
