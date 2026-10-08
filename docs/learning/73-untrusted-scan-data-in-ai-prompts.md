# C10: separate scanner observations from AI instructions

URLs, file paths, DNS values, finding titles and evidence can come from an
attacker. All five prompt builders keep these values in user-role data,
surrounded by explicit untrusted-data delimiters and JSON encoded. The chat
system message is now a fixed constant; the digest no longer extends it.
Stored chat history accepts only user/assistant roles and strips extra fields.

Prompt version v4 invalidates prior fix caches. Offline regression tests put
instruction-like strings in evidence, URLs, filenames and DNS values and check
the actual outbound messages. This verifies the application boundary; it does
not prove that every possible model response resists prompt injection.
