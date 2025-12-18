#!/bin/bash
set -e

# Release script for ha_nwp500 integration
# Usage: ./scripts/release.sh [patch|minor|major]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get current version from manifest.json
CURRENT_VERSION=$(jq -r '.version' custom_components/nwp500/manifest.json)
echo -e "${GREEN}Current version: ${CURRENT_VERSION}${NC}"

# Determine next version
if [ $# -eq 0 ]; then
    echo -e "${YELLOW}No version bump type specified. Current: ${CURRENT_VERSION}${NC}"
    read -p "Enter new version (e.g., 0.1.3): " NEW_VERSION
else
    IFS='.' read -r -a VERSION_PARTS <<< "$CURRENT_VERSION"
    MAJOR="${VERSION_PARTS[0]}"
    MINOR="${VERSION_PARTS[1]}"
    PATCH="${VERSION_PARTS[2]}"

    case "$1" in
        patch)
            PATCH=$((PATCH + 1))
            ;;
        minor)
            MINOR=$((MINOR + 1))
            PATCH=0
            ;;
        major)
            MAJOR=$((MAJOR + 1))
            MINOR=0
            PATCH=0
            ;;
        *)
            echo -e "${RED}Invalid bump type. Use: patch, minor, or major${NC}"
            exit 1
            ;;
    esac
    NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
fi

echo -e "${GREEN}New version: ${NEW_VERSION}${NC}"

# Get current date
RELEASE_DATE=$(date +%Y-%m-%d)

# Check if there are uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo -e "${RED}Error: Uncommitted changes detected. Please commit or stash them.${NC}"
    exit 1
fi

# Check if we're on main branch
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo -e "${YELLOW}Warning: Not on main branch (currently on: ${CURRENT_BRANCH})${NC}"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update manifest.json
echo -e "${GREEN}Updating manifest.json...${NC}"
jq ".version = \"${NEW_VERSION}\"" custom_components/nwp500/manifest.json > /tmp/manifest.json
mv /tmp/manifest.json custom_components/nwp500/manifest.json

# Update CHANGELOG.md
echo -e "${GREEN}Updating CHANGELOG.md...${NC}"
# Replace ## [Unreleased] with ## [Unreleased]\n\n## [NEW_VERSION] - DATE
sed -i "s/## \[Unreleased\]/## [Unreleased]\n\n## [${NEW_VERSION}] - ${RELEASE_DATE}/" CHANGELOG.md

# Update changelog links at bottom
# Find the current [Unreleased] link and update it
PREV_VERSION=$CURRENT_VERSION
sed -i "s|\[Unreleased\]: .*|\[Unreleased\]: https://github.com/eman/ha_nwp500/compare/v${NEW_VERSION}...HEAD\n[${NEW_VERSION}]: https://github.com/eman/ha_nwp500/compare/v${PREV_VERSION}...v${NEW_VERSION}|" CHANGELOG.md

# Run type checking
echo -e "${GREEN}Running type checking...${NC}"
if ! .venv/bin/tox -e mypy; then
    echo -e "${RED}Type checking failed. Please fix errors before releasing.${NC}"
    exit 1
fi

# Show changes
echo -e "${GREEN}Changes to be committed:${NC}"
git diff custom_components/nwp500/manifest.json CHANGELOG.md

# Confirm
read -p "Create release v${NEW_VERSION}? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Release cancelled. Rolling back changes...${NC}"
    git checkout custom_components/nwp500/manifest.json CHANGELOG.md
    exit 1
fi

# Commit and tag
echo -e "${GREEN}Creating commit and tag...${NC}"
git add custom_components/nwp500/manifest.json CHANGELOG.md
git commit -m "chore: Release v${NEW_VERSION}"
git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"

echo -e "${GREEN}✓ Release v${NEW_VERSION} created locally${NC}"
echo -e "${YELLOW}To publish, run:${NC}"
echo -e "  git push && git push --tags"
echo -e ""
echo -e "${YELLOW}Note: GitHub Actions will automatically create the release when the tag is pushed.${NC}"
