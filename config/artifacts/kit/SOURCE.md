# Report kit v1: sources of the vendored files

The kit is served by the platform (gateway path `/report-kit/v1/`) and inlined into a report's download; nothing is fetched from the internet at runtime (reports PR, I2, ADR-0370). `kit.css` and `kit.js` are this repository's own code. The files below are third-party, copied unmodified from the npm registry; each tarball's sha512 matched the registry's published `integrity` when it was fetched (2026-10-28). A test (services/agent/tests/unit/test_report_kit.py) holds each file's SHA-256.

| File | Source | Licence | SHA-256 |
|---|---|---|---|
| vendor/echarts.min.js | echarts 5.5.1, `package/dist/echarts.min.js`; tarball sha512-Fce8upazaAXUVUVsjgV6mBnGuqgO+JNDlcgF79Dksy4+wgGpQB2lmYoO4TSweFg/mZITdpGHomw/cNBJZj1icA== (matched the registry's `integrity` when fetched, 2026-10-29) | Apache-2.0, vendor/ECHARTS-LICENSE.txt and vendor/ECHARTS-NOTICE.txt | e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf |
| fonts/ibm-plex-sans-arabic-arabic-400-normal.woff2 | @fontsource/ibm-plex-sans-arabic 5.2.9, `package/files/`; tarball sha512-mAs+anYcmatmhTuYApDy/CEEu5iYcfalXp2DB1gBN/6o2ex24lcBs635bwtgV5BNbByCYslIGsQvD4X6s7eVgw== | SIL OFL 1.1, fonts/OFL.txt | 6010e7fd0dce5d527583951750728cbe3c895efbd16aa0f809ab8c824878c9d8 |
| fonts/ibm-plex-sans-arabic-arabic-500-normal.woff2 | same | same | 90aef64fea9794f232332e907d45810ab268d1a11721340025eba2a2cdb36d5c |
| fonts/ibm-plex-sans-arabic-arabic-600-normal.woff2 | same | same | 16734a5adb27b0f363e566cbbeacec480da0dc0baa19c8f0053251c2e2bc0eac |
| fonts/ibm-plex-sans-arabic-arabic-700-normal.woff2 | same | same | 04c730b4292731ccedadd5bd80756124364ae4923895145d9cd7b1f28e443de1 |
| fonts/ibm-plex-sans-arabic-latin-400-normal.woff2 | same | same | 9ed8dcb02e6c7246de4c120295fafa39a7bb73085f4951a4524a07dc911069f2 |
| fonts/ibm-plex-sans-arabic-latin-500-normal.woff2 | same | same | be6a3b2e37f3ad67aa822a55ce356d28c416331c97530a1ec076c2118240ca2d |
| fonts/ibm-plex-sans-arabic-latin-600-normal.woff2 | same | same | 63f4757271e403f7baec0862f284ffaa4560ea6096ba9fe8bc6e585ca656e724 |
| fonts/ibm-plex-sans-arabic-latin-700-normal.woff2 | same | same | ae2d59f91ecf9f7ed279e4a1fc1a9da8170fb0ce5b9152917a8f759033f5777c |

The categorical palette in `kit.css` follows the platform's identity (ADR-0415): the brand green and gold first, then blue, orange, teal, olive, purple and rose, in light and dark steps; the order was chosen for the largest adjacent-pair colour difference under normal, deuteranopic and protanopic vision (worst ΔE 33 light, 21 dark, the dark green and gold under protanopia). The page's surfaces, ink, grid and accent are the identity's tokens.
