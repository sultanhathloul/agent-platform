#!/bin/sh
# Prints the totals of the JUnit XML reports in one directory (phase 03 rule G1: counts, not "BUILD SUCCESSFUL").
# Usage: junit-totals.sh <label> <build/test-results/<task>>
label=$1 dir=$2
cat "$dir"/*.xml 2>/dev/null | awk -v label="$label" '
  /<testsuite / {
    for (i = 1; i <= NF; i++) {
      split($i, kv, "=")
      gsub(/"/, "", kv[2])
      if (kv[1] == "tests") t += kv[2]
      if (kv[1] == "failures") f += kv[2]
      if (kv[1] == "errors") e += kv[2]
      if (kv[1] == "skipped") s += kv[2]
    }
  }
  END { printf "JUNIT %s tests=%d passed=%d failed=%d errors=%d skipped=%d\n", label, t, t - f - e - s, f, e, s }'
