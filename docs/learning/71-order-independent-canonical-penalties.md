# Canonical penalties, independent of agent timing

The same four High findings previously scored 65 or 80. Equal-severity
duplicates retained whichever agent arrived first; that agent determined the
cap. Streaming made completion order part of the result.

Canonical HSTS/CSP findings now belong to Headers and canonical certificate
validity findings to TLS, including alias-only observations. Their penalties
are uncapped. Other API, subdomain and misconfiguration checks retain their
per-agent caps. Equal-severity representatives use a stable provenance key.

This intentionally makes three subdomain HSTS observations cost 29 points
(15 + 7 + 7), instead of being limited to the subdomain agent's 20-point cap.
The policy is explicit in the final launch plan; do not quietly reintroduce
the old cap through the reporting agent's name.

Regression checks enumerate all permutations of the reproduced fixture,
duplicate each permutation, cover alias-only observations and unknown issue
ties, and compare streaming with non-streaming orchestration. The reproduced
case is always 65. Severity weights, repeat decay and grade cutoffs are unchanged.
