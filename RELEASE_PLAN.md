# Release Plan

## Current Distribution

The source is distributed through GitHub. After cloning, launch with `./resume` on macOS/Linux or `resume.cmd` / `resume.ps1` on Windows. No installer, domain, server, database, or separate account is required.

The repository contains the Python core and safety policy, platform launchers, a loopback-only Web dashboard, the repository-level Codex Skill, isolated tests, user documentation, and release-hygiene checks. It does not include native macOS clients, signing/notarization tools, mobile clients, npm dependencies, or remote-control endpoints.

## Release Checks

1. From a clean checkout, run `--help`, `doctor`, and `web` without third-party dependencies.
2. Invoke the platform launcher from outside the checkout to confirm module discovery does not depend on the working directory.
3. Run the Python core and Web regression suite.
4. Run browser script tests and syntax checks.
5. Run Python compilation and tracked-file hygiene checks.
6. Perform real-send validation only with explicit authorization, an isolated test task, and no side effects.
7. Never use a business task as a test or treat offline simulation as natural usage-reset validation.

## Release Procedure

Update the package version and `CHANGELOG.md`, commit the changes, and push the matching `vX.Y.Z` tag. GitHub Actions runs isolated Windows, macOS, and Linux checks for the tag. A formal GitHub Release is created only if all platform checks pass.
