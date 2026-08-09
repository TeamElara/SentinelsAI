# 51 — Misconfiguration agent

> **Status:** done. Sentinels now runs 7 agents. A live scan of `example.com`
> still scores exactly 54/F — this agent only found an Info-level "no
> version disclosed" PASS. `pytest backend/tests -q` → 35 passed.

## What we built

A 7th agent, `misconfig`, that checks for a server set up carelessly:
browsable directory listings, forgotten backup files, debug/error pages
left on, an exact software version leaking in a header, risky HTTP methods
advertised, a leftover installer script, and a session cookie served in a
response that's allowed to be cached. Same rules as every other agent —
only reads, never a write, never a deliberately triggered error.

## The one new idea: not every check needs a "clean" answer

`headers.py` and `exposure.py` both report a PASS for every single thing
they check — "CSP present," "`.env` not exposed" — because each of those is
one specific, named thing with an obvious yes/no state. This agent is
different: five of its seven checks (directory listing, backups, debug
output, risky methods, setup pages) are really "did we spot a problem
anywhere across several paths?" — and a spot-check like that doesn't have a
clean opposite worth reporting. There's no meaningful "PASS: no directory
listing found on `/uploads/`" the way there's a meaningful "PASS: HSTS
header present" — the six paths checked were arbitrary guesses, not an
exhaustive list, so silence just means "nothing here," not "verified safe."

The one check that *does* get an explicit PASS is server-version disclosure
(check D), because — like `headers.py`'s checks — it's one specific header
with one obvious clean state:

```python
if _VERSION_RE.search(value):
    ...  # WARN, Low — "server: nginx/1.18.0"
else:
    ...  # PASS, Info — "server: nginx" (no version number)
```

Standalone example of the same asymmetry, with grocery shopping instead of
HTTP:

```python
# "Did I check the fridge for milk?" has a clean yes/no — you either
# saw milk or you didn't, and both are worth writing on the list.
fridge_has_milk = "milk" in fridge_contents  # True or False, always meaningful

# "Did I find anything suspicious searching the whole house?" doesn't.
# Searching five rooms and finding nothing isn't "the house is verified
# clean" -- it's "nothing in the five rooms we happened to check."
suspicious_items = [item for item in searched_rooms if is_suspicious(item)]
# an empty list here just means "found nothing," not "confirmed nothing exists"
```

That's why a mocked site with a normal homepage and a real 404 for every
other path returns **zero findings** from this agent — not a filler "all
clear" finding. `test_styled_404_everywhere_yields_zero_findings` pins
exactly that.

## The actual code

Directory listings require the body to actually look like an index page,
not just answer 200 — the same soft-404 discipline every other agent uses:

```python
_AUTOINDEX_RE = re.compile(
    r"<title>Index of /|<h1>Index of|<table id=\"indexlist\"", re.IGNORECASE
)
...
if not _AUTOINDEX_RE.search(text):
    continue
```

Severity then depends on *what's in* the listing, not just that one exists:

```python
severity = Severity.LOW
if _SENSITIVE_EXT_RE.search(text):      # a .sql or .env-shaped filename
    severity = Severity.HIGH
elif _ARCHIVE_EXT_RE.search(text):      # .zip, .bak, .dump, ...
    severity = Severity.MEDIUM
```

The debug-output check doesn't send its own probes — it scans every
response the agent *already fetched* for the other six checks (plus the
homepage and `robots.txt`), which is why it runs last and takes a plain
list, not a URL:

```python
def _check_debug_output(self, probed: list[dict]) -> list[Finding]:
    for item in probed:
        text = item["response"].text
        ...
```

Budget math (why it's exactly ≤18, not some rounder number): homepage (1) +
directory listing (6) + backup files (7) + setup pages (3) + OPTIONS (1) =
18. `robots.txt` doesn't count — it reuses the same cached fetch
`RobotsGate` already made, so re-reading it here is free, not a new
request.

## Try it

- `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_misconfig.py -v`
  — 16 tests: severity ladders for directory listings and backup files, the
  bare-vs-versioned `Server` header split, risky-method thresholds, and the
  `.env`/`.git` exclusion test that fails loudly if this file ever
  duplicates `exposure.py`'s job.
- Open `test_real_sql_dump_is_critical` and delete the `CREATE TABLE` line
  from the mocked body — rerun it and watch the severity drop from Critical
  to High, since the content no longer really parses as SQL.
- Change `BACKUP_FILE_PATHS` to include `"/.env"` and rerun
  `test_backup_paths_never_include_env_or_git` — watch it fail immediately,
  which is the point: it's a tripwire for exactly that mistake.

## Words worth knowing

- **Marker match** — requiring the *content* of a 200 response to match a
  known shape (an autoindex page, a phpinfo() signature) before trusting it,
  never the status code alone.
- **FAIL-only check** — a check that only ever produces a finding when it
  spots a problem, with no symmetric "PASS" for silence, because silence
  here doesn't mean "verified clean."
- **Free fetch** — reusing a response another part of the scan already
  fetched (via V2's cache) instead of issuing — and budgeting for — a new
  request.

---

**Next:** V6 — the Subdomain Security agent.
