# GPT Luna subscription polishing

User-approved design: local recognition -> GPT Luna using the existing ChatGPT sign-in -> Claude subscription on failure -> original transcript if both fail. No API billing route. Both providers receive the editable prompt, glossary and enabled screen/caret context.

Implemented: Codex app-server stdin transport, prepared in the background without model inference. Each ephemeral worker handles one dictation and closes, releasing its screen context; a clean standby prepares during the request. GPT gets at most 10 seconds. Unavailable service has a 30-second cooldown (missing login/install: 5 minutes); the settings recheck can retry immediately. Completed, matching thread/turn outputs only. Tool features and MCP servers are disabled for the child. API variables and parent-chat integration variables are removed. The official CLI owns authentication.

Integrated Claude's e949366 editable-prompt commit in the isolated Codex branch. Claude's own worktree remains untouched. The earlier handoff was blocked by Claude's approval review, so implementation and deployment continue in this user-authorized task.

Validation on 2026-10-19:
- 160 unit/UI persistence tests passed (offscreen Qt), including subscription routing, failure cooldown, output isolation, terminal punctuation, prompt propagation, provider changes and custom Claude model preservation.
- 6 actual GPT Luna cases passed: self-correction, on-screen name, homophone, preserving a question without answering, negation, numbered steps.
- GPT times: 6.44, 7.05, 4.39, 4.75, 4.58, 5.09 seconds (mean 5.38). Previous exec adapter took 11-19 seconds.
- Simulated GPT outage -> actual warm Claude: 1.41 seconds, correct self-correction. This validates fallback transport and behavior; no real service outage was induced.
- Main application deployment and settings readback are the next validation step. Actual microphone-to-paste acceptance requires exercising that workflow.

References: https://learn.chatgpt.com/docs/auth ; https://learn.chatgpt.com/docs/app-server . The app-server protocol uses thread/start baseInstructions; this differs from exec config, where model_instructions_file is required.
