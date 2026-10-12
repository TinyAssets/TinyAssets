---
name: tinyassets-status
description: Show TinyAssets status from the hosted MCP server at tinyassets.io.
disable-model-invocation: true
---

# TinyAssets status

When the user invokes `/tinyassets-universe-server:tinyassets-status`, call the hosted TinyAssets tool `get_status` and present what it returns.

Do not start a local Python server. Do not invent fields the tool did not return. If the server asks the user to sign in, say so and stop.
