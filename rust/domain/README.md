# Shared runtime contracts

Provider, guardrails and notifier implementations depend on this plain crate; worker orchestration depends on the same interfaces. `src/lib.rs` is the compiled contract approved in `../research/worker-contract.md`.

DTOs mirror Scout BankTxn, InvoiceDoc, MailHit, ChatHit and Button; Decimal amounts serialize as strings, attachments stay raw bytes. Async traits are Send+Sync: Bank, Invoicing, Files, Mail, Chat, KsefLookup, Notifier, Guard, AuditSink and People. RuntimeError preserves the original exception class name separately from message/retryability. Guard facade methods return source shapes directly because failure/audit handling belongs inside guardrails.

`Connectors` contains Arc provider instances; worker registry refresh uses Arc<RwLock<Connectors>>. Clock supports UtcClock and injected closures. Concrete database HR/Audit and external providers remain in their own crates. No permissive provider or guard implementation is supplied here.
