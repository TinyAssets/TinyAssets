import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, extname, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(here, "..");
const repoRoot = resolve(siteRoot, "../..");
const provenance = JSON.parse(
  readFileSync(resolve(repoRoot, "WebSite/brand/generated-assets.json"), "utf8"),
);

function sha256(path) {
  let data = readFileSync(path);
  if ([".html", ".py", ".svg", ".tsx", ".webmanifest"].includes(extname(path))) {
    data = Buffer.from(data.toString("utf8").replaceAll("\r\n", "\n"));
  }
  return createHash("sha256").update(data).digest("hex");
}

function filesBelow(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? filesBelow(path) : [path];
  });
}

test("every committed mark matches the canonical generator receipt", () => {
  assert.equal(provenance.schema_version, 1);
  assert.equal(provenance.canonical_source, "tinyassets/desktop/icon_gen.py");
  assert.match(provenance.mark_version, /^[0-9a-f]{12}$/);

  for (const [kind, entries] of [
    ["generator", provenance.generators],
    ["generated asset", provenance.generated],
  ]) {
    for (const [path, expected] of Object.entries(entries)) {
      assert.equal(
        sha256(resolve(repoRoot, path)),
        expected,
        `${kind} drifted: ${path}; run python WebSite/brand/render_marks.py`,
      );
    }
  }

  const requiredSurfaces = [
    "WebSite/site-react/public/favicon.ico",
    "WebSite/site-react/public/icon.svg",
    "WebSite/site-react/public/icons/apple-touch-icon.png",
    "WebSite/site-react/public/site.webmanifest",
    "tinyassets/desktop/app.ico",
    "assets/brand/tinyassets-app.ico",
    "assets/brand/tinyassets-app.icns",
    "mobile/resources/icon.png",
    "mobile/resources/android/mipmap-mdpi/ic_launcher.png",
    "docs/ops/play-assets/icon-512.png",
    "docs/ops/play-assets/feature-graphic-1024x500.png",
  ];
  for (const path of requiredSurfaces) {
    assert.ok(provenance.generated[path], `canonical receipt omits ${path}`);
  }

  const androidRoot = resolve(repoRoot, "mobile/resources/android");
  const actualAndroid = filesBelow(androidRoot)
    .filter((path) => path.endsWith(".png"))
    .map((path) => relative(repoRoot, path).replaceAll("\\", "/"))
    .sort();
  const recordedAndroid = Object.keys(provenance.generated)
    .filter((path) => path.startsWith("mobile/resources/android/") && path.endsWith(".png"))
    .sort();
  assert.deepEqual(actualAndroid, recordedAndroid, "Android icon set and receipt must be exact");
});

test("browser and manifest icon URLs carry the generated mark version", () => {
  const component = readFileSync(resolve(siteRoot, "components/TinyAssetsMark.tsx"), "utf8");
  assert.match(
    component,
    new RegExp(`TINYASSETS_MARK_VERSION = "${provenance.mark_version}"`),
  );

  const layout = readFileSync(resolve(siteRoot, "app/layout.tsx"), "utf8");
  assert.match(layout, /manifest: markAsset\("\/site\.webmanifest"\)/);
  assert.match(layout, /url: markAsset\("\/favicon\.ico"\)/);
  assert.match(layout, /url: markAsset\("\/icon\.svg"\)/);
  assert.match(layout, /apple: markAsset\("\/icons\/apple-touch-icon\.png"\)/);

  const webmanifest = JSON.parse(
    readFileSync(resolve(siteRoot, "public/site.webmanifest"), "utf8"),
  );
  assert.ok(webmanifest.icons.length >= 3);
  for (const icon of webmanifest.icons) {
    assert.equal(
      new URL(icon.src, "https://tinyassets.io").searchParams.get("v"),
      provenance.mark_version,
      `manifest icon is not cache-versioned: ${icon.src}`,
    );
  }

  const desktop = JSON.parse(readFileSync(resolve(repoRoot, "desktop-app/package.json"), "utf8"));
  assert.equal(desktop.build.win.icon, "../assets/brand/tinyassets-app.ico");
  assert.equal(desktop.build.mac.icon, "../assets/brand/tinyassets-app.icns");
  assert.equal(desktop.build.linux.icon, "../assets/icon.png");
});
