# Hosting the console (preview, not production)

`python console/build_console.py <export.json> -o dist/index.html` writes `index.html` and a `_headers` file
next to it. Both come from the same function, so the CSP hashes in the meta tag and in the header never drift. Upload
both. `_headers` is the Cloudflare Pages / static-assets format; any host that can set the same response headers works.

Headers set: Content-Security-Policy (hash-based script and style, `default-src 'none'`, `object-src 'none'`,
`base-uri 'none'`, `form-action 'none'`, `frame-ancestors 'none'`, `require-trusted-types-for 'script'`, no image
source), X-Content-Type-Options nosniff, Referrer-Policy no-referrer, X-Frame-Options DENY, Permissions-Policy (camera,
microphone, geolocation, payment, usb off), COOP and CORP same-origin, HSTS one year, Cache-Control no-cache, and
X-Robots-Tag noindex (preview). Remove noindex only on a deliberate launch.

Turn these Cloudflare features OFF for the host name, because each one rewrites or injects into the page and breaks the
hash-based CSP (or adds a third-party script): Web Analytics auto-injection, Rocket Loader, Email Address Obfuscation,
Auto Minify (HTML, CSS, JS), and any Snippet or Worker that edits HTML. Do not add a custom analytics or chat script.

The page is a fixture viewer for invented data. Do not host an export that contains real client data.

`python -m hadavin_kit.kitcheck <dir>` checks a directory holding `export.json`, `index.html` and `_headers`: the page's inline script and style hashes must be in its CSP, the CSP in `_headers` must equal the page's (plus `frame-ancestors`), the embedded data must equal `export.json`, and no remote URL may appear anywhere.

Never put `demo_only/fixtures/green-testdouble.json` (or a page built from it) in a deploy directory: it is a rendering test double for the green state, and `kitcheck` refuses it (a string match that guards against accident; it can be renamed, so it is not a security control).
