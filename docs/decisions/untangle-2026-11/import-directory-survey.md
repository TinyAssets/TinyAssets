# Optional folders in the frozen backup survey

The shared accounts folder and each owner's sessions folder are optional. When
present, each must be a directory. The census and bundle builder both consume
the survey. On f934871ad a regular file in the sessions slot yielded the same
ready result as an absent folder. The dry build still reported three public
owners and left out the unexpected file.

- [x] Use one directory check for both optional locations. Absence is valid.
  An unexpected sessions file adds the ordinary owner-level unknown-entry
  finding. An unexpected shared accounts file keeps its fleet-level scope.
  Propagate storage read errors. Visit each account that the shared directory
  listing already confirmed without a second optional-existence check.
- [x] The ordinary regression failed on main and all 46 census tests passed on
  the candidate. The affected owner is withheld while the two other public
  owners remain eligible. Restoring the original sessions directory restores
  the three-owner result.
- [x] On Node 22 and 24, independent synthetic checks exercised absent,
  normal and wrong-kind sessions and injected a storage I/O error at each
  optional root. Main continued on both errors. The corrected survey stopped
  on each, and avoided the redundant per-account probe.
- [x] All 168 ordinary importer cases and the full platform typecheck passed on
  b1017a39 using Node 22. With the eleven stages in a22b487e, all 211
  ordinary importer cases and full platform types passed using Node 24.
  Each of two healthy frozen-input runs with different exclusion lists produced
  all 41 files byte-identically to main, including private records, on each of
  Node 22 and 24. The same comparisons passed independently on eeee6bba.
- [ ] Historical subsets still need the original full owner list and deletion
  comparison. The staged policy census has a separate source-order dependency.
