#!/usr/bin/env bash
set -euo pipefail

branch="${GITHUB_REF_NAME:?GITHUB_REF_NAME is required}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git add -- data reports health evidence
if git diff --cached --quiet; then
  echo "No output changes to commit."
  exit 0
fi

git commit -m "chore(data): update news monitor outputs"

attempt=1
while test "$attempt" -le 3; do
  if git push origin "HEAD:${branch}"; then
    exit 0
  fi
  if test "$attempt" -eq 3; then
    break
  fi
  echo "Push attempt ${attempt} failed; rebasing before a limited retry."
  if ! git pull --rebase origin "$branch"; then
    git rebase --abort || true
    echo "Rebase conflict requires manual resolution; refusing to overwrite data." >&2
    exit 1
  fi
  attempt=$((attempt + 1))
done

echo "Push failed after 3 attempts." >&2
exit 1
