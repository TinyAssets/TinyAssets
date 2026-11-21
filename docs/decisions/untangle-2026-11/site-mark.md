# site-mark as built (2026-11-18)

F4 copies the drawing to `WebSite/brand/mark.py`, the mark's one source from now on. Both
site renderers, the site's tests and the brand parity check read it there. The old stack is
fixes only, so `tinyassets/desktop/icon_gen.py` stays unedited for the old desktop tray, and
the cutover manifest deletes it with the old stack. Nothing outside `tinyassets/` reads it,
and the Python wheel and daemon image are unchanged.

The cutover manifest names the tray and onboarding outputs in the generator, receipt and
checks. They are still shipped and checked today. Retiring them includes regenerating the
receipt; a missing asset in today's tree must still fail parity.

A full render on the old and new source with the same Pillow and host fonts produced
the same SVG, PNG, ICO, ICNS, web manifest and onboarding HTML. The generated React
component changed only its source attribution. Both renders produced mark version
`ef7e1cdb0f17`. Raster encoders and host fonts can differ from those of the checked-in
exports, so the binary assets keep their original bytes.
