# Windows Compatibility Evidence and Validation Limits

## Inspected Build

On 2026-10-25, the official x64 installer [ChatGPT-x64.msix](https://persistent.oaistatic.com/codex-app-prod/ChatGPT-x64.msix) was inspected. The URL changes with official releases; these notes apply only to the extracted package examined at that time.

- AppxManifest identity: `OpenAI.Codex`, version `26.1021.3705.0`, executable `app/ChatGPT.exe`.
- Bundled `codex.exe` version marker: `0.164.0-alpha.11.1`. This was a static binary inspection; `--version` was not run on Windows.
- `app.asar` SHA-256: `04862a659c6a90fc50514d8c0b8016709d97dece58839726c57495ed72f1d9f0`.
- `codex.exe` SHA-256: `1103e9e09c71fa2cd0afd6d8f3b9a2d63174fe8efd7cb5bd09f1cb14ef58a098`.
- The bundled `package.json` version differs from the MSIX version and is not used to identify the Windows App.

## Protocol Inspection

The package files `.vite/build/src-Dl21kCYQ.js`, `main-BPBEjAxi.js`, and related bundles were inspected:

- The named pipe remains `\\.\pipe\codex-ipc`; JSON frames use a four-byte little-endian length prefix.
- Protocol versions: `thread-owner-discovery` v1, `thread-stream-following-changed` v1, `thread-stream-state-changed` v11, and `thread-follower-start-turn` v2.
- Sends still target the original task owner with `conversationId` and `turnStart.request`. `request.threadId` must match the task; omitted `context` inherits the original task settings.
- State still provides canonical `turnHistory`, `requests`, `hostId`, and `resumeState`.
- When optional `thread.ephemeral` is undefined, JSON omits it and the App treats only `=== true` as temporary. The implementation follows the inspected v11 snapshot semantics and normalizes this missing optional field to false without tying behavior to one exact App/CLI pair. Explicit `null` or `true` is still rejected.
- The App blocks sends when `unconfirmedTurnSubmissions` is present. This tool does too, including non-empty records marked terminal, to prevent an uncertain send from being attempted again.

This is compatibility evidence for a historical build, not a version allowlist. App/CLI versions are diagnostic metadata; versions are not rejected solely because they are new, old, or an unlisted pair. Sending still requires live protocol and state checks. Unknown protocols, missing required state, or a version change during a connection cancel sending. Future protocol compatibility has not been established.

See [the latest stable compatibility check](latest-compatibility-check.md) for the latest package inspection and optional-field change.

## Validation Levels

| Check | Inspected build |
| --- | --- |
| Static protocol inspection of the official x64 installer | Complete |
| Offline frame, state, request, and response simulation | Complete |
| Offline reset wait, usage re-query, single send, and deduplication | Complete |
| Real Windows named-pipe and read-only query | Not completed |
| Real send to the original task on Windows | Not completed |
| Automatic resume after a natural usage reset on Windows | Not completed |

Offline tests are in `tests/test_windows_compatibility.py`; they use synthetic state and simulated pipes and do not connect to a real account. They do not prove success on a Windows device. The previous README record of a successful Windows 11 natural reset applies to the older pair `26.901.31953.0` / `0.153.1`, not the inspected build documented here.

Before closing a user report, verify in native Windows with an explicitly authorized, side-effect-free test task: `resume.cmd doctor` reports the exact pair, the original task passes a read-only state check, an authorized resume gets a new turn ID, a restart does not send again, and one natural usage reset is observed. Never use a business task for a test send.
