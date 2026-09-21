"""Fixer for the four `missing-*` header findings (`agents/headers.py`) --
PLAN-v5 Stage D, the only Fixer whose finding never carries a `file_path`
(a header finding came from observing a live site, not a repository). It
only ever runs once a URL scan has been linked to a repository
(`remediation/linking.py`), and only against Vercel (`vercel.json`), Netlify
(`netlify.toml`), nginx (`nginx.conf`) and Next.js (`next.config.*`).

Tier 2 (review-required): a header value this fixer writes is a reasonable
default, not a value the site's own maintainer chose, so a human should
look at it before it merges -- same reasoning `dockerfile.py`'s base-image
guess carries.
"""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone

from models import Finding, FixPlan
from remediation.base import Fixer
from remediation.patch import make_patch
from remediation.source import FileSource
from remediation.stack import StackKind, StackResult, detect_stack

# finding.id -> the one header this fixer knows how to add for it.
_HEADER_BY_FINDING: dict[str, tuple[str, str]] = {
    "missing-csp": ("Content-Security-Policy", "default-src 'self'"),
    "missing-hsts": (
        "Strict-Transport-Security",
        "max-age=31536000; includeSubDomains",
    ),
    "missing-x-content-type-options": ("X-Content-Type-Options", "nosniff"),
    "missing-x-frame-options": ("X-Frame-Options", "DENY"),
}

# The finding ids this fixer knows about -- the exact set Stage D's
# verification bridge (`remediation/verify.py`) is allowed to re-run a URL
# agent for, since those are the only URL findings that can ever have gone
# through a linked-repo PR.
FIXABLE_FINDING_IDS: frozenset[str] = frozenset(_HEADER_BY_FINDING)

# All four, in the same fixed order, for the "write every header this fixer
# knows about in one pass" cases below (see the module docstring on
# `remediation/stack.py`'s STAGE D scope note in PLAN-v5.md: the first
# finding to actually touch the file writes all four, so the other three
# correctly find `headers(` already defined and decline -- no duplicate
# writes to the same config file across a batch).
_ALL_HEADERS = list(_HEADER_BY_FINDING.values())

_VERCEL_SOURCE = "/(.*)"

_NGINX_SERVER_RE = re.compile(r"^\s*server\s*\{")
_NGINX_ADD_HEADER_RE = re.compile(r"^\s*add_header\s+([\w-]+)", re.MULTILINE)

_NEXT_EXPORT_OBJECT_RE = re.compile(
    r"^(module\.exports\s*=\s*\{|export\s+default\s*\{|const\s+\w+(?:\s*:\s*\w+)?\s*=\s*\{)"
)


class SecurityHeaderFixer(Fixer):
    slug = "security-headers"
    display_name = "Add the missing security header"

    def handles(self, finding: Finding) -> bool:
        return finding.id in _HEADER_BY_FINDING

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        stack = await detect_stack(files)
        if stack is None:
            return None  # no recognized stack -- decline rather than guess

        if stack.kind == StackKind.VERCEL:
            patch = self._plan_vercel(stack)
        elif stack.kind == StackKind.NETLIFY:
            patch = self._plan_netlify(stack)
        elif stack.kind == StackKind.NGINX:
            patch = self._plan_nginx(stack)
        else:
            patch = self._plan_next(stack)

        if patch is None:
            return None

        header_name, _ = _HEADER_BY_FINDING[finding.id]
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=2,
            summary=f"Add {header_name} via {stack.path}.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def _plan_vercel(self, stack: StackResult):
        assert stack.existing is not None  # detect_stack only returns VERCEL when it exists
        try:
            data = json.loads(stack.existing.content)
        except json.JSONDecodeError:
            return None  # malformed JSON -- not safe to guess at

        if not isinstance(data, dict):
            return None

        headers_list = data.setdefault("headers", [])
        if not isinstance(headers_list, list):
            return None

        entry = next(
            (h for h in headers_list if isinstance(h, dict) and h.get("source") == _VERCEL_SOURCE),
            None,
        )
        if entry is None:
            entry = {"source": _VERCEL_SOURCE, "headers": []}
            headers_list.append(entry)

        entry_headers = entry.setdefault("headers", [])
        existing_keys = {h.get("key") for h in entry_headers if isinstance(h, dict)}

        added = False
        for key, value in _ALL_HEADERS:
            if key not in existing_keys:
                entry_headers.append({"key": key, "value": value})
                added = True

        if not added:
            return None  # every header this fixer knows about is already there

        new_content = json.dumps(data, indent=2) + "\n"
        return make_patch(stack.path, "modify", stack.existing, new_content)

    def _plan_netlify(self, stack: StackResult):
        assert stack.existing is not None
        try:
            data = tomllib.loads(stack.existing.content)
        except tomllib.TOMLDecodeError:
            return None  # malformed TOML -- not safe to append to

        already = set()
        for block in data.get("headers", []) if isinstance(data.get("headers"), list) else []:
            values = block.get("values") if isinstance(block, dict) else None
            if isinstance(values, dict):
                already.update(k.lower() for k in values)

        missing = [(k, v) for k, v in _ALL_HEADERS if k.lower() not in already]
        if not missing:
            return None

        # Appending a fresh [[headers]] table is valid TOML wherever the
        # file currently ends, and Netlify merges every matching block.
        content = stack.existing.content
        if content and not content.endswith("\n"):
            content += "\n"
        block = '\n[[headers]]\n  for = "/*"\n  [headers.values]\n'
        block += "".join(f'    {k} = "{v}"\n' for k, v in missing)
        return make_patch(stack.path, "modify", stack.existing, content + block)

    def _plan_nginx(self, stack: StackResult):
        assert stack.existing is not None
        content = stack.existing.content
        already = {m.lower() for m in _NGINX_ADD_HEADER_RE.findall(content)}
        missing = [(k, v) for k, v in _ALL_HEADERS if k.lower() not in already]
        if not missing:
            return None

        lines = content.splitlines(keepends=True)
        server_at = next((i for i, ln in enumerate(lines) if _NGINX_SERVER_RE.match(ln)), None)
        if server_at is None:
            return None  # no server block to put directives in -- decline

        indent = re.match(r"\s*", lines[server_at]).group(0) + "    "
        added = [f'{indent}add_header {k} "{v}" always;\n' for k, v in missing]
        new_content = "".join(lines[: server_at + 1] + added + lines[server_at + 1 :])
        return make_patch(stack.path, "modify", stack.existing, new_content)

    def _plan_next(self, stack: StackResult):
        if stack.existing is None:
            return make_patch(stack.path, "create", None, _next_config_template())

        content = stack.existing.content
        if "headers(" in content:
            return None  # already has its own headers() -- too risky to merge into

        lines = content.splitlines(keepends=True)
        insert_at = None
        for i, line in enumerate(lines):
            if _NEXT_EXPORT_OBJECT_RE.match(line.strip()):
                insert_at = i
                break
        if insert_at is None:
            return None  # couldn't find the exported config object -- decline

        block = "".join(f"  {line}\n" for line in _next_headers_property().splitlines())
        new_lines = lines[: insert_at + 1] + [block] + lines[insert_at + 1 :]
        new_content = "".join(new_lines)
        return make_patch(stack.path, "modify", stack.existing, new_content)


def _headers_array_literal(indent: str) -> str:
    entries = ",\n".join(
        f'{indent}      {{ key: "{key}", value: "{value}" }}' for key, value in _ALL_HEADERS
    )
    return (
        f"{indent}return [\n"
        f"{indent}  {{\n"
        f'{indent}    source: "/(.*)",\n'
        f"{indent}    headers: [\n"
        f"{entries}\n"
        f"{indent}    ],\n"
        f"{indent}  }},\n"
        f"{indent}];"
    )


def _next_headers_property() -> str:
    return "async headers() {\n" + _headers_array_literal("") + "\n},"


def _next_config_template() -> str:
    return (
        'import type { NextConfig } from "next";\n'
        "\n"
        "const nextConfig: NextConfig = {\n"
        "  async headers() {\n"
        f"{_headers_array_literal('  ')}\n"
        "  },\n"
        "};\n"
        "\n"
        "export default nextConfig;\n"
    )
