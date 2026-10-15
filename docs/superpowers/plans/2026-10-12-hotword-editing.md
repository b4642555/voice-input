# Hotword editing implementation plan

Goal: Add and edit hotwords in Settings; use changes on the next utterance.
Architecture: Settings edits the shared Hotwords store. Rename is a single version-checked transaction. App supplies active model vocabulary validation.

Constraints: Preserve right Alt, word caps and single-character smart picks. Temporary test data only. Restart after committing.

- [x] Add failing store/UI tests for Enter, rename/date preservation, duplicate rejection, write failure and next-read bias.
- [x] Add `Hotwords.rename(old, new) -> bool`; validate, reload, change one item, save and rollback on failure.
- [x] Add Settings input/add button, per-row edit and validation feedback.
- [x] Run isolated regressions and inspect the UI (91 tests and isolated Qt rendering).
- [x] Investigate removed hand-edit learning for correction-and-submit; track only dictation corrections and test unrelated edits/focus changes.

User approved the add/edit design on 2026-10-12.


Correction learning is now implemented with verified field snapshots and a submit/clear state machine. See docs/manual-correction-learning.md for explicit support boundaries. Manual UI was reviewed and rendered with isolated data. Final regression, review and restart are tracked in this task.
