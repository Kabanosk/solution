# App replay

Build: `/home/pawel/hackatons/collabothon2026/solution/rust/guardrails-http-reference/native.sh`

Passed: 17/18

Fallback rate: not reported by this build

## Failing cases

### json-invalid-1

Arguments: ``

stdout:

```diff
- {"requests":[{"authorization":"Bearer test-token","body":{"guardrails":{"config_id":"user-input"},"messages":[],"model":"scout-test"},"path":"/v1/guardrail/checks"}],"values":[{"activated_rails":[],"detail":"JSONDecodeError: Expecting value: line 1 column 1 (char 0)","status":"unavailable"}]}
+ {"requests":[{"authorization":"Bearer test-token","body":{"guardrails":{"config_id":"user-input"},"messages":[],"model":"scout-test"},"path":"/v1/guardrail/checks"}],"values":[{"activated_rails":[],"detail":"JSONDecodeError: expected ident at line 1 column 2","status":"unavailable"}]}
```
