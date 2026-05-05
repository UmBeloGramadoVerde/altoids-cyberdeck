#!/usr/bin/env bash
set -euo pipefail

message="${1:-}"

if [ -z "$message" ]; then
	message="Update $(date +%Y-%m-%d_%H-%M-%S)"
fi

git add -A

if git diff --cached --quiet; then
	echo "No changes to commit."
	exit 0
fi

git commit -m "$message"
git push
