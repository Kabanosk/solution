# Native policy core

Plain production crate compiles the exact real implementation `../policy/src/port.rs`, including the saved case tests and Unicode mapping. No PyO3 dependency or duplicate PyInit symbol. `classify(&Value,&Value)` and `context_from_transactions(&[Value],&[Value])` are the native JSON interfaces. Original LLM `parse_rule` is a separate runtime unit. See the policy gate/report and source provenance.
