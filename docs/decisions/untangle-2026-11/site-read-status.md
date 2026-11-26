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
- [x] On 8c25e159 the standalone Node 24 export and the combined Node 20
  export both completed. In both HTML responses the sign-in notice was
  present before hydration, with no browser read claim or read time.
  The client manifest retained only the component's stylesheet.
  The standalone sweep advanced past the static regression and stopped
  before navigation because the Playwright Chromium binary was missing.
  Browser acceptance and the coordinated route release remain open.

### November 26 combined browser verification

On this branch's ef837894 with the Connect and developer profile stages
and main d662e63e, the combined export passed all 41 site tests. Chromium
read the receipt at 390 and 1280 pixels with JavaScript enabled and
disabled. In all four cases its only reading was “sign-in required”
and it displayed no read time. See the combined browser record at
8f4be08c and the separate site reporting dependency at ff356e0a.
The coordinated release and its final route verification remain open.
