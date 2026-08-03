# Security

Use GitHub private vulnerability reporting for issues that could place users or systems at immediate
risk. Do not include credentials, access tokens, private task transcripts, customer data, or live
host state in public reports.

DarkExec can create Codex tasks with the authority configured by the operator. It is not an
authorization system or general sandbox. Only dispatch requests and targets that are already within
the caller's authority.

Same-turn steering uses an executive-scoped mode-0600 Unix socket that exists only while an
attached product turn is active. It requires exact target and turn identities and is unavailable
during harness or synthetic closeout work.

Automatic trailing closeout copies only bounded user/agent text, failure kinds, changed paths, and
counts into its fresh target-owned task. It never copies raw command or tool output, tool arguments,
attachments, or broad transcript state. Capsule text remains private and can retain sensitive text
the user or agent placed in the source messages. Its digest and exact source, product, harness-task,
and harness-turn identities remain in runtime-owned receipts.
