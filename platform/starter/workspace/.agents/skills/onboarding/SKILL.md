---
name: onboarding
description: Get to know a new owner in one short exchange - what to call them and you, their time zone, what they want help with and are working toward, how much to message them unasked, which other AI assistants they use - and offer a regular pass and brief. Use when the profile in USER.md has no Onboarded date, after doing what was asked.
metadata:
  ta-starter: "onboarding@1"
---

# Onboarding

The owner's request always comes first. Onboarding never blocks real work, and it runs once
for all of the owner's agents: never again once `/workspace/USER.md`'s profile has an
`Onboarded:` date. A profile that already has a time zone counts as onboarded too.

1. After you have done what was asked, ask only what is missing, in one short message:
   - what to call them, and what they would like to call you;
   - their time zone (a city is enough);
   - what they want help with;
   - what they are working toward now, if anything (write each as a goal, the `goals`
     skill);
   - how much agents should message them unasked, and any times not to.
2. Write the answers: their name, time zone, wants and their words on messaging them
   unasked in `/workspace/USER.md`'s profile, which all their agents share; your name (and a
   vibe, if they gave one) on `SOUL.md`'s `Name:` and `Vibe:` lines. Read both back in one line each.
3. Their other assistants: ask which other AI assistants they use, and write them on the
   profile's `Assistants I use:` line. Name the ones `agents_list` shows already connected
   to TinyAssets. For each of the rest, offer to connect it, with its connect step:
   its `connect_url` from `agents_list` (TinyAssets' list of apps, `apps.json`), or
   `https://tinyassets.io/connect` where it shows none. Connecting is their act, in that
   app; once it is connected, it shares this same workspace. Write
   `- Offered to connect <app>` as a directive for each one you offered (the `shared-brain`
   skill, rule 5).
4. Offer the pass and brief (the `schedule` skill). On a yes, make it at the times they
   pick, check it exists, and say when it runs in their time and in UTC.
5. Write `- Onboarded: <YYYY-MM-DD>` on the profile, whatever they answered. If they skip a
   question, leave the field empty. Never invent a name, a time zone, an assistant or a
   preference.

Later, when TinyAssets updates these files, your owner may get a notice of a newer version
waiting beside a file as `<file>.ta-new`, or of one that would let you do more. Tell them in
plain words: nothing changes until they say so; to accept it they can ask you to adopt it, or
replace the file with the `.ta-new` copy themselves; to refuse it, delete the copy.
