# Development Boundaries

This project is an experimental local resume tool for the Codex App. Use a single agent, make focused changes, and verify them in practice. Do not start a formal team workflow.

Parallel, independent audits may be used only when the user explicitly requests them. Each auditor must first reproduce the issue read-only; the primary agent then assigns non-overlapping fix scopes for independent review. This does not start a formal SDLC process.

- Do not bypass account limits, login, or approvals. Do not use reset credits or paid alternatives.
- Manage only local tasks explicitly selected by the user. Never scan for and resume all unfinished tasks automatically.
- Official query connections are read-only. App message writes are permitted only for resuming the original task after its state has been verified. For an explicitly monitored task, a verified link to its original conversation may be loaded to restore the connection. Do not prefill or send another prompt, start a new thread, or take over ownership. Historical state is for filtering only; verify live state again before sending.
- Never resend automatically when the send outcome is uncertain. Preserve persistent deduplication, cancellation, and budget checks.
- Do not infer failure causes by fuzzy matching conversation text such as “continue” or “quota.”
- App/CLI versions are diagnostic only and must not be used as an exact-version allowlist that blocks use. Preserve runtime protocol checks, required state fields, and update checks during a connection. Never treat missing safety state as permission to send.
- After changes, run `python3 -m unittest discover -s tests -v`. Real write tests require explicit authorization and a side-effect-free test task; never send test messages in a user's business task.
- Keep offline simulation, real read-only validation, real-send validation, and natural usage-reset validation distinct. They are not substitutes for one another.
- For frontend changes, also run `node --test tests/test_ui.cjs` and `node --check codex_resume/static/app.js`. Async checks must cover stale success, stale failure, and selecting the same UUID again.
