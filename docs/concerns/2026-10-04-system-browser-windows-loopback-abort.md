# System-copy browser proof records a Windows loopback abort

On 2026-10-04 in `fix/copied-system-brings-its-agents`, Python 3.14 / Windows,
`test_shipped_frame_previews_system_trusted_rail_copies_and_navigation_persists`
failed twice at its final `assert not failures`. All preceding browser checks,
including the new no-chat-agents warning, actual copy, and persisted navigation,
completed. The fixture recorded `ConnectionAbortedError: [WinError 10053]` while
writing an HTTP response in `system_server.Handler.reply`; its error reply also
hit the closed socket. This is not recorded as a passing browser test.

The second run used only that test and a fresh external basetemp:

```powershell
python -m pytest tests/test_command_center_system_browser.py::test_shipped_frame_previews_system_trusted_rail_copies_and_navigation_persists -q --tb=short --basetemp C:/Users/Jonathan/AppData/Local/Temp/ta-copied-agents-browser-retry
```

The neighboring `test_public_instruction_template_copies_private_agent_and_opens_its_chat_without_model`
passed. No fixture workaround or relaxed assertion was added. Per AGENTS.md's
same-finding-twice rule, hand this transport failure to a separate investigation;
establish baseline behavior and distinguish navigation-cancelled requests from
failed application requests before changing the fixture. Delete this concern
when the Windows browser proof passes with that distinction verified.
