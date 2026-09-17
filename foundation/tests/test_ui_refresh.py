"""Execute the actual browser refresh function with deterministic fetch replies."""
from pathlib import Path
import re
import subprocess


def test_refresh_reports_acceptance_errors_task_failures_and_success():
    source = (Path(__file__).resolve().parents[1] / 'tools/knowledge-ui/static/index.html').read_text()
    function = re.search(r'^async function refreshIndex\(\) \{.*?^}', source, re.M | re.S).group()
    script = function + '''
const reindexButton = {disabled: false};
const previewPath = {textContent: ""};
let replies, loads;
async function fetch() { return replies.shift(); }
async function loadTree() { loads++; }
async function loadDrafts() { loads++; }
const ok = (value) => ({ok: true, json: async () => value});
for (const [responses, expected, expectedLoads] of [
  [[{ok: false, text: async () => "MEILI_URL is not configured"}], "MEILI_URL", 0],
  [[ok({}), ok({status: "failed", error: "embedding failed"})], "embedding failed", 0],
  [[ok({}), ok({status: "succeeded"})], "Search index refreshed.", 2],
]) {
  replies = responses; loads = 0;
  await refreshIndex();
  if (reindexButton.disabled || !previewPath.textContent.includes(expected) || loads !== expectedLoads) {
    throw new Error(previewPath.textContent);
  }
}
'''
    subprocess.run(['deno', 'run', '--no-config', '-'], input=script, text=True, check=True, timeout=15)
