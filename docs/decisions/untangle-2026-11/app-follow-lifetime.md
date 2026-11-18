# Owner app socket lifetime as built (2026-11-18)

On main `f934871ad`, an owner could leave and revisit an agent while its
first ticket or reconnect was pending. The earlier and later follows both
opened and rendered the same live frame twice. In Model sources, a late
list or ticket from sign-in A could close the socket for B and announce
that B had ended. Closing during a reconnect still opened the new socket
when its ticket arrived. The same failures occurred in the joined build
`61eca6794`.

The browser owns one follow from the request for its first ticket through
reconnects until it closes. The handle is available immediately. Its
`ready` promise reports initial ticket failure to its caller. Closing
cancels the backoff and ignores an outstanding ticket's later result. On
a live socket the browser still delivers the transport's close event.
Agent Talk keeps its follow across navigation. The sign-in panel replaces
its follow and a pending observation applies only to its own follow. An
obsolete render checks its turn before it can open a sign-in panel.

The owner row and the live prompt have no common revision. A row read that
overlaps a newer frame can hide the URL and code. The panel displays the
frame immediately, discards the ambiguous read, then reads the owner's
row again. That observation can also confirm that the sign-in ended.
An older outstanding request cannot overwrite a newer reread.

Offline comparisons exercised click, navigation, delayed row and ticket
answers, a dropped connection and one live frame using the shipped app
scripts. On main, returning to the same agent while reconnecting asked for
three tickets and left two sockets displaying the frame. On the candidate
it asked for two tickets in total and displayed it once. Both agent and
sign-in still use the owner's one conversation socket protocol.

The server's six close reasons also occupy four literal codes in the raw
browser asset for final closure. All six currently agree on whether to
retry. Resolve their shared representation when reviewing the separate
restore close (`r5w.md` item 3), preserving ordinary transport codes and
the browser's reconnect policy. The planned multi-owner match relay is a
separate protocol (ADR-027). A ticket for an owner's conversation admits
one connection to its run/sign-in stream. A match needs publisher state,
member tickets, host election, checkpoints and metering under ADR-024,
ADR-027 and ADR-032. Reusing the owner socket does not supply those facts.
