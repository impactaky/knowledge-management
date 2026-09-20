# Hermes Federation Workflows Plugin

Standalone Hermes plugin that registers the canonical shared workflows from
the common repository and any optional personal/store workflows.

Install and enable the plugin:

```bash
mkdir -p ~/.hermes/plugins
ln -sfn "/path/to/knowledge-management/foundation/integrations/hermes-federation-workflows" ~/.hermes/plugins/federation-workflows
hermes plugins enable federation-workflows
```

After restarting Hermes, workflows resolve with qualified names such as:

```text
federation-workflows:distill
federation-workflows:grill-federated
federation-workflows:study-book
federation-workflows:study-topic
federation-workflows:summarize-source
federation-workflows:survey
federation-workflows:teach
federation-workflows:write-article
```

## Discovery Contract

1. **Canonical shared workflows**: Discovered from the common repository's `skills/` directory (or configured via `memory.federation.shared_skills_path`).
2. **Optional personal workflows**: Discovered from `memory.federation.personal_skills_path` or from `skills/` beside `memory.federation.catalog_path` if that directory exists.
3. **Deduplication**: If a workflow name is already registered from the canonical shared set, it is not registered again from personal directories.

The plugin registers repository paths directly; it does not copy skill bodies into `~/.hermes/skills/`.
