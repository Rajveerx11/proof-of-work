#!/usr/bin/env bash
set -euo pipefail

# Install only the built wheel and its locked runtime dependencies. Running outside
# the checkout prevents local sources or an editable installation masking omissions.
project_root="$(pwd)"
smoke_root="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/pow-wheel-smoke.XXXXXX")"
trap 'rm -rf "$smoke_root"' EXIT
uv export --quiet --frozen --no-dev --no-emit-project --output-file "$smoke_root/requirements.txt"
uv venv --python 3.12 "$smoke_root/venv"
uv pip sync --python "$smoke_root/venv/bin/python" "$smoke_root/requirements.txt"
uv pip install --python "$smoke_root/venv/bin/python" --no-deps "$project_root"/dist/*.whl
cd "$smoke_root"
"$smoke_root/venv/bin/python" -I -c 'import proofofwork; print(proofofwork.__version__)'
"$smoke_root/venv/bin/proof-of-work" --help
"$smoke_root/venv/bin/proof-of-work" eval report --format html --output report.html
test -s report.html
