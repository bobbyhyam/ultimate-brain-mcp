#!/usr/bin/env bash
set -euo pipefail

# Release script for ultimate-brain-mcp
# Usage: ./scripts/release.sh <major|minor|patch> "<commit message>" [--dry-run]

PYPROJECT="pyproject.toml"
DRY_RUN=false

# --- Parse arguments ---
BUMP_TYPE=""
COMMIT_MSG=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
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
    echo "Usage: ./scripts/release.sh <major|minor|patch> \"<commit message>\" [--dry-run]"
    exit 1
fi

if [[ -z "$COMMIT_MSG" ]]; then
    echo "Error: commit message required"
    echo "Usage: ./scripts/release.sh <major|minor|patch> \"<commit message>\" [--dry-run]"
    exit 1
fi

CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo "Error: must be on main branch (currently on '$CURRENT_BRANCH')"
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

# --- Check tag doesn't already exist ---
if git tag -l "$TAG" | grep -q "^${TAG}$"; then
    echo "Error: tag $TAG already exists"
    exit 1
fi

echo "Bumping $CURRENT_VERSION → $NEW_VERSION"

# --- Update pyproject.toml ---
sed -i '' "s/^version = \"${CURRENT_VERSION}\"/version = \"${NEW_VERSION}\"/" "$PYPROJECT"

# --- Git operations ---
git add -A
git commit -m "$COMMIT_MSG"
git tag "$TAG"

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete — commit and tag created locally, skipping push"
else
    git push origin main
    git push origin "$TAG"

    # Print GitHub Actions URL
    REMOTE_URL=$(git remote get-url origin)
    # Handle both SSH and HTTPS remote formats
    REPO_SLUG=$(echo "$REMOTE_URL" | sed -E 's#(https://github\.com/|git@github\.com:)##; s/\.git$//')
    echo "Release pushed! Monitor the publish workflow:"
    echo "  https://github.com/${REPO_SLUG}/actions"
fi

echo "Done: $TAG"
