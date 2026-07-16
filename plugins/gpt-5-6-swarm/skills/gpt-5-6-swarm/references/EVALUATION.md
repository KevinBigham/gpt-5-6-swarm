# Evaluation and performance evidence

Protocol reference set: `1.4.0`.

Never claim that Swarm is faster from intuition, example data, or overlapping
ledger timestamps. Use `scripts/swarm_benchmark.py` for offline declared-record
validation and paired diagnostics; it does not launch workers, manufacture
host timing, or authenticate the source bytes named by declared hashes.

The primary `fixed_graph_scheduling` track holds the frozen task, graph,
prompts, route requests, worker count, host profile, cache policy, and gate
constant. Compare `requested_parallel=1` with the preregistered safe peak. The
secondary `end_to_end_workflow` track is useful but confounded by decomposition,
model count, tokens, and routing; name those limits.

Preregister immutable hashes, warmups, measured pairs, AB/BA order seed,
exclusions, and the acceptance gate. Pair arms closely, restore the same
fixture, publish failures/`UNKNOWN`/exclusions, and keep missing usage as null.
The tool rejects trials outside the exact generated pair/order/warmup plan and
non-preregistered exclusion text. It withholds the median whenever any measured
pair is missing, failed, `UNKNOWN`, gate-failing, or excluded.

Keep concurrency fields separate:

- requested peak: a ceiling;
- scheduler-issued peak: coordinator dispatch evidence;
- observed peak: `UNKNOWN` unless authoritative active-thread telemetry exists.

Break-even is case-family, host, scale, and date specific. A supported claim
needs a preregistered scale series, enough valid pairs, no worse quality, and
uncertainty wholly above speedup `1`. Example benchmark files are format
fixtures, not evidence. A public performance claim also requires independent
source-byte and authority verification; comparator output alone is insufficient.
