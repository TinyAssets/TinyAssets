---
severity: P3
title: A signed-out browser that drops the owner sign-in cookie cannot finish its first sign-in
filed: '2026-10-09'
summary: 'Cookie-free owner completion binds the fresh IdP login to the app bearer; with no bearer and no refresh cookie there is nothing safe to bind to, so the browser must allow the __Host-ta-owner-login cookie'
---

# First sign-in still needs the flow cookie

Found in the Claude review of #4569. The owner sign-in completes without the
`__Host-ta-owner-login` cookie only when the page already holds a signed-in
bearer (or can refresh one): the fresh AuthKit identity must equal that user.
A browser with neither, which also drops the flow cookie on the cross-site
return, has nothing safe to bind the login to. It now shows an explicit
"this browser blocked the sign-in cookie" message instead of looping.

Resolve by finding why real Chrome dropped the cookie in the founder's case
(the refusal log now records cookie names and fetch metadata), or by a binding
that needs neither a cookie nor a bearer.
