# Report kit v1: sources of the vendored files

The kit is served by the platform (gateway path `/report-kit/v1/`) and inlined into a report's download; nothing is fetched from the internet at runtime (reports PR, I2, ADR-0370). `kit.css` and `kit.js` are this repository's own code. The files below are third-party, copied unmodified from the npm registry; each tarball's sha512 matched the registry's published `integrity` when it was fetched (2026-10-28). A test (services/agent/tests/unit/test_report_kit.py) holds each file's SHA-256.

| File | Source | Licence | SHA-256 |
|---|---|---|---|
| vendor/chart.umd.min.js | chart.js 4.4.4, `package/dist/chart.umd.js` (minified UMD build); tarball sha512-emICKGBABnxhMjUjlYRR12PmOXhJ2eJjEHL2/dZlWjxRAZT1D8xplLFq5M0tMQK8ja+wBS/tuVEJB5C6r7VxJA== | MIT, vendor/CHART-JS-LICENSE.md | fed6a739f8d0f0687174de6cd14745fc0fc7809144ab113d22908a26bf0d7fea |
| fonts/ibm-plex-sans-arabic-arabic-400-normal.woff2 | @fontsource/ibm-plex-sans-arabic 5.2.9, `package/files/`; tarball sha512-mAs+anYcmatmhTuYApDy/CEEu5iYcfalXp2DB1gBN/6o2ex24lcBs635bwtgV5BNbByCYslIGsQvD4X6s7eVgw== | SIL OFL 1.1, fonts/OFL.txt | 6010e7fd0dce5d527583951750728cbe3c895efbd16aa0f809ab8c824878c9d8 |
| fonts/ibm-plex-sans-arabic-arabic-500-normal.woff2 | same | same | 90aef64fea9794f232332e907d45810ab268d1a11721340025eba2a2cdb36d5c |
| fonts/ibm-plex-sans-arabic-arabic-600-normal.woff2 | same | same | 16734a5adb27b0f363e566cbbeacec480da0dc0baa19c8f0053251c2e2bc0eac |
| fonts/ibm-plex-sans-arabic-arabic-700-normal.woff2 | same | same | 04c730b4292731ccedadd5bd80756124364ae4923895145d9cd7b1f28e443de1 |
| fonts/ibm-plex-sans-arabic-latin-400-normal.woff2 | same | same | 9ed8dcb02e6c7246de4c120295fafa39a7bb73085f4951a4524a07dc911069f2 |
| fonts/ibm-plex-sans-arabic-latin-500-normal.woff2 | same | same | be6a3b2e37f3ad67aa822a55ce356d28c416331c97530a1ec076c2118240ca2d |
| fonts/ibm-plex-sans-arabic-latin-600-normal.woff2 | same | same | 63f4757271e403f7baec0862f284ffaa4560ea6096ba9fe8bc6e585ca656e724 |
| fonts/ibm-plex-sans-arabic-latin-700-normal.woff2 | same | same | ae2d59f91ecf9f7ed279e4a1fc1a9da8170fb0ce5b9152917a8f759033f5777c |

The categorical palette in `kit.css` is the validated reference instance of the dataviz method (eight hues in a fixed order, light and dark steps; adjacent-pair colour-vision separation checked).
