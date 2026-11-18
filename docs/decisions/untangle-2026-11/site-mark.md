# site-mark as built (2026-11-18)

F4 moves the drawing to `WebSite/brand/mark.py`. Both site renderers import it there. Hatch
ships that file at the same import path in the Python wheel. The desktop helpers use the
shared drawing and ICO encoder. The desktop module still chooses the tray destination, so
`generate_icon()` writes beside the installed desktop module, as it does from a checkout.

The daemon image copies this package input before its editable install. A change to
the source is included in the image publish and smoke workflows.

The cutover manifest names the tray and onboarding outputs in the generator, receipt and
checks. They are still shipped and checked today. Retiring them includes regenerating the
receipt; a missing asset in today's tree must still fail parity.

A full render on the old and new source with the same Pillow and host fonts produced
the same SVG, PNG, ICO, ICNS, web manifest and onboarding HTML. The generated React
component changed only its source attribution. Both renders produced mark version
`ef7e1cdb0f17`. Raster encoders and host fonts can differ from those of the checked-in
exports, so the binary assets keep their original bytes.
