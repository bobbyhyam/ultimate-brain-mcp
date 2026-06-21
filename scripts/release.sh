#!/usr/bin/env bash
set -euo pipefail

# PR-based release script for ultimate-brain-mcp.
#
# Usage: ./scripts/release.sh <major|minor|patch> "<commit message>" [--dry-run] [--no-wait]
#
# What it does:
#   1. Bumps the version in pyproject.toml.
#   2. Rolls CHANGELOG.md's [Unreleased] section into a dated [X.Y.Z] section
#      and refreshes the compare links.
#   3. Runs `uv lock` so uv.lock's own-package version matches pyproject
#      (CI runs `uv sync --frozen`, which fails on any mismatch).
#   4. Commits on a release/vX.Y.Z branch, pushes, and opens an auto-merge PR
#      so the release commit goes through CI before landing on main.
#   5. Waits for the PR to merge, then tags vX.Y.Z and pushes the tag, which
#      triggers .github/workflows/publish.yml (PyPI publish + skill asset).
#
# Flags:
#   --dry-run   Create the release branch + commit locally; skip push/PR/tag.
#   --no-wait   Open the auto-merge PR but don't block waiting for the merge;
#               print the tag command to run once it lands.
#
# Requirements: clean tree on main, `gh` authenticated, auto-merge enabled on
# the repo (Settings -> General -> Allow auto-merge).

PYPROJECT="pyproject.toml"
CHANGELOG="CHANGELOG.md"
DRY_RUN=false
NO_WAIT=false
WAIT_SECONDS=1200

# --- Parse arguments ---
BUMP_TYPE=""
COMMIT_MSG=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --no-wait) NO_WAIT=true ;;
        major|minor|patch)
            if [[ -z "$BUMP_TYPE" ]]; then
                BUMP_TYPE="$arg"
            else
                COMMIT_MSG="$arg"
            fi
            ;;
        *) COMMIT_MSG="$arg" ;;
    esac
done

# --- Validation ---
if [[ -z "$BUMP_TYPE" ]]; then
    echo "Error: bump type required (major|minor|patch)"
    echo "Usage: ./scripts/release.sh <major|minor|patch> \"<commit message>\" [--dry-run] [--no-wait]"
    exit 1
fi

if [[ -z "$COMMIT_MSG" ]]; then
    echo "Error: commit message required"
    echo "Usage: ./scripts/release.sh <major|minor|patch> \"<commit message>\" [--dry-run] [--no-wait]"
    exit 1
fi

CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo "Error: must be on main branch (currently on '$CURRENT_BRANCH')"
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working tree is not clean. Commit or stash changes first."
    exit 1
fi

# --- Read current version ---
CURRENT_VERSION=$(grep -m1 '^version' "$PYPROJECT" | sed 's/version = "\(.*\)"/\1/')
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

# --- Compute new version ---
case "$BUMP_TYPE" in
    major) NEW_VERSION="$((MAJOR + 1)).0.0" ;;
    minor) NEW_VERSION="${MAJOR}.$((MINOR + 1)).0" ;;
    patch) NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))" ;;
esac

TAG="v${NEW_VERSION}"
BRANCH="release/${TAG}"
RELEASE_DATE=$(date +%F)

# --- Derive the GitHub repo slug (owner/repo) from the origin remote ---
REMOTE_URL=$(git remote get-url origin)
REPO_SLUG=$(echo "$REMOTE_URL" | sed -E 's#(https://github\.com/|git@github\.com:)##; s/\.git$//')

# --- Preflight checks ---
if git tag -l "$TAG" | grep -q "^${TAG}$"; then
    echo "Error: tag $TAG already exists"
    exit 1
fi
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    echo "Error: branch $BRANCH already exists"
    exit 1
fi

echo "Releasing $CURRENT_VERSION -> $NEW_VERSION (tag $TAG) on $RELEASE_DATE"

# --- Roll the CHANGELOG: [Unreleased] -> dated [NEW_VERSION] + fresh Unreleased ---
roll_changelog() {
    if [[ ! -f "$CHANGELOG" ]]; then
        echo "warn: $CHANGELOG not found, skipping changelog roll"
        return 0
    fi
    if ! grep -q '^## \[Unreleased\]' "$CHANGELOG"; then
        echo "warn: no '## [Unreleased]' heading in $CHANGELOG, skipping changelog roll"
        return 0
    fi

    # Insert a dated version heading immediately after the first [Unreleased]
    # heading. Whatever sat under [Unreleased] now belongs to this version.
    awk -v ver="$NEW_VERSION" -v date="$RELEASE_DATE" '
        /^## \[Unreleased\]/ && !done {
            print
            print ""
            print "## [" ver "] - " date
            done = 1
            next
        }
        { print }
    ' "$CHANGELOG" > "${CHANGELOG}.tmp" && mv "${CHANGELOG}.tmp" "$CHANGELOG"

    # Refresh the link references at the bottom (if present): repoint the
    # Unreleased compare to the new tag and add a line for the new version.
    if grep -q '^\[Unreleased\]:' "$CHANGELOG"; then
        awk -v ver="$NEW_VERSION" -v prev="$CURRENT_VERSION" -v slug="$REPO_SLUG" '
            /^\[Unreleased\]:/ {
                print "[Unreleased]: https://github.com/" slug "/compare/v" ver "...HEAD"
                print "[" ver "]: https://github.com/" slug "/compare/v" prev "...v" ver
                next
            }
            { print }
        ' "$CHANGELOG" > "${CHANGELOG}.tmp" && mv "${CHANGELOG}.tmp" "$CHANGELOG"
    fi
}

# --- Create the release branch and apply changes ---
git checkout -b "$BRANCH"

# Bump pyproject.toml (portable in-place edit via temp file).
sed "s/^version = \"${CURRENT_VERSION}\"/version = \"${NEW_VERSION}\"/" "$PYPROJECT" > "${PYPROJECT}.tmp"
mv "${PYPROJECT}.tmp" "$PYPROJECT"

roll_changelog

# Keep uv.lock's own-package version in sync (CI uses --frozen).
uv lock

git add -A
git commit -m "$COMMIT_MSG"

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete - branch '$BRANCH' and commit created locally."
    echo "Review with: git show; git diff main...$BRANCH"
    echo "Discard with: git checkout main && git branch -D $BRANCH"
    exit 0
fi

# --- Push, open an auto-merge PR ---
git push -u origin "$BRANCH"

PR_BODY="Release ${TAG}.

Once this merges, \`${TAG}\` is tagged to trigger the PyPI publish + skill-archive workflow.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"

gh pr create --base main --head "$BRANCH" --title "$COMMIT_MSG" --body "$PR_BODY"
PR_NUM=$(gh pr view "$BRANCH" --json number --jq '.number')
gh pr merge "$PR_NUM" --squash --auto --delete-branch

finish_with_tag() {
    git checkout main
    git pull --ff-only
    git tag "$TAG"
    git push origin "$TAG"
    echo "Release pushed! Monitor the publish workflow:"
    echo "  https://github.com/${REPO_SLUG}/actions"
    echo "Done: $TAG"
}

manual_tag_hint() {
    echo "Once PR #${PR_NUM} merges, finish the release with:"
    echo "  git checkout main && git pull --ff-only && git tag $TAG && git push origin $TAG"
}

if [[ "$NO_WAIT" == true ]]; then
    echo "Auto-merge queued on PR #${PR_NUM} (will merge when CI passes)."
    manual_tag_hint
    exit 0
fi

# --- Wait for the PR to merge, then tag ---
echo "Waiting up to $((WAIT_SECONDS / 60))m for PR #${PR_NUM} to auto-merge (CI must pass)..."
deadline=$((SECONDS + WAIT_SECONDS))
while true; do
    STATE=$(gh pr view "$PR_NUM" --json state --jq '.state' 2>/dev/null || echo "")
    if [[ "$STATE" == "MERGED" ]]; then
        echo "PR #${PR_NUM} merged."
        finish_with_tag
        break
    fi
    if [[ "$STATE" == "CLOSED" ]]; then
        echo "Error: PR #${PR_NUM} was closed without merging."
        exit 1
    fi
    if (( SECONDS >= deadline )); then
        echo "Timed out waiting for merge; the PR is still open with auto-merge queued."
        manual_tag_hint
        exit 1
    fi
    sleep 10
done
