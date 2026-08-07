# Security

Use GitHub private vulnerability reporting for issues that could place users or systems at immediate risk. Do not include credentials, access tokens, private task transcripts, customer data, or live host state in public reports.

DarkExec can create Codex tasks with the authority configured by the operator. It is not an authorization system or general sandbox. Only dispatch requests and targets that are already within the caller's authority.

Same-turn steering uses an executive-scoped mode-0600 Unix socket that exists only while an attached product turn is active. It requires exact target and turn identities and is unavailable during harness or synthetic closeout work.

Automatic trailing closeout resumes the exact source task and adds only the configured harness prompt. It does not copy the transcript, tool output, attachments, or other task content into a second task.

The editable standard harness prompt is bounded, atomically replaced, and stored in a mode-0600 runtime file beneath a mode-0700 state directory. The read-only fire-drill prompt is fixed separately and cannot be changed through this setting.
