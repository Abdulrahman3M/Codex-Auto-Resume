# Latest Stable Compatibility Check (2026-11-03)

## Summary

The optional `ephemeral` field in the v11 snapshot is now normalized to false when omitted, after verifying the local original task owner and state protocol. Explicit `null` or `true`, invalid types, and missing required safety state still block sending. Static protocol inspection and offline regression checks passed. Real-device sending and natural usage-reset validation for the newly inspected builds remain incomplete.

## Official Versions and Static Evidence

| Platform | Inspected version | Source |
| --- | --- | --- |
| Windows x64 | MSIX `26.1027.6604.0`; bundled app `26.1027.51404 / build 23880` | [Official release manifest](https://persistent.oaistatic.com/codex-app-prod/windows-release.json), MSIX AppxManifest and package.json |
| macOS arm64 | `26.1027.61615 / build 24141` | [Official appcast](https://persistent.oaistatic.com/codex-app-prod/appcast.xml), ZIP Info.plist |

These values represent the stable builds returned by the official sources at the time of inspection, not preview channels or staged rollouts to other accounts.

The `app.asar` files were extracted from official installers without installing or running the newly downloaded apps:

- Windows [versioned x64 MSIX](https://persistent.oaistatic.com/codex-app-prod/releases/26.1027.6604.0/ChatGPT-x64.msix), SHA-256: `faacb124f33b792bd2c88fbe8b1215fb6869d81b145a919260e16ac11d9c1451`.
- macOS [arm64 ZIP](https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.1027.61615.zip), SHA-256: `bb88615052255e79b5977885f9a0cc212c268589587c79d4d3b7c4b46e881a95`.
- Both packages declare state broadcast v11, owner discovery v1, follow-state v1, and resume request v2 in `.vite/build/early-bootstrap.js`.
- The resume path still calls `startTurn(conversationId, turnStart)` on the original conversation and returns a nested result.
- The optional `ephemeral` value is copied directly; JSON serialization omits `undefined`, and the App still identifies temporary conversations with `=== true`.
- Windows IPC remains `\\.\pipe\codex-ipc` with a four-byte little-endian length prefix.

## Checks Performed

1. The installed Mac build `26.1023.50603 / build 22391` passed a real read-only `doctor` connection and usage query. It is not the newly inspected Mac build and does not count as validation of that build.
2. Before the fix, synthetic states with the latest Windows and Mac version metadata stopped when `ephemeral` was missing; no simulated resume was sent. Explicit `false` was eligible under the policy.
3. After the fix, the same missing optional field passed full simulated request/response flows for both builds, each producing one simulated resume request and turn ID. Future version metadata follows the same protocol rule. The Mac path also passed a full exchange over a real Unix socket with a simulated App.
4. Refusal regressions cover explicit `true`, `null`, and invalid field types; missing hostId, resumeState, requests, or history; and protocol changes. Approval, budget, cancellation, and deduplication regressions remain in place.
5. These are offline simulations. The Windows CLI was not run on macOS, and no real send or natural usage-reset test was performed on the newly inspected builds.

## Scope of the Fix

The optional-field behavior follows the inspected v11 protocol semantics and does not require adding every App version to a list. It applies only to a complete local v11 snapshot from the original task owner, not history queries or unknown protocols. Missing fields are not generally treated as safe: temporary-session checks, approval state, original-task verification, cancellation, budgets, persistent deduplication, and no-retry behavior for uncertain sends remain required.
