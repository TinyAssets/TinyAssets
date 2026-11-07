---
name: onboarding
description: Get to know a new owner in one short exchange - what to call them and you, their time zone, what they want help with and are working toward, how much to message them unasked, which other AI assistants they use - and offer a regular pass and brief. For an owner who moved from the old TinyAssets (a /workspace/imported folder), first say what came across and what to connect again, and offer to merge their old files. Use when the profile in USER.md has no Onboarded date, after doing what was asked.
metadata:
  ta-starter: "onboarding@3"
---

# Onboarding

The owner's request always comes first. Onboarding never blocks real work, and it runs once
for all of the owner's agents: never again once `/workspace/USER.md`'s profile has an
`Onboarded:` date. A profile that already has a time zone counts as onboarded too.
Before you start, claim `onboarding` on the board (the `shared-brain` skill, rule 3), so two
agents never onboard your owner at once; a conflict means another agent is doing it: leave
it. Mark the claim done once you write the `Onboarded:` date.

## If your owner moved from the old TinyAssets

Only if `/workspace/imported/` exists: your owner used the old TinyAssets, and their data
came across when it moved to the new platform. If it does not exist, skip this section. Put
this first in the message of step 1, in plain words, before its questions:

- Their old TinyAssets data came across: their files are in `/workspace/imported/`, copied
  unchanged, and its `README.md` lists what is there (each old command center a folder, the
  old records as read-only SQLite copies in `.records` folders). Nothing there is used until
  they or you choose to.
- What they must connect again, since none of it came across: their connectors (the
  outside services their agents used for them), their model sources (the AI accounts their
  agents run on) and their API keys. Connecting is their own act, in TinyAssets; never ask
  them to paste a key, token or password into a conversation.
- An offer to merge the imported files into their workspace, with them choosing what to
  keep: say what is there, folder by folder, and on their word copy only what they pick, to
  where they say, then check it. Never merge on your own, never overwrite one of their files
  without asking, and never delete or move anything in `/workspace/imported/` until they say
  so; then delete only what they named.
- What did not come across as plain files, for each one that is there:
  - `refused.tar`: files and folders whose names cannot be stored here (not UTF-8, or too
    long), kept unchanged with everything in them;
  - `ignored.tar`: files their agents can rebuild (dependencies, build outputs, caches),
    kept unchanged so nothing of theirs is left behind;
  - `links.txt`: the links their old files held and where each pointed, listed only: they
    were not followed and are not made here.

Write `- Old TinyAssets files are in /workspace/imported/: merge only what they pick, delete
nothing there until they say` as a directive on the profile (the `shared-brain` skill, rule
2), so every agent of theirs keeps to it; rewrite it once they decide.

1. After you have done what was asked, ask only what is missing, in one short message:
   - what to call them, and what they would like to call you;
   - their time zone (a city is enough);
   - what they want help with;
   - what they are working toward now, if anything (write each as a goal, the `goals`
     skill);
   - how much agents should message them unasked, and any times not to.
2. Write the answers: their name, time zone, wants and their words on messaging them
   unasked in `/workspace/USER.md`'s profile, which all their agents share. If you are one of
   their TinyAssets agents, write your name (and a vibe, if they gave one) on your own
   `SOUL.md`'s `Name:` and `Vibe:` lines; an assistant of another app has no `SOUL.md` there
   and writes none. Read them back briefly.
3. Their other assistants: ask which other AI assistants they use, and write them on the
   profile's `Assistants I use:` line. Name the ones `agents_list` shows already connected
   to TinyAssets; if `agents_list` lists no apps, ask which are already connected. For each
   of the rest, offer to connect it, with its connect step:
   its `connect_url` from `agents_list` (TinyAssets' list of apps, `apps.json`), or
   `https://tinyassets.io/connect` where it shows none. Connecting is their act, in that
   app; once it is connected, it shares this same workspace. Write
   `- Offered to connect <app>` as a directive for each one you offered, `<app>` its id as
   `agents_list` gives it, lower-case (the `shared-brain` skill, rule 5).
4. Offer the pass and brief (the `schedule` skill). On a yes, make it at the times they
   pick, check it exists, and say when it runs in their time and in UTC.
5. Write `- Onboarded: <YYYY-MM-DD>` on the profile, whatever they answered. If they skip a
   question, leave the field empty. Never invent a name, a time zone, an assistant or a
   preference.

Later, when TinyAssets updates these files, your owner may get a notice of a newer version
waiting beside a file as `<file>.ta-new`, or of one that would let you do more. Tell them in
plain words: nothing changes until they say so; to accept it they can ask you to adopt it, or
replace the file with the `.ta-new` copy themselves; to refuse it, delete the copy.
