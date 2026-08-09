"""Data only — no logic. Known "dangling CNAME" providers and the exact
fingerprint text their unclaimed-resource page shows.

This is the difference between a guess and a claim: a CNAME pointing at
`something.github.io` is not, by itself, evidence of anything — it's simply
how GitHub Pages works for millions of legitimate sites. It only becomes a
takeover *signal* when the page actually served back is the specific "this
isn't claimed" page these providers show for an unregistered custom domain.
`agents/subdomain.py` never treats a suffix match alone as a finding.

Fingerprints are illustrative, sourced from public "can-i-take-over-xyz"
references, and can drift as providers change their error pages — a missed
match just means one less inventory annotation, never a false claim.
"""
from __future__ import annotations

TAKEOVER_SIGNATURES: list[dict[str, str]] = [
    {"provider": "GitHub Pages", "cname_suffix": "github.io", "fingerprint": "There isn't a GitHub Pages site here"},
    {"provider": "Amazon S3", "cname_suffix": "s3.amazonaws.com", "fingerprint": "The specified bucket does not exist"},
    {"provider": "Heroku", "cname_suffix": "herokuapp.com", "fingerprint": "No such app"},
    {"provider": "Azure", "cname_suffix": "azurewebsites.net", "fingerprint": "404 Web Site not found"},
    {"provider": "Netlify", "cname_suffix": "netlify.app", "fingerprint": "Not Found - Request ID"},
    {"provider": "Shopify", "cname_suffix": "myshopify.com", "fingerprint": "Sorry, this shop is currently unavailable"},
    {"provider": "Ghost (Pro)", "cname_suffix": "ghost.io", "fingerprint": "The thing you were looking for is no longer here"},
    {"provider": "WordPress.com", "cname_suffix": "wordpress.com", "fingerprint": "Do you want to register"},
    {"provider": "Surge.sh", "cname_suffix": "surge.sh", "fingerprint": "project not found"},
    {"provider": "Bitbucket", "cname_suffix": "bitbucket.io", "fingerprint": "Repository not found"},
    {"provider": "Read the Docs", "cname_suffix": "readthedocs.io", "fingerprint": "unknown to Read the Docs"},
    {"provider": "Fastly", "cname_suffix": "fastly.net", "fingerprint": "Fastly error: unknown domain"},
]


def match_provider(cname_target: str) -> dict[str, str] | None:
    """Return the signature dict whose suffix matches `cname_target`, or
    `None`. A suffix match alone is never a finding — see module docstring."""
    lowered = cname_target.lower().rstrip(".")
    for signature in TAKEOVER_SIGNATURES:
        if lowered.endswith(signature["cname_suffix"]):
            return signature
    return None
