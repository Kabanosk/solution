# Proposed native worker interfaces

These interfaces preserve `scout/loops/context.py`, connector Protocols and the guardrails facade. No loop implementation has started. Shared records/traits will live in plain `rust/domain` (`scout-domain`), so provider/guard implementations and worker code do not depend on each other. Money fields use the shared Decimal representation and serialize as source decimal strings; datetimes preserve UTC and source metadata. Existing jobs/tasks records remain their current crate types.

`JobContext` (in `rust/worker`) holds verified Tenant, Database, JobQueue, TaskRecorder, Arc<Audit>, Arc<HR>, Arc<Llm>, Arc<dyn Notifier>, an Arc<RwLock<Connectors>> refreshed by the registry watcher, Arc<dyn Guard>, explicit WorkerSettings and an injected UTC clock. The forecaster seam accepts tenant transactions/model/options and returns the real native liquidity Review. No global tenant/context fallback exists. WorkerSettings contains the source settings actually used by loops, supplied from root Config; raw environment access belongs to configuration.

All external traits are `Send + Sync`, asynchronous through async_trait, and return `Result<T, RuntimeError>` unless noted. RuntimeError carries `legacy_error_name`, message and retryable metadata so source retry steps stay faithful. Domain DTOs mirror BankTxn, InvoiceDoc, MailHit and ChatHit from scout/models.py, plus Button and search option structs. Bytes remain Vec<u8> and never pass through text JSON.

- Bank: name/label getters; list_transactions(since: DateTime<Utc>) -> Vec<BankTxn>.
- Invoicing: name/label; find(InvoiceSearch { number, supplier, amount, around, days, task_id }) -> Vec<InvoiceDoc>; file(doc, transaction_id, task_id) -> (bool, String), retaining source mismatch reasons and atomic locking.
- Files: name; search(owner, terms, task_id) -> Vec<Value>; read(owner, path, task_id) -> Option<Vec<u8>>.
- Mail: name; available() -> bool; search(terms, since, task_id) -> Vec<MailHit>; attachment(hit_id, name, task_id) -> Option<Vec<u8>>; changes(cursor: Value) -> (Vec<MailHit>, Value). pdf_search is an optional capability exposed explicitly, preserving Gmail behavior instead of checking a Python attribute.
- Chat: name; search(ChatSearch { since, invoice_number, supplier, amount, task_id }) -> Vec<ChatHit>.
- KsefLookup: find(number, task_id, days, attempts) -> Vec<(Value, String)> (parsed document plus KSeF number).
- Notifier: dm(employee_id, text, level, DmOptions { task_id, buttons, thread }) -> Option<String>; update(ref,text,level) -> (); track(task_id,status_ref) -> (); upload(employee_id, Vec<(String,Vec<u8>)>, comment) -> Option<String>. Production Slack implements actual effects; LogNotifier preserves the original explicit unconfigured mode and captures effects for tests.

`Guard` is the already-orchestrated facade, not an HTTP client and not a generic callback. This avoids a guardrails→worker dependency. Its methods return their source shapes directly; remote failures/audit fallbacks are handled within the guardrails implementation:

- screen_human(person: Option<&str>, text, role, task_id) -> Option<String> (None means continue; Some is blocked reply).
- screen_tool(task_id, tool, content, requester, result: Option<&Value>) -> String (content or WITHHELD).
- screen_document(task_id,text,source) -> Verdict { status, activated_rails, latency, detail }, with blocked/failed predicates.
- screen_output(task_id,text,requester,source,role) -> String, preserving masking and OUTPUT_REPLY.
- screen_history(turns: Vec<Value>, task_id, requester) -> Vec<Value>.
- alert_outside(key,label,rails,task_id) -> (); active() -> bool.

Root guardrails construction receives Database/Tenant, Audit, Notifier, clock and the required explicit settings through domain-level dependencies. Its public constants (WITHHELD, OUTPUT_REPLY and configuration IDs) remain exported by the root-owned guardrails crate. Worker loops use only the facade. Root owns provider clients, authorization-generation fencing, registry construction and Slack socket transport; the worker owns lease heartbeat/reaping, dispatch, task/error effects, watchers/scheduling and loop orchestration.

Concrete HR and Audit live behind their own small DB-scoped APIs (`rust/hr` or `domain::hr`, `rust/audit`), independent from worker/provider crates. HR preserves explicit Slack identity maps, names, role codes, inclusive vacations, cycle-safe manager chains and first-available fallback. Audit preserves source permission/salary/task visibility and database records, rather than using a permissive stub. Contracts will be finalized with root before loop implementation.
