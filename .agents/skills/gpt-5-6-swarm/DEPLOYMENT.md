# Serial deployment after a swarm

Read this file only when the user authorized deployment and the graph contains a deployment node.

Deployment is never a parallel worker wave. Quiesce all builders and integrators first. A Luna Medium deployment owner executes the settled release plan; it does not invent one during release. If production evidence invalidates the plan, stop and return evidence to the Sol coordinator.

Authorization to deploy does not automatically authorize committing, pushing, merging, or landing changes. Use only the integrated revision and upstream state already authorized during preflight. If deployment requires an additional repository mutation that the user did not authorize, stop and request that authority before continuing.

1. Reconcile every swarm worker, process, worktree artifact, resource lease, and cleanup item.
2. Require the integrated revision—not individual worker branches—to pass the complete acceptance gate.
3. Acquire an effective repository/shared-environment deployment lock and wait for active deployment to finish. If no reliable lock exists, stop; do not deploy.
4. After acquiring the lock, discard previously selected revisions and fetch the recorded authorized upstream again only if network fetch was authorized.
5. In a dedicated clean deployment checkout on the recorded deployment ref with a configured upstream, run `git pull --ff-only` only if updating that checkout was authorized.
6. Stop without discarding local work if the checkout is dirty, detached, missing its upstream, diverged, or cannot pull.
7. Verify the swarm's integrated commit is an ancestor of the pulled revision.
8. Build complete artifacts for affected services from that integrated revision. Never deploy from a task worktree, feature branch, dirty checkout, or partial overlay.
9. Hold the deployment lock through production health verification.
10. Never deploy an older revision after a newer one unless the user explicitly authorizes rollback.

If the deployment command times out or its effect is ambiguous, mark the node `UNKNOWN`, query authoritative production state while still holding the lock, and never repeat the deployment command speculatively.

Release passes only with the integrated revision, affected services, deployment result, production health evidence, and released lock recorded. Publication, messaging, migrations, rollback, and cleanup remain separately authorized serial effects.
