# 47 — Two new fields on a finding

> **Status:** done. Every `Finding` can now say *which host* it's about
> (`affected_url`) and *how sure we are* (`confidence`) — and every finding
> written before today still reads back exactly as it did.

## What we built

The three new agents coming in v4 need to say things the old five never had to.
The subdomain agent will report "HSTS missing" — but on *which* subdomain? And
it will sometimes report "this might be a subdomain takeover" without being
able to prove it. So `Finding` grew two optional fields, the SQLite table grew
two nullable columns, and the frontend shows them only when they're there.

We also fixed a small bug on the way past: a stored scan's severity counts
included the checks that *passed*, while a live scan's didn't.

## The one big idea: adding without breaking

There are three places a finding lives — the Python model, a SQLite row, and a
TypeScript type in the browser. Changing a shape that already has data in it is
where things usually break. The trick is that **every new piece has to be
optional, and its "missing" value has to mean the right thing already**.

Here's the whole idea in five lines of ordinary Python:

```python
class Order:
    def __init__(self, item, note=None):   # note is optional
        self.item = item
        self.note = note

print(Order("coffee").note)   # None — an old order simply has no note
```

Nothing that was written before the `note` field existed becomes wrong. It just
has `None`, which reads as "no note" — which is true.

SQLite does the same thing at the table level:

```sql
ALTER TABLE findings ADD COLUMN affected_url TEXT;
```

Every row already in that table instantly has `affected_url = NULL`. There's no
rewrite, no rebuild, no downtime — the column is added to the table's
definition and old rows answer `NULL` for it. That only works because we didn't
say `NOT NULL`: a required column with no default would have nothing to put in
the existing rows, and SQLite would refuse.

`db.py` already had the machinery for this — a numbered list:

```python
MIGRATIONS = [(1, _V1_SCHEMA), ..., (7, _V7_SCHEMA), (8, _V8_SCHEMA)]
```

`init_db()` reads which version the database is on and runs only the entries
above it. Our database was on 7, so starting the server ran exactly `_V8_SCHEMA`
and nothing else. A brand-new database runs all eight in order and arrives at
the same place.

## The second idea: `None` is not zero

`confidence` is a number from 0 to 1 — so why does a certain check leave it
`None` instead of setting `1.0`?

Because they mean different things. `1.0` is a claim: "I considered how sure I
am, and I'm completely sure." `None` is "this question doesn't apply here" —
the header was in the response or it wasn't; there's nothing to be unsure
about. Reserving `None` for "no opinion" is what lets the UI stay quiet for the
old five agents and speak up only where doubt is real:

```tsx
const needsVerification = finding.confidence !== null && finding.confidence < 0.9;
```

Same instinct behind `affected_url`: `None` means "the site you scanned",
which is what every existing finding was always about.

## The actual code

The model ([`backend/models.py`](../../backend/models.py)):

```python
affected_url: Optional[str] = None   # the exact URL/host this finding is about
confidence: Optional[float] = None   # 0.0-1.0; None = nothing to hedge
```

Then the same two values are threaded through the places a finding travels:
`storage/findings.py` writes them, `storage/scans.py` reads them back,
`frontend/lib/api.ts` mirrors them as `string | null` / `number | null`, and
`FindingRow.tsx` renders each one **inside an `if`** — so a finding without
them draws exactly the same pixels it drew yesterday.

The counts bug, in `storage/scans.py`:

```python
# was: a hand-written tally that counted every finding, passes included
counts = count_by_severity(all_findings)   # now: the same function a live scan uses
```

Two tallies of the same thing will always drift eventually. Now there's one.

## Try it

- Start the backend once and watch it migrate itself:
  ```bash
  cd backend && ./.venv/Scripts/python.exe -m uvicorn main:app --port 8010
  ```
  Then check the version it landed on:
  ```bash
  cd backend && ./.venv/Scripts/python.exe -c "import db; c=db.get_connection(); print(c.execute('SELECT version FROM schema_version').fetchone()['version'])"
  ```
  It says `8`. Run the server again — still `8`, because there's nothing left to apply.

- Open an old scan in the browser (`/scan/<id>`) and an agent page under it.
  Nothing looks different — the new fields are `null` on every one of those
  findings, so nothing renders.

- Try making the migration illegal on purpose: add
  `ALTER TABLE findings ADD COLUMN foo TEXT NOT NULL;` as a version 9 and start
  the server. SQLite refuses it — there's no value it could put in the rows
  that already exist. Delete it again afterwards.

## Words worth knowing

- **Migration** — one numbered, one-way change to the database's shape, applied
  only if the database hasn't had it yet.
- **Nullable column** — a column allowed to hold nothing, which is what makes
  adding one to a table with data in it safe.
- **Additive change** — a change where nothing existing has to be edited,
  rewritten, or re-read to keep working.
- **Conditional rendering** — drawing a piece of UI only when there's something
  to draw (`{value && <p>…</p>}` in React).

---

**Next:** V2 — the shared probe layer. One response cache so eight agents never
fetch the same URL twice, a robots.txt gate, and a hard budget on how many
requests any agent may make.
