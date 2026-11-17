# REPORT

Bank Suite is a hostile HTML back-office: unlabeled inputs, data tables, radios without accessible names, and member detail loaded in an iframe. The goal is not an agent that can click through a session once. It is to record a flow, compile it into a reusable capability, and fail in a form an operator can act on.

The design follows a compile/execute split. Discovery (LLM + Playwright) is the expensive front end. The artifact is the intermediate representation (contract). Replay executes that representation with no model in the loop.

## 1. Architecture

The system is a single process with two execution paths.

Discovery runs observe → plan → act against a live Chromium page. Replay uses the same page, locator resolver, and `SafetyGuard`, without the model. A job queue, worker pool, and capability catalog were omitted. Those belong in a service; they would not change the correctness of this loop.

The boundary between the paths is the artifact JSON, not the step log. The log is evidence for a run. The artifact is the callable contract: input is parameters, output is checkpoint and outputs.

Locators live in `src/surface/locator.py`, separate from the flow schema. A step specifies the action (for example, type into Member Number). The surface layer resolves the control via role, HTML `name`/`id`, table-cell XPath, or `nth`. That separation is the intended extension point for a desktop accessibility driver: replace the resolver, keep the step format.

**Tradeoff.** A single process is straightforward to operate and to demonstrate. It is not a multi-tenant service. The choice was a complete, inspectable loop over platform scaffolding.

## 2. Artifact schema

An artifact is a versioned capability: `schema_version`, id, name, `allowed_domain`, typed `parameters` and `outputs`, ordered `steps`, and a `checkpoint`. Actions are a discriminated union (`navigate`, `click`, `type`, `select`, `wait`, `read`). Each targeted step carries a full `LocatorStrategy`, including `resolved_via` and `frame_url`, so replay does not have to rediscover how the control was found.

`read` steps re-extract outputs on replay. Values the model placed in `declare_goal_complete` are not treated as source of truth.

The parameterizer makes a recording reusable. Member numbers, application IDs, review decisions, officer notes, and new-application fields become `{placeholders}` that replay interpolates. When a decision requires extras (`decline_reason`, `escalate_to`), missing parameters fail the run rather than selecting the first `<option>` (in the dropdown).

Results are a discriminated union: `success`, `business_outcome` (member not found, already decided, account frozen), and `failure` (step, expected, observed, recoverable). Process exit codes follow that split: 0, 2, and 1.

**Tradeoff.** A strict schema means a discovery run that cannot produce a valid artifact is a miss. That is preferable to treating a Playwright transcript as a capability. The cost is that a noisy recording (for example, a wizard step that typed ZIP into the wrong box, then corrected it) remains noisy in the artifact. The parameterizer normalizes values; it does not rewrite the agent’s path.

## 3. Determinism and error handling

Replay prefers `resolved_via` from the recording, then walks the remainder of the locator chain. Timeouts retry once. After each step the runner scans page and iframe text for known Bank Suite messages and returns those as business outcomes, not exceptions. For example, “Member not found” is a legitimate result. A missing Approve radio is a locator failure.

Checkpoints assert a URL substring, visible text, and/or an element. Locator misses and checkpoint failures write screenshots next to `steps.json`. Native `confirm()` dialogs are accepted only after the safety guard has allowed the action; that dialog is the application’s confirmation, not the operator’s.

Locator fallbacks address unlabeled fields and iframe content in the absence of test IDs, including `input[value=APPROVED]` for radios that do not appear in the accessibility tree. UI drift is a secondary concern on these screens; missing semantics are the primary one.

**Tradeoff.** Matching known error strings will miss new copy. Classifying the outcomes that exist in Bank Suite is preferable to treating every error banner as a crash. Introducing a second model on replay would make determinism a claim rather than a property.

## 4. Heterogeneity and multi-tenant

Heterogeneity is handled as surface versus flow. The artifact should not assume Chromium. Portable hints are `role`, `name`, HTML `name`/`id`, proximity, and frame. A desktop adapter would implement the same resolve contract on the OS accessibility tree. Replay would not change.

Multi-tenant reuse is modeled as a base artifact plus overlays, not as N full recordings. The base holds parameterized routes and inputs. An overlay replaces locators or inserts steps such as dismissing a branded interstitial. Drift is a typed replay failure (`LOCATOR_NOT_FOUND`, checkpoint failure) plus re-discovery of that step, not a full re-record.

The overlay store is not implemented. `resolved_via` and HTML attributes are stored on each step so an overlay has a method to pin.

**Tradeoff.** Without a second tenant copy of Bank Suite, overlay reuse is described rather than demonstrated. Implementing a second UI would not have improved the schema, which is the prerequisite.

## 5. Escalation and handoff

A run is stuck when the model calls `escalate`, when safety blocks a required action, or when replay is started with `--handoff` on a recoverable failure.

Control stays on the same Playwright page.  It displays a collapsed dock at the bottom of the page (`ESCALATED — Take control`). Operator is now in control adn performs action and handing off to agent requires a non-empty description before Resume or Stop is enabled. Those actions write `{id}-response.json` and `handoff.json` under `evidence/`. Resume continues the session. Stop or timeout ends it. `--mock-operator` bypasses the dock in tests.

Replay does not synthesize an escalation. If discovery recorded a completed handoff as a successful checkpoint, replay verifies that checkpoint and exits. The dock is for a live stuck state, not for replaying a finished intervention.

**Tradeoff.** This is a control transfer, not co-browsing. There is no operator console, shared cursor, or keystroke audit. The implementation provides a usable page, a required note, and a machine-readable resume or stop. A full operations console is out of scope.

## 6. Safety

The same `SafetyGuard` runs on discovery and replay. Policy is an allowlist of domains and action types. Navigation off the allowed domain is blocked. Clicks whose accessible name matches irreversible hints (`submit`, `approve`, `delete`, and similar) are flagged during discovery, native confirm is still accepted so a recording can finish and may be blocked in replay with `--block-irreversible`. Fields that look like SSN or password must be marked `sensitive`. Redactor removes those values from traces.

Name-substring heuristics are incomplete. A button labeled “OK” that commits a payment would not be flagged. Artifacts should still be reviewed before production use. This layer constrains the agent, it does not underwrite the flow.

**Tradeoff.** Discovery is permissive enough to record irreversible actions, production replay can refuse them. Blocking submit during recording would prevent a new-application artifact. Never blocking it in production would make the allowlist ineffective. `--block-irreversible` is that split.

## 7. Scope cuts

Not implemented: a job queue, a capability catalog API, a desktop driver, a tenant overlay service, an LLM fallback on replay, and an artifact approval workflow.

If this were extended, the next steps would be to pin iframe locators more explicitly on the artifact, and to add a second tenant UI of Bank Suite so overlay reuse can be shown rather than described.

The README covers the demo path: discover against Bank Suite, inspect `artifacts/` and `evidence/`, replay with parameters and escalate on a missing field (SWIFT) during a live discovery run.
