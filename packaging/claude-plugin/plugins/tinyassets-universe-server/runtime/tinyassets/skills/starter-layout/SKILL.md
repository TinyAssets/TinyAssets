---
name: starter-layout
description: Install or edit the starter chat, feed, ideas, goals and files app_ui component.
---

Read `starter/command-center.json`, the owner's current app_ui index/revision,
and `ta describe write_graph` -> interfaces handbook. Install this one component
with operation add_ui, or replace_ui when its ui_id already exists; never replace
the whole UI library. These single-component operations need no revision.
Activate it only for a fresh setup or when requested;
preserve an existing choice. Verify the saved component and run app_ui_preview.
Do not claim the default is installed from a JSON file alone.

This component is editable or replaceable. Chat uses the ordinary send_message
bridge; feed reads starter/feed.md, ideas reads starter/ideas.json, goals reads
starter/goals.json, files browses command-center files. Refresh rereads current
files. Missing/malformed files are visible errors, not stock substitutes.
Use the agent's normal file tools to edit the data and the governed app_ui path
to change the component. Prompt examples prefill chat; they never send by
themselves. No direct network access, embedded credentials or external scripts.
