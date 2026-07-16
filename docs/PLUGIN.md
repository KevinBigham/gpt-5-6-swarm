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

## What the plugin installs

The plugin installs `$gpt-5-6-swarm`, including its reference set, ledger,
frozen-contract tool, and benchmark evidence tool. It declares no hooks, MCP
servers, apps, remote services, or authentication requirements.

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
PYTHONPATH=/tmp/gpt-swarm-skill-validator \
  python3 /Users/kevin/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/gpt-5-6-swarm

PYTHONPATH=/tmp/gpt-swarm-skill-validator \
  python3 /Users/kevin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/gpt-5-6-swarm/skills/gpt-5-6-swarm
```

Those absolute paths are local maintainer examples, not runtime dependencies.
Repository CI independently checks the marketplace/manifest tree, skill
metadata, profile fields, version agreement, licenses, notices, and links.

## Credit

Kevin Bigham is the community plugin publisher. The packaged skill preserves
Forward Future and Matthew Berman's upstream MIT credit and the exact license
notice. Publisher identity does not replace upstream attribution; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
