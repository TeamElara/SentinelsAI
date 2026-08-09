# 55 — Testing the untested paths

> **Status:** done. 45 new tests (56 → 101), covering the probe layer, the
> schema's round trip through SQLite, and orchestrator-level crash isolation.

## What we built

A few milestones back (V2, V6) shipped code that was verified by hand — a
one-off script, or "read the code and reason about it" — because the test
infrastructure didn't exist yet at the time, or because a full end-to-end
storage test was out of scope for that step. This milestone goes back and
writes those tests properly: `test_probe.py` for the shared fetch/robots/
budget machinery, `test_findings_schema.py` for the `affected_url`/
`confidence` fields' actual trip through the database, and
`test_orchestrator.py` for the one guarantee CLAUDE.md names explicitly —
"agents must never crash the scan" — tested at the level where it actually
matters: the whole 8-agent scan, not just one agent in isolation.

## The one big idea: a fixture that swaps out a module-level constant

Every existing storage function (`save_scan`, `get_scan`, ...) opens its own
connection by calling `db.get_connection()`, which always points at
`db.DB_PATH` — a fixed path to the real development database. A test that
called `save_scan()` directly would write real rows into
`backend/data/sentinels.db`, which is wrong for two reasons: it pollutes your
actual scan history, and it means tests can affect each other depending on
what's already in that file.

The fix doesn't require changing any storage code. `db.DB_PATH` is just a
module attribute, and pytest's `monkeypatch` fixture can replace any
attribute on any object for the duration of one test, then put the original
back automatically when the test ends:

```python
import my_module

def test_something(monkeypatch):
    monkeypatch.setattr(my_module, "SOME_CONSTANT", "test-value")
    # my_module.SOME_CONSTANT is "test-value" for this test only
# back to the original value here, automatically
```

The project's new `temp_db` fixture (in `conftest.py`) does exactly this
with a throwaway file path from pytest's own `tmp_path` fixture:

```python
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db.DB_PATH
```

Any test that takes `temp_db` as an argument gets a completely fresh,
throwaway database, and every `get_connection()` call anywhere in the code
under test — even three function calls deep, even in files that never heard
of pytest — transparently opens that file instead of the real one, because
they're all reading the same `db.DB_PATH` name at call time.

## The other idea worth naming: testing the *architecture's* failure mode, not inventing a new one

`test_orchestrator.py`'s main test asks: if one of the eight agents raises an
exception, does the whole scan still produce a report? The answer was already
"yes" — `BaseAgent.run()` has caught every agent's exceptions since the very
first milestone (see note 03). This test doesn't add new crash-handling
code; it just proves the existing guarantee holds at the scale that actually
matters now (eight real agents, not one). To do that without a real network,
it swaps in eight tiny fake agent classes — seven that return one clean
finding, one that raises on purpose — by monkeypatching
`orchestrator.AGENTS` the same way `temp_db` swaps `db.DB_PATH`:

```python
monkeypatch.setattr(orchestrator, "AGENTS", _eight_agents_one_broken())
report = await run_scan("https://example.com")

broken = [a for a in report.agents if a.agent == "broken"]
assert broken[0].error is not None          # the crash was caught...
assert len(report.agents) == 8              # ...but nothing else was lost
```

The subdomain agent's tests found a real edge of this the same way: if the
low-level `_resolve()` function raises a genuine bug (not the DNS-not-found
cases it already handles), that exception is caught one level up, by
`BaseAgent.run()` — the *whole* subdomain agent reports an error rather than
skipping just that one host. `test_dns_exception_on_one_host_is_isolated_...`
pins that this is the real, current, and acceptable behavior — a single
agent going red doesn't take the rest of the scan down with it — rather than
guessing at a nicer-sounding but non-existent per-host recovery.

## Try it

- Run `cd backend && ./.venv/Scripts/python.exe -m pytest tests -q` — 101
  tests pass, 45 of them new.
- Open `tests/test_orchestrator.py` and change `_BrokenAgent`'s exception
  message — rerun just that file
  (`pytest tests/test_orchestrator.py -q`) and watch the assertion on the
  message text catch the change.
- Delete the `temp_db` fixture's `monkeypatch.setattr(...)` line temporarily
  and rerun `test_findings_schema.py` — every test still passes, but now
  against the real `backend/data/sentinels.db`, which is exactly the bug
  the fixture exists to prevent.

## Words worth knowing

- **`monkeypatch`** — a pytest fixture that temporarily replaces an
  attribute, function, or dict entry for one test, then restores it
  automatically afterward.
- **`tmp_path`** — a pytest fixture handing each test its own empty,
  automatically-cleaned-up directory on disk.
- **Fixture composition** — a fixture (`temp_db`) can itself depend on other
  fixtures (`tmp_path`, `monkeypatch`), the same way a function can call
  other functions.

---

**Next:** V10 — documentation, the remaining learning notes, and a full
end-to-end pass over everything V1-V10 shipped.
