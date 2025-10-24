# Release Process

This document describes how to create a new release for the Navien NWP500 Home Assistant integration.

## Prerequisites

- Write access to the repository
- All changes merged to main branch
- All tests passing (mypy type checking)
- Updated documentation

## Release Steps

### 1. Update Version Numbers

Update the version in the following files:

- `custom_components/nwp500/manifest.json` - Update the `version` field
- `CHANGELOG.md` - Add new version section with date and changes

**Important:** Always get the current date using:
```bash
date +%Y-%m-%d
```

### 2. Update CHANGELOG.md

Add a new section for the version following this format:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features

### Changed
- Changes to existing functionality

### Fixed
- Bug fixes

### Removed
- Removed features
```

Update the comparison links at the bottom:
```markdown
[Unreleased]: https://github.com/eman/ha_nwp500/compare/vX.Y.Z...HEAD
[X.Y.Z]: https://github.com/eman/ha_nwp500/releases/tag/vX.Y.Z
```

### 3. Commit Changes

```bash
git add custom_components/nwp500/manifest.json CHANGELOG.md
git commit -m "Release vX.Y.Z"
git push origin main
```

### 4. Create and Push Tag

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

### 5. Automated Release

Once the tag is pushed, the GitHub Actions workflow (`.github/workflows/release.yml`) will automatically:

1. Extract the changelog for the version
2. Create a GitHub release with the changelog as the description
3. Build a ZIP archive of the integration
4. Upload the ZIP archive to the release

### 6. Verify Release

1. Go to https://github.com/eman/ha_nwp500/releases
2. Verify the release was created successfully
3. Check that the ZIP file is attached
4. Review the changelog content

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):

- **MAJOR** version (X.0.0): Incompatible API changes or breaking changes
- **MINOR** version (0.X.0): New functionality in a backward compatible manner
- **PATCH** version (0.0.X): Backward compatible bug fixes

## Hotfix Process

For urgent fixes to a released version:

1. Create a branch from the release tag: `git checkout -b hotfix/vX.Y.Z+1 vX.Y.Z`
2. Make the fix
3. Update version to X.Y.Z+1 in manifest.json
4. Update CHANGELOG.md with the fix
5. Commit and push
6. Create PR and merge to main
7. Follow release steps above with the new version

## Rolling Back a Release

If a release needs to be rolled back:

1. Delete the release on GitHub (do not delete the tag)
2. Mark the release as deprecated in CHANGELOG.md
3. Create a new release with fixes

## Automation Details

The release workflow (`.github/workflows/release.yml`) is triggered by:
- Pushing a tag matching the pattern `v*` (e.g., v0.1.0, v1.2.3)

The workflow:
- Runs on: Ubuntu latest
- Permissions: Requires `contents: write` to create releases
- Creates a ZIP archive named `nwp500-X.Y.Z.zip` containing the integration files
- Extracts changelog content automatically from CHANGELOG.md

## Troubleshooting

### Release workflow fails
- Check GitHub Actions logs for error messages
- Verify CHANGELOG.md formatting is correct
- Ensure tag format matches `v*` pattern

### Changelog extraction fails
- Ensure version section exists in CHANGELOG.md
- Check that version format is `## [X.Y.Z] - YYYY-MM-DD`
- Verify there's content between version headers

### ZIP archive missing or incorrect
- Check that `custom_components/nwp500/` directory structure is correct
- Verify all required files exist in the integration directory
