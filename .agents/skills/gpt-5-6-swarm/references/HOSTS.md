# Host capability matrix

Swarm's guarantees are only as real as the host features they stand on.
This file separates three things that are often blurred: what the protocol
*requires*, what the current host has been *verified* to provide, and what
is *optional or experimental*. Never promote a claim across tiers without
re-verification.

## Tier 1 - protocol requirements (host-independent)

These must hold for a full-route Swarm regardless of host; where the host
cannot provide one, `SKILL.md` prescribes narrowing the route:

| Requirement | Fallback when absent |
| --- | --- |
| Create host-managed child threads with coordinator-visible identity, lifecycle, and results | do the work serially in the coordinator |
| Read a child thread's turns (nonce discovery) | no guarded classes: `PURE`/`ISOLATED` only |
| Unique-launch discovery (find a nonce among threads) | prohibit `NON_IDEMPOTENT`, `EXCLUSIVE_UNKNOWN`, `ONE_SHOT` (enforced) |
| Cooperative cancel or interrupt of a child | treat cancellation as advisory; plan for orphans |
| Local command execution and permitted control-plane state writes | prompt-only ledger discipline (enforcement tool unavailable) |
| Isolated write locations (worktrees/branches) | single-writer route |

The enforcement tool snapshots what the coordinator declares at `init` and
holds the run to it; it cannot itself verify the host. Declare only what
you verified.

## Tier 2 - verified on the current baseline host

Baseline: Codex CLI `0.144.2`, verified locally on 2026-07-15, plus the
current official Codex manual and configuration reference. This is an
environment snapshot, not a minimum-version promise. Re-verify after any host
upgrade:

```bash
codex --version
codex features list
codex debug models
```

Verified facts relevant to Swarm:

| Fact | Source | Swarm consequence |
| --- | --- | --- |
| Multi-agent support is stable and on by default (`features.multi_agent`) | local feature list + Codex manual | persistent subagent threads are a first-class host feature; discover the actual tool vocabulary exposed by the current surface rather than hardcoding names |
| `agents.max_depth` defaults to `1` | config reference | the host itself blocks nested delegation at default settings - keep it at 1; Swarm's prohibition assumes it |
| `agents.max_threads` defaults to `6` when unset | Codex manual | this is only a configuration default; use a lower live-session slot limit when the surface exposes one |
| Project custom agents may be standalone `.codex/agents/*.toml` files with `name`, `description`, and `developer_instructions`; config-file mappings also exist in the configuration reference | Codex manual + config reference | inspect the current host schema before shipping presets; do not invent an agent manifest format |
| Reasoning efforts are model-dependent and can include `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra` | Codex manual | record only an effort supported by the selected model on the current host |
| This local surface exposes four total concurrency slots including the root, and its spawn call does not expose per-child model/effort selectors or arbitrary child-turn search | current session tool contract | cap the live peak at three here; keep the current coordinator when child model/effort cannot be pinned; do not claim nonce discovery unless a separate thread-read/listing surface proves it |
| Lifecycle hooks exist with `SubagentStart`/`SubagentStop` events (`features.hooks`) | config reference | future enforcement integration point (Phase 2+), not used in Phase 1 |

## Tier 3 - optional and experimental (gated, fail closed)

| Feature | Status | Swarm policy |
| --- | --- | --- |
| `ultra` effort (parallel multi-agent mode) | exposed on the local host | **prohibited for Swarm nodes, enforced**: it delegates outside the root scheduler; revisit only if root-visible nested-delegation control, deduplication, and accounting are proven |
| `max` effort | documented by the Codex manual and exposed locally on Sol, Terra, and Luna | the enforcement whitelist accepts `max`; the routing protocol reserves it for exceptional Sol adjudication unless a future evaluated route says otherwise |
| `features.rollout_budget.*` | documented as under development and off by default | treat as a gated optional optimization; never a normative requirement; absence changes nothing |
| Responses API multi-agent (server-side) | separate surface from Codex CLI | out of scope for this skill's host assumptions; a future adapter is a Phase 4 item |
| Explicit API prompt-cache breakpoints | real at the API level | not claimed controllable inside ordinary Codex child threads; do not design routes that depend on it |
| Luna long-context routing | no repository-owned evaluation baseline yet | conservative slicing heuristic in `references/ROUTES.md`, not a threshold or prohibition; replace with measured guidance after local evals |

## Verification procedure for a new host

1. `codex --version` - record it in the run's capability snapshot rationale.
2. `codex features list` - confirm multi-agent on; note anything experimental
   you intend to rely on, and default to *not* relying on it.
3. `codex debug models` - confirm the model IDs and the effort levels each
   supports before routing depends on them (especially `xhigh`/`max`).
4. Confirm `agents.max_depth` is 1, note `agents.max_threads`, and prefer any lower live-session concurrency limit surfaced by the client.
5. Inspect whether the current spawn surface can pin each child's model and effort, discover an ambiguous launch nonce, read child turns, and interrupt a child. Treat each as a separate capability.
6. Declare exactly what you verified via `init --capability k=v`, nothing
   more. An undeclared capability is treated as absent by the enforcement
   tool - by design.
