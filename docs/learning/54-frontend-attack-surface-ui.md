# 54 — Frontend: wiring in three new agents

> **Status:** done. The scan UI shows all 8 agents (five original, three new),
> and the subdomain agent has its own inventory table.

## What we built

The backend grew three new agents in the last few milestones — API Security,
Misconfiguration, Subdomain — but a scan you ran in the browser still only
*showed* five of them. This step catches the frontend up: the progress grid
now waits on all eight, and the subdomain agent's page gets a proper table of
every host it found, sortable by how many issues each one has.

## The one big idea: fetching data conditionally inside `useEffect`

Every agent's detail page (`/scan/[scanId]/agents/[agentName]`) fetches the
same two things for every agent: the agent's metadata and its findings. The
subdomain page needs one extra thing nothing else needs — the full scan
report, because that's where the subdomain inventory lives, not in the
agent's own findings list. Fetching it for every agent would be wasted work
for the other seven.

The fix is just an `if` inside the effect:

```javascript
useEffect(() => {
  fetchThing(id).then(setThing);   // runs for everyone

  if (id === "special-case") {
    fetchExtra(id).then(setExtra); // runs only when it's needed
  }
}, [id]);
```

Standalone example — nothing to do with agents or scans:

```javascript
useEffect(() => {
  fetchUser(userId).then(setUser);       // every profile page needs this

  if (userId === currentUserId) {
    fetchDrafts().then(setDrafts);       // only your own profile needs this
  }
}, [userId]);
```

A `useEffect` isn't one fixed request — it's just a function that runs after
render, so it can contain as much or as little logic as any other function,
including branches. Our real version, in
[`agents/[agentName]/page.tsx`](../../frontend/app/scan/%5BscanId%5D/agents/%5BagentName%5D/page.tsx):

```javascript
if (agentName === "subdomain") {
  fetchScan(scanId)
    .then((report) => setSubdomains(report.subdomains))
    .catch(() => setSubdomains([]));
}
```

Every other agent's page runs exactly the requests it always did — the branch
never executes for them, so there's no way this could slow down or break the
other seven pages.

## The actual code

**`lib/api.ts`** — a `SubdomainEntry` interface mirroring the backend's
Pydantic model field-for-field (`host`, `record_type`, `record_value`,
`source`, `http_status`, `scheme`, `tls_valid`, `server`, `redirects_to`,
`issue_count`), plus `ScanReport.subdomains: SubdomainEntry[]`.

**`ScanProgress.tsx`** — the fallback agent-name list grew from 5 to 8 slugs,
and the grid went from `sm:grid-cols-5` to `sm:grid-cols-4` so eight panels
sit as two even rows instead of an awkward 5-then-3. This list is only ever
used if the live `GET /agents` call is briefly unreachable — the real list
always comes from the API.

**`SubdomainTable.tsx`** (new) — one table, one bit of local state:

```javascript
const [sortDir, setSortDir] = useState("desc");
const sorted = useMemo(() => {
  const copy = [...entries];       // copy first!
  copy.sort((a, b) => sortDir === "desc"
    ? b.issue_count - a.issue_count
    : a.issue_count - b.issue_count);
  return copy;
}, [entries, sortDir]);
```

`entries` is a prop — data owned by a parent component. `Array.sort()`
rewrites an array *in place* instead of returning a new one, so calling it
directly on `entries` would silently mutate the parent's data out from under
it. `[...entries]` makes a fresh copy first, and only the copy gets sorted.
This is a common enough trap in React that it's worth knowing by name even
outside this project: never call `.sort()`, `.reverse()`, or `.push()`
straight on a prop or piece of state.

**Nothing else needed to change.** `AgentReel.tsx` already draws a plain
coloured plate for any agent it doesn't have artwork for, and `FindingRow.tsx`
already knows how to render `affected_url` and `confidence` — both built
generically two milestones ago, before either was strictly needed. That
earlier generality is why three brand-new agents showed up correctly without
touching either file.

Artwork for the three new agent plates is still outstanding — deliberately
not self-sourced; the fallback colour plate covers it until Arihant supplies
direction.

## Try it

- Run a live scan and watch the progress screen — eight panels, two rows of
  four, filling in whatever order they actually finish.
- Open the `subdomain` agent's page after a scan of a domain with subdomains,
  and click "Issues ↓" in the table header — the sort flips without
  re-fetching anything.
- Shrink the browser below `sm` width — the grid drops to two columns
  (`grid-cols-2`), confirming the breakpoint still works at 8 panels.

## Words worth knowing

- **Conditional fetch** — an `if` inside `useEffect` that only runs a
  particular request under some condition, instead of unconditionally.
- **Mutating in place** — an operation (like `.sort()`) that changes the
  original array/object rather than returning a new one.

---

**Next:** V9 — the full test matrix for the three new agents. Then V10 —
docs, remaining learning notes, and an end-to-end pass over the whole plan.
