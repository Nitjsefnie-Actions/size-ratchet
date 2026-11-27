#!/usr/bin/env bash
# Suppression surfaces come from BASE-REF, never the scanned tree.
set -euo pipefail
(( $# == 1 )) || { echo "usage: gitleaks_scan.sh BASE-REF" >&2; exit 2; }
base=$1
temp=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
config="$temp/gitleaks.toml"
rules="$temp/gitleaks-default.toml"
remote="refs/remotes/origin/${base}"
if ! git fetch --no-tags --quiet origin "+refs/heads/${base}:${remote}" 2>"$temp/fetch-error"; then
  # A missing base branch is a configuration error; anything else may be transport.
  if grep -q "couldn't find remote ref" "$temp/fetch-error"; then
    cat "$temp/fetch-error" >&2
    exit 1
  fi
  "$(dirname "$0")/retry.sh" git fetch --no-tags --quiet origin "+refs/heads/${base}:${remote}"
fi
# The default rules minus each allowlist exempting a file by name alone (#172).
python3 - gitleaks-default.toml "$rules" <<'PY'
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
section = re.findall(r'(?ms)^\[allowlist\]\n.*?(?=^\[|\Z)', text)
paths = re.findall(r"(?ms)^paths = \[\n.*?^\]\n", section[0]) if len(section) == 1 else []
if len(paths) != 1:
    sys.exit('gitleaks_scan.sh: the default config has no single global [allowlist] paths array')
text = text.replace(section[0], section[0].replace(paths[0], ''), 1)
text = re.sub(r"(?ms)^\[\[rules\.allowlists\]\]\npaths = \[\n.*?^\]\n(?=\n|\[|\Z)", '', text)
open(sys.argv[2], 'w', encoding='utf-8').write(text)
PY
if git show "${remote}:.gitleaks.toml" > "$config" 2>/dev/null \
    && grep -q '^\[extend\]' "$config" \
    && grep -qE '^useDefault[[:space:]]*=[[:space:]]*true$' "$config"; then
  sed -i -E "s|^useDefault[[:space:]]*=[[:space:]]*true$|path = \"${rules}\"|" "$config"
else
  cp "$rules" "$config"
fi
rm -f -- .gitleaksignore
git show "${remote}:.gitleaksignore" > .gitleaksignore 2>/dev/null || rm -f -- .gitleaksignore
printf '* diff\n' > "$(git rev-parse --git-path info/attributes)"
./gitleaks detect --verbose --redact --ignore-gitleaks-allow --config "$config" \
  --log-opts='--full-history --all --diff-filter=uxdb --diff-merges=first-parent'
