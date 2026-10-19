# Claude / Codex integration — 2026-10-19

The user requested that both agents' changes reach the running VoiceInput and Claude's working branch.

Integrated parents: Codex `8316b77` and Claude `7b8f0d7` (`claude/typeless-ui-conversation-eb5155`).
Claude's merge had included the older GPT implementation `b1b0c82`; the later subscription routing and account UI were still missing there.

## Retained behavior

- Claude: editable shared polishing prompt, signed-in Claude account, removal and migration of AI 指令, and one `llm.settings_from` factory shared by dictation and settings tests.
- Codex: prestarted ephemeral GPT worker, Flash opt-in, measured service ordering, same-family model refresh and rebenchmarking, manual controls, per-service model selection, Fast mode, and the ChatGPT account display from official `account/read`.
- Current user configuration: Claude Haiku → Antigravity Flash → GPT Luna Fast; automatic same-family updates enabled.

## Conflict decisions

- Extended Claude's shared settings factory to carry all current routing fields. Actual dictation and the settings test use exactly the same settings.
- Kept the current three-provider warmup and bounded GPT transport rather than the older GPT-first/20-second implementation. GPT requests remain capped at 10 seconds; Claude's default remains 15 seconds.
- Kept one GPT account row. Identity comes from the official CLI account API; the older auth-file decoding implementation and its duplicate UI are not included.
- Adapted Claude's integration tests to the current model order and official account path; existing account tests cover sign-out and unavailable identity.

Validation: full `unittest discover -s dev -p 'test_*.py'`, including shared settings, prompt propagation, three-provider fallback, config persistence, account display, and removed-command migration. Deployment requires fast-forwarding both the main checkout and Claude checkout to the merge commit, then restarting the main `D:\voice-input\run.pyw` and checking readiness.
