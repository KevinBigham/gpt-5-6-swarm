# Codex plugin and specialist profiles

This repository is a Codex marketplace with one canonical skill copy under
`plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm/`. The plugin manifest, installed
skill, license, and third-party notices travel together; there is no second
tracked skill tree that can drift.

## Install from a published tag

Pin releases when reproducibility matters:

```sh
codex plugin marketplace add KevinBigham/gpt-5-6-swarm --ref v0.4.0
codex plugin add gpt-5-6-swarm@gpt-5-6-swarm
```

The `v0.4.0` command becomes valid only after that tag is publicly released.
Until then, use a local clone:

```sh
codex plugin marketplace add /absolute/path/to/gpt-5-6-swarm
codex plugin add gpt-5-6-swarm@gpt-5-6-swarm
```

Start a new Codex task after installation or reinstallation so the skill is
loaded from the new package.

Finish or explicitly reconcile an active older-protocol run with the tool that
created it before upgrading. Tool 0.4 intentionally refuses protocol 1.3
ledgers instead of silently migrating in-flight execution state.

## What the plugin installs

The plugin installs `$gpt-5-6-swarm`, including its reference set, ledger,
frozen-contract tool, and benchmark evidence tool. It declares no hooks, MCP
servers, apps, remote services, or authentication requirements. The benchmark
tool is a declared-record diagnostic, not a source-evidence authenticator.

The eight files under `.codex/agents/` are **project-scoped contributor
profiles**, not plugin components. The current documented plugin manifest has
no custom-agent field, so installation does not copy, select, or pin them. A
profile's presence also does not prove the current spawn surface selected it.

The profiles cover architecture reconnaissance, test strategy, adversarial
security review, isolated implementation, integration review, artifact
verification, incident hypotheses, and documentation/compatibility review.
Each forbids nested delegation and ledger mutation. Only the isolated builder
may write, and only inside the exact root-assigned isolated worktree and scope.

## Validate before release

```sh
python3 -m pip install -r requirements-dev.txt
CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" \
  plugins/gpt-5-6-swarm
python3 "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" \
  plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm
```

The validator paths resolve through the maintainer's configured Codex home and
are not runtime dependencies. Repository CI independently checks the
marketplace/manifest tree, skill metadata, profile fields, version agreement,
licenses, notices, links, schemas, and examples.

## Credit

Kevin Bigham is the community plugin publisher. The packaged skill preserves
Forward Future and Matthew Berman's upstream MIT credit and the exact license
notice. Publisher identity does not replace upstream attribution; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
