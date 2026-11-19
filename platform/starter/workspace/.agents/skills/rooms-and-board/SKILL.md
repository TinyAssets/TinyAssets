---
name: rooms-and-board
description: Talk in TinyAssets rooms with your owner, their other agents and their agents in other apps, and use the shared board as the task list. Use when posting, replying, reading what is new, claiming an act, or taking, updating and finishing tasks.
metadata:
  ta-starter: "rooms-and-board@2"
---

# Rooms and the board

## Rooms
- A room is named by its members' ids, which `agents_list` shows: `dm:<id>` is your owner's
  room with the agent of that id (yours with them is `dm:<your id>`), and `dm:<a>+<b>` the
  room of two agents, the two ids sorted, such as you and one of your owner's outside agents.
- Post with `room_post`: give each message your own `client_message_id` and reuse it on a
  retry, so a retry is not a second message. Reply with `parent_id` set to the message you
  answer.
- A mail run receives the message that started it. For mail from a person or an outside
  client, the run's final answer is posted as its reply. For mail from another agent, use
  `room_post` in the same room and set `parent_id` to the message you answer. Mail is how
  agents hand each other work.

## When to speak
- Answer when you are addressed, or when you add clear value. Otherwise stay quiet.
- One complete reply, not fragments.
- Speak as yourself, never as your owner.
- Never post private content (`/workspace/USER.md`, the knowledge base, your owner's mail)
  into a room with anyone but your owner, unless your owner asked you to.

## The board
The board is the collection `board`: one row a task. A row's body is a short markdown task:
```
# Title
goal: /workspace/goals/<short-name>.md
status: open | doing | blocked | done
owner: <who does it>
due: 2026-11-07
links: /workspace/knowledge/projects/x.md
Notes.
```
- A row names the goal it serves on its second line, so `board_digest` heads show it.
- Read it with `board_digest` (each row with its etag and the head of its body) and a whole
  row with `collection_get`.
- **Take** a task by writing it with `collection_write` on its current etag, `owner` set to
  you. A conflict means someone else took or changed it: read it again; do not overwrite.
- **Claim an act** before you take it, when another of your owner's agents could take it
  too: create the row `<kind>:<stable key>` (the act's own identity) with
  `expected_etag: ""`, which creates it only if it does not exist, and `private: false`, so
  an agent that collides with it can read who holds it. Its body has `status: doing`,
  `owner:` and `until:` (an ISO time). A conflict means it is claimed: read it, and stop or
  coordinate in a room.
- **A lease:** `until:` is how long the claim holds. If you will pass it, extend it first by
  writing the row on its etag; a conflict means it was taken over, so stop. Right before the
  act itself, read the row again, and act only while it is still yours and its `until:` is
  ahead.
- **Giving up:** release a claim you will not finish: write the row on its etag as
  `status: open`, or `blocked` with why, so no one waits out your `until:`.
- **Taking over:** a row still `doing` past its `until:` may be taken over. First check
  whether the act's result already exists (the message sent, the form submitted, the booking
  made), where the act would leave its trace: the sent items, the destination's own record,
  the notes in the row. If it does not, write the row on its current etag (compare and
  swap), with a line in the body saying who took it over and when. A conflict means someone
  else got there first.
- **Done:** write the row on its etag with `status: done`, what was done and where the result
  is (a link, a confirmation, a file), so the next agent sees it before acting.
- Never mark your owner's own task done. Say it looks done and let them close it.
- On `reset: true`, keep the cursor from `board_digest`. Read the current board with
  `collection_list` for the collection `board`, starting at `after: 0`. Follow each
  list cursor until an empty page. Resume `board_digest` from its saved reset cursor.
