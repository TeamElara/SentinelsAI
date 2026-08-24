# 70 — The dependency version fixer

> **Status:** done. `remediation/dependencies.py`'s `DependencyVersionFixer`,
> registered in `remediation/registry.py`. Two small shared exports added to
> `agents/repo/dependencies.py` (`dependency_finding_id`,
> `manifest_parser_for`, `split_version_prefix`) so the Fixer never
> duplicates the agent's own parsing/slug logic. 448 backend tests green (26
> new, all offline against a mocked transport — no real OSV.dev or GitHub
> call). Builds on the design in [PLAN-v5.md](../PLAN-v5.md)'s Stage F,
> scoped in chat before any of this was written.

## What we built

A `dependency-*` finding (`agents/repo/dependencies.py`, e.g.
"`requests==2.6.0` has 2 known vulnerabilities") now has a real Fixer. It
only handles a pin traceable to a manifest the agent already parses
(`requirements.txt`, `package.json`, `pyproject.toml`) — a finding that only
shows up via `package-lock.json`'s transitive entries still has no Fixer,
on purpose, and falls back to the AI suggestion panel like any other
unfixable finding.

## The one big idea: don't store what you can re-derive

The scoping pass ([PLAN-v5.md](../PLAN-v5.md)) started from an assumption:
`Finding` would need a new field to carry vulnerability ids, since
`agents/repo/dependencies.py` only puts them in a formatted `evidence`
string, never meant to be parsed back out. Writing the actual `plan()`
function made that assumption fall apart on contact — it turned out there
was nothing worth storing in the first place.

`plan()` doesn't need to know which vulnerabilities the *scan* found. It
needs to know whether the *current* pin is still vulnerable, which is a
question it can just ask fresh:

```python
vuln_ids = await _current_vuln_ids(files.client, dep)  # dep's version, re-read from the file, right now
```

If OSV comes back clean, the pin was already fixed since the scan ran —
`plan()` returns `None`, the same honest "nothing to do" every other Fixer
in this codebase already answers with. There's no state to keep in sync
between the finding and the world, because nothing from the finding's
`evidence` is ever consulted for the actual decision. This is the same
lesson [note 69](69-wiring-the-unlink-button.md) drew about React state
going stale, one layer down: the fix there was "throw the stale copy away
and re-ask"; here it's "never make a copy in the first place."

## A tiny standalone version of it

```python
def is_it_raining(cached_forecast: str) -> bool:
    return cached_forecast == "rain"       # trusts a note someone wrote earlier

def is_it_raining_now() -> bool:
    return look_out_the_window()           # asks the only source that can't be stale
```

The first version is only ever as good as when the forecast was written.
The second one can't drift, because there's nothing to drift — it has no
memory to fall out of sync with reality.

## OSV's vulnerability ranges, briefly

A single OSV record can say more than "version X is bad." It describes
*ranges*: a vulnerability starts at some `introduced` version and, if it's
been patched, ends at a `fixed` version. A package can have several
disjoint bad ranges (patched, then broken again by a regression, then
patched again), so `_target_version` doesn't just grab the first `fixed` it
sees — it collects every `fixed` event across every vulnerability
currently affecting the exact pinned version, and picks the highest one, so
one patch clears all of them:

```python
best: tuple[int, ...] | None = None
for vuln_id in vuln_ids:
    vuln = await _fetch(vuln_id)                       # one record per vulnerability
    for raw in _fixed_versions(vuln, ecosystem, name):  # every "fixed" event in it
        parsed = _parse_plain_version(raw)
        if parsed is None:
            return None       # can't confidently order it -- decline the whole fix
        if best is None or parsed > best:
            best = parsed
```

If *any* relevant range has no `fixed` event at all — still open, or OSV
only ever recorded a `last_affected` version — the whole fix declines
rather than guess a version that might still be inside that open range.

## Rejected: editing package-lock.json for a transitive dependency

`agents/repo/dependencies.py` reads transitive versions straight out of the
lockfile, so in principle a `dependency-*` finding could point at a package
with no line in `package.json` at all. Editing that entry directly was
rejected during scoping: `npm`'s dependency resolution decides which
transitive version actually gets installed from the *whole* tree, not from
one file. Hand-editing one entry can produce a lockfile that doesn't match
what `npm install` would ever actually resolve — a worse failure mode than
declining. `manifest_parser_for` returning `None` for `package-lock.json`
is what enforces this: no parser, no Fixer, full stop.

## Rejected: bundling every vulnerable pin in a file into one patch

`SecurityHeaderFixer` bundles all four header findings into a single file
edit, because they're four independent facts about *one* shared config
object. Dependency pins aren't like that — each one is its own line (or
JSON entry), unrelated to its neighbors except by living in the same file.
One `FixPlan` per finding matches the finding model's own granularity, and
means a repo with five vulnerable packages gets five independent, reviewable
patches instead of one large one where a single package's ambiguous
`fixed` version blocks the other four from landing at all.

## Try it

- `pytest tests/test_remediation_dependencies.py -q` — 26 tests, fully
  offline against a mocked transport that serves both a fake GitHub content
  endpoint and a fake OSV.dev at once (`mock_site` matches by path only, so
  one fixture stands in for two hosts).
- `pytest tests -q` — 448 green, no regressions from the two small
  refactors to `agents/repo/dependencies.py` (extracting
  `dependency_finding_id` and `split_version_prefix` out of code that
  already existed, not changing what it produces).
- Feed `_rewrite_package_json_pin` a `"^4.17.0"` pin and a `"4.17.21"`
  target — the result keeps the `^`, because losing it while bumping the
  version underneath would silently turn a semver range into an exact pin,
  a bigger behavior change than the fix was supposed to make.

## Words worth knowing

- **`await`** — pauses this `async def` function until the thing on the
  right finishes, without blocking anything else in the program while it
  waits. `_target_version` awaits one HTTP call per vulnerability id, one
  at a time, in a normal `for` loop — each `await` yields control back to
  the event loop for that one pause, not a special parallel mechanism.
- **Range / event (OSV's vocabulary)** — a *range* is one span of vulnerable
  versions; an *event* inside it marks where that span starts
  (`introduced`) or ends (`fixed`). One vulnerability can have several
  ranges if it was patched, then reintroduced, then patched again.

---

**Next:** the two remaining deferred items — `remediation/secrets.py` and
the `netlify.toml`/`nginx.conf` header-fixer extension — both still need
their own scoping pass before any code.
