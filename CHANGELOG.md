# Changelog

## 0.3.5 — Windows Engine Discovery and Web Recovery (2026-11-11)

- Merged [PR #4](https://github.com/progressrdx/codex-auto-resume/pull/4): discover `codex.exe` in both legacy Windows paths and the newer first-level hash-directory layout. Thanks to [TheM14](https://github.com/TheM14).
- Verify process ownership with the Windows package identity and select a validated executable inside the installation directory. If ownership is ambiguous, require an explicit path.
- Rediscover the engine when Web needs it, so installing or updating the App does not require restarting the dashboard. A manually specified path takes priority; monitoring records remain available when engine discovery is ambiguous.
- Added isolated regressions for Windows engine discovery and Web recovery. Existing real-send and natural-reset evidence applies to earlier versions.

## 0.3.4 — Compatibility and Robustness (2026-11-03)

- Read the actual package version (`__version__`) during the RPC handshake instead of declaring a fixed `0.1.0`.
- Added an English paragraph to the fixed continuation prompt so English Codex sessions keep matching context.
- Removed exact App/CLI version allowlists. Version numbers remain diagnostic; installation identity, live protocol, and required state are still checked. Updates during a connection require reconnecting. Cancellation, budgets, persistent deduplication, and no-retry behavior for uncertain sends remain.
- Added isolated future-version and missing-state refusal tests. This does not validate real sends or natural resets on the latest builds.
- For v11 snapshots, normalize an omitted optional `ephemeral` field to false according to the inspected protocol semantics. Explicit temporary conversations, null/invalid types, missing required state, and unknown protocols remain blocked.
- Added Windows system notifications using PowerShell Forms with `msg.exe` fallback.
- Rotate oversized logs before launching child processes, retaining the most recent 512 KB when a log exceeds 2 MB.
- Added `--list-timeout` to `list`, `check`, and `start`; the default is 55 seconds.
- Added `quota_status` clock-drift protection after 60 consecutive stale results and track the stale count in the controller.
- Clarified why `can_dispatch` uses `<=`: after `claim` inserts a row, a count equal to `max_resumes` must still be allowed.
- Added two isolated quota clock-drift regressions. At release time, the Python suite had 142 cases (141 passed and one Windows-only launcher case skipped); 26 browser tests passed.

## 0.3.3 — Windows Compatibility and Read-Only Diagnostics (2026-10-25)

- Added support for the inspected Windows App `26.1021.3705.0` / CLI `0.164.0-alpha.11.1` pair based on static protocol inspection of the official x64 MSIX. Exact version protections remained in that release; cross-pair sends were not allowed.
- Fixed handling of an omitted optional `ephemeral` field while continuing to reject explicit temporary and unknown states.
- Added unconfirmed-send blocking, environment fingerprints, and checks for version changes during a connection, preserving task ownership, reset checks, cancellation, budgets, and persistent deduplication.
- Unadapted versions could use read-only `doctor`, `list`, and `check`; successful diagnostics did not authorize automatic resumes.
- Python suite: 135 cases (134 passed and one Windows-only launcher case skipped); 26 browser tests passed. Offline cases covered reset waiting, usage re-query, original-task sending, and deduplication.
- The new Windows pair had not completed real-device read-only, real-send, or natural-reset validation. Offline simulations did not establish that the Windows user issue was solved. Earlier Windows 11 natural-reset evidence applies only to the previous pair; see [Windows compatibility evidence](docs/windows-compatibility.md).
- Source archives continued to be distributed through GitHub; official releases required isolated Windows, macOS, and Linux CI checks.

## 0.3.2 — Cross-Platform Release Checks (2026-10-18)

- Fixed macOS path and version tests that incorrectly used the host platform in Windows CI.
- Release tags now require isolated Windows, macOS, and Linux checks before a formal GitHub Release is created.
- Includes bounded live-state retries and macOS safe-stop notifications from 0.3.1, plus the newer macOS compatibility work from 0.3.0.
- Source archives are distributed through GitHub. Isolated tests are not end-to-end natural-reset validation.

## 0.3.1 — Bounded Retries and Reliable Resume State (2026-10-18)

- Added bounded retries when loading live Codex conversation state to reduce failures when state is briefly unavailable.
- Added macOS notifications for safety stops that need user confirmation.
- Preserved pre-send safety checks; persisted history alone is not authorization to send.
- Tests cover retries and stop conditions without describing simulations as real natural-reset validation.

## 0.3.0 — Windows and Newer macOS Support (2026-10-18)

- Added Windows named-pipe IPC, file locking, and background watcher startup.
- Added `resume.cmd` and `resume.ps1` for launching from PowerShell or Command Prompt.
- Automatically locate the Windows Codex App's per-user `codex.exe` and inspect Store/MSIX App and CLI versions.
- Preserved the exact-version protection of that release. The Windows automation path passed isolated tests and one real Windows 11 natural-reset resume on a signed-in ChatGPT Free account.
- Adapted the CLI path and conversation snapshot format for macOS Codex App `26.1002.52244 / build 13536`; `doctor` passed IPC and usage-read checks on that exact build.
- Added isolated regressions for the newer macOS Unix socket protocol. These simulations were not described as real reset/resume validation.

## 0.2.0 — Source-Only Distribution (2026-08-29)

- Standardized distribution on GitHub source checkouts and local manual startup.
- Expanded README guidance for download, startup, dashboard use, configuration, updates, and safety boundaries.
- Kept the Python core, CLI, local Web UI, Codex Skill, and matching tests.
- Removed the native macOS client, mini-program, mobile integration, signing/build scripts, and npm dependencies.
- Bound the dashboard to the local loopback interface; no cross-device access is offered.
- End-to-end validation across a natural usage-reset boundary remained outstanding.

## 0.1.1 — Core Safety Fixes (2026-08-29)

- Recheck cancellation, budget, send intent, and live App state before sending.
- Share the monitoring lock across parent and child processes; failed startup does not leave a false monitoring record.
- Added cross-tab Web write locks, persistent pending-intent protection, and stale-session safeguards.
- Added version checks, release-hygiene checks, and isolated CI.

## 0.1.0 — Experimental Baseline (2026-08-29)

- Implemented the local selected-task resume controller, usage queries, persistent state, and Web dashboard.
