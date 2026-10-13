---
name: tinyassets-premise
description: Read or replace the premise the TinyAssets command center is working from.
disable-model-invocation: true
---

# TinyAssets premise

When the user invokes `/tinyassets-universe-server:tinyassets-premise`, use the hosted TinyAssets tool `converse`.

- With no extra text, ask the command center for the premise it is working from and show the reply.
- With text, warn that this replaces the current premise and send it only after the user confirms.

Do not write a premise file on the user's machine.
