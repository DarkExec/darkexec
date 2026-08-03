# Contributing

Issues and pull requests are welcome. Keep changes within DarkExec's narrow ownership: native Codex
task identity, visibility, lifecycle closeout, receipts, installation, and recovery.

Schedulers, product-specific detection, business policy, notification transport, target secrets, and
customer data belong outside this repository.

Run `./scripts/validate.sh` and describe the behavioral claim, proof, compatibility limits, and
rollback for material changes.

Trailing closeout must preserve exact source task/product-turn identity, omit raw tool output from
its bounded capsule, attribute only per-call harness usage, and leave the source task as the sole
continuation owner.
