"""Execute the UI's real event handlers and rendering in the existing Deno runtime."""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from federation_support import FOUNDATION

STATIC_INDEX = FOUNDATION / "tools" / "knowledge-ui" / "static" / "index.html"


class SearchActionTests(unittest.TestCase):
    def test_explicit_actions_replace_results_and_ignore_stale_responses(self):
        if not shutil.which("deno"):
            self.skipTest("deno not found")
        html = STATIC_INDEX.read_text()
        functions = "\n".join(
            re.search(rf"^(?:async )?function {name}\([^\n]*\).*?^}}", html, re.M | re.S).group()
            for name in ("runSearch", "renderResults", "selectionOmissions", "claimSourceLabel",
                         "resolveEntryTarget", "resultLabel", "isExecutableArticle", "stripMarkdown", "esc", "jsEsc")
        )
        handlers = re.search(r'^qEl.addEventListener\("keydown".*?^}\);', html, re.M | re.S).group()
        handlers += "\n" + "\n".join(re.findall(r'^(?:searchButton|grepButton).addEventListener.*$', html, re.M))
        self.assertIn('id="grep-button"', html)
        script = r'''
function assert(value, message) { if (!value) throw new Error(message); }
function control() { return {handlers: {}, addEventListener(event, fn) { this.handlers[event] = fn; }}; }
const qEl = control(), searchButton = control(), grepButton = control();
const resultsPane = {innerHTML: ""};
const state = {searchRequest: 0};
let tab, response, calls = [];
function hideSuggest() {}
function setTab(value) { tab = value; }
async function fetch(url) { calls.push(url); return response; }
function reply(data, ok = true) { return {ok, status: ok ? 200 : 500, json: async () => data}; }
const article = {path: "/article.md", title: "Article hit", claim: "Current claim", snippet: "human snippet", anchor: "finding"};
const location = {path: "/rule.md", title: "Rule hit", match_lines: [3], omitted: ["claim:not_authored"]};
''' + functions + "\n" + handlers + r'''
qEl.value = "日本語";
response = reply({article_results: [article], fulltext_results: [location]});
await searchButton.handlers.click();
assert(calls.length === 1 && calls[0] === "/api/search?q=" + encodeURIComponent(qEl.value), "Search endpoint");
assert(tab === "results" && resultsPane.innerHTML.includes("human snippet"), "article rendering");
assert(resultsPane.innerHTML.includes("#finding") && !resultsPane.innerHTML.includes("/rule.md"), "only article action renders articles");

response = reply({fulltext_results: [location], article_results: [article]});
await grepButton.handlers.click();
assert(calls.length === 2 && calls[1].startsWith("/api/grep?"), "Text search endpoint only");
assert(resultsPane.innerHTML.includes("/rule.md") && resultsPane.innerHTML.includes("lines 3"), "locator rendering");
assert(!resultsPane.innerHTML.includes("human snippet") && !resultsPane.innerHTML.includes("Article hit"), "grep replaces article results");

response = reply({index_results: [{path: "/INDEX.md", claim: "Entry claim", links: [{path: "/rule.md", anchor: "rule"}]}]});
let prevented = false;
qEl.handlers.keydown({key: "Enter", preventDefault() { prevented = true; }});
await new Promise(resolve => setTimeout(resolve, 0));
assert(prevented && calls.length === 3 && calls[2].startsWith("/api/search?"), "Enter remains search");
assert(resultsPane.innerHTML.includes("Entry claim") && resultsPane.innerHTML.includes("#rule"), "entry navigation retained");
assert(!resultsPane.innerHTML.includes("Text locations"), "Enter clears grep display");

response = reply({fulltext_results: [], warnings: ["<query too large>"], truncated: true});
await grepButton.handlers.click();
assert(resultsPane.innerHTML.includes("No results.") && resultsPane.innerHTML.includes("&lt;query too large&gt;"), "empty warning shown and escaped");
assert(resultsPane.innerHTML.includes("Text search") && resultsPane.innerHTML.includes("omitted"), "grep status without semantic fields");
response = reply({fulltext_results: [], warnings: []});
await grepButton.handlers.click();
assert(resultsPane.innerHTML.includes("No results.") && !resultsPane.innerHTML.includes("query too large"), "new empty success clears warnings");
response = reply({detail: "<fixture failure>"}, false);
await grepButton.handlers.click();
assert(resultsPane.innerHTML.includes("Search failed:") && resultsPane.innerHTML.includes("&lt;fixture failure&gt;"), "HTTP errors visible");
assert(!resultsPane.innerHTML.includes("No results."), "error not misreported as empty success");

let finish;
response = new Promise(resolve => { finish = resolve; });
const old = searchButton.handlers.click();
assert(resultsPane.innerHTML.includes("Searching..."), "loading clears previous state");
response = reply({fulltext_results: [location]});
await grepButton.handlers.click();
const current = resultsPane.innerHTML;
finish(reply({article_results: [article]}));
await old;
assert(resultsPane.innerHTML === current, "late search cannot overwrite chosen grep");

response = Promise.reject(new Error("network unavailable"));
await grepButton.handlers.click();
assert(resultsPane.innerHTML.includes("network unavailable"), "network failure visible");
response = reply({article_results: [article]});
await searchButton.handlers.click();
assert(resultsPane.innerHTML.includes("human snippet") && !resultsPane.innerHTML.includes("failed"), "re-search recovers");
const count = calls.length;
qEl.value = " ";
await grepButton.handlers.click();
assert(calls.length === count, "blank query never sent");
console.log("Explicit search/grep events, status, navigation and response switching passed");
'''
        result = subprocess.run(["deno", "run", "--no-config", "-"], input=script,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hiding_suggest_aborts_in_flight_request_and_keeps_latest(self):
        if not shutil.which("deno"):
            self.skipTest("deno not found")
        html = STATIC_INDEX.read_text()
        functions = "\n".join(
            re.search(rf"^(?:async )?function {name}\([^\n]*\).*?^}}", html, re.M | re.S).group()
            for name in ("fetchSuggest", "hideSuggest")
        )
        script = r'''
function assert(value, message) { if (!value) throw new Error(message); }
const state = {suggestAbort: null, suggestActive: -1};
const suggestList = {style: {}, innerHTML: "", _items: []};
let rendered = [];
function renderSuggest(items) { rendered = items; }
const pending = [];
function fetch(url, options) {
  return new Promise((resolve, reject) => pending.push({url, signal: options.signal, resolve, reject}));
}
''' + functions + r'''
function abortError() { const error = new Error("aborted"); error.name = "AbortError"; return error; }

const first = fetchSuggest("日本");
assert(state.suggestAbort, "controller stored");
const firstSignal = state.suggestAbort.signal;
hideSuggest();
assert(firstSignal.aborted, "hide aborts in-flight request");
assert(state.suggestAbort === null, "hide clears controller");
assert(suggestList.style.display === "none" && suggestList._items.length === 0, "hide clears list");
pending[0].reject(abortError());
await first;
assert(rendered.length === 0, "aborted response is ignored");

pending.length = 0;
const older = fetchSuggest("a");
const olderSignal = state.suggestAbort.signal;
const latest = fetchSuggest("b");
assert(olderSignal.aborted, "new query aborts the older request");
pending[0].reject(abortError());
pending[1].resolve({json: async () => ({results: [{text: "B"}]})});
await older;
await latest;
assert(rendered.length === 1 && rendered[0].text === "B", "only the latest suggestion renders");
console.log("suggest abort and late-response behavior passed");
'''
        result = subprocess.run(["deno", "run", "--no-config", "-"], input=script,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
