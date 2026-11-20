# Public site read status

- [x] Reviewed the public fine-print route, its reachability component,
  browser reader and boundary tests on main f934871ad. All 162 heads in
  the comparison have the same reader and component. Two historical site
  branches have different fine-print pages. The mark and Connect changes
  also touch the boundary test. Keep their checks in the single site release.
- [x] On Node 22 and Node 24, two calls to the actual reader made zero
  network requests and returned two different read timestamps. The public
  component initially says it is reading from the browser and shows that
  timestamp after hydration. The protected reader always reports that
  sign-in is required. A new regression failed on main with 40 other site
  checks passing. The export check also requires the sign-in message on
  the initially served page.
- [ ] Render the known sign-in boundary directly. Verify the exported page
  before hydration and preserve the separate Commons snapshot and the
  authenticated connector boundary. Recheck with the other staged site work.
- [x] The public route now renders its sign-in notice as ordinary static
  markup. Removed its sole client effect and the private site module's
  unused synthetic reading, including its timestamp and activity fields.
  The Commons consumer still labels its checked-in snapshot. The other
  public list reader still refuses the unauthenticated call. On the first
  test run the production-site guard still required the removed flag.
  Its check now verifies the real list refusal alongside its existing
  no-request guard. All 41 site tests passed standalone and all 41
  passed with the Connect, profiles and mark changes. Static export and
  browser acceptance are recorded separately.
