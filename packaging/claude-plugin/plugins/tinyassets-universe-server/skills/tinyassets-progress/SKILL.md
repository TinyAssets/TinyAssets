---
name: tinyassets-progress
description: Summarize recent TinyAssets command-center activity from the hosted MCP server.
disable-model-invocation: true
---

# TinyAssets progress

When the user invokes `/tinyassets-universe-server:tinyassets-progress`, call the hosted TinyAssets tool `read_graph` for the command center's runs and present the result in plain language.

If the tool needs a command-center identifier the user has not given, ask which one. Do not guess, and do not read a local data directory.
