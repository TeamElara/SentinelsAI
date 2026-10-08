# C8: optional AI leaves scans usable

Mocked Groq 401 and 429 responses leave scored findings and stored reports
intact. Reload reads the saved summary, including an empty unavailable result,
without retrying Groq. Successful summaries also survive reload unchanged.

Fix cache lookup now happens before checking the provider key, so an outage or
removed key does not hide existing advice. Invalid JSON shapes and invalid
suggestion fields return unavailable instead of raising or overwriting a cache.
Explicit regeneration remains the only way to replace a current-version fix.
