"""Two Dockerfile fixers for `agents/repo/config.py`'s `_check_dockerfile`.

`DockerRootUserFixer` (`docker-root-user-*`) -- inserts a non-root `USER`
before the container's entrypoint. Tier 2 (review-required): the base-image
detection below is a heuristic, not a certainty, so this always needs a
human to look at the diff before it merges.

PLAN-v5.md is explicit that this must not trust the finding's own
`line=1` placeholder -- the insertion point is found fresh, before the
*last* `CMD`/`ENTRYPOINT` instruction in the file (a multi-stage Dockerfile
can have several; only the final stage's entrypoint matters for the image
that actually ships).

`DockerLatestTagFixer` (`docker-latest-tag-*`) -- pins a floating `:latest`
(or implicit-latest, untagged) `FROM` to the digest it resolves to right
now. Same split `remediation/workflows.py`'s `WorkflowPinFixer` uses for the
same reason (PLAN-v5 conflict #3: "tag -> resolution needs the network"): a
pure line-rewrite function plus a resolver, mocked in tests. The resolver
only ever talks to Docker Hub -- a base image from any other registry
(`ghcr.io/...`, a self-hosted host:port, ...) declines rather than guessing
at a registry API this project has never verified against.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from models import Finding, FixPlan
from remediation.base import Fixer
from remediation.patch import make_patch
from remediation.source import FileSource

_FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.IGNORECASE | re.MULTILINE)
_USER_RE = re.compile(r"^\s*USER\s+", re.IGNORECASE)
_ENTRYPOINT_RE = re.compile(r"^\s*(CMD|ENTRYPOINT)\b", re.IGNORECASE)

# Keyed by the base-image family this Dockerfile's last FROM looks like.
# Alpine's BusyBox `adduser`/`addgroup` take different flags than the
# `useradd` most other common bases (Debian, Ubuntu, the official
# python/node images) ship -- getting this wrong produces a Dockerfile that
# doesn't build, which is worse than not fixing it at all.
_USER_BLOCK_BY_FAMILY = {
    "alpine": "RUN addgroup -S appgroup && adduser -S appuser -G appgroup\nUSER appuser\n",
    "debian": "RUN useradd --create-home --shell /usr/sbin/nologin appuser\nUSER appuser\n",
}


def _detect_family(dockerfile_text: str) -> str:
    """Guess a base-image family from the *last* FROM line -- the one that
    actually determines the final image in a multi-stage build. Defaults to
    "debian" (the `useradd` family): it's the shape most non-Alpine base
    images share, including the official `python`/`node` images, which are
    a safer default than assuming BusyBox tooling that usually isn't there.
    """
    images = _FROM_RE.findall(dockerfile_text)
    last_image = images[-1] if images else ""
    # Checked against the whole reference, tag included -- "alpine" almost
    # always shows up in the tag (python:3.12-alpine), not the image name.
    return "alpine" if "alpine" in last_image.lower() else "debian"


class DockerRootUserFixer(Fixer):
    slug = "docker-root-user"
    display_name = "Add a non-root USER"

    def handles(self, finding: Finding) -> bool:
        return finding.id.startswith("docker-root-user-")

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        if not finding.file_path:
            return None

        source = await files.get(finding.file_path)
        if source is None:
            return None  # the Dockerfile is gone since the scan ran

        lines = source.content.splitlines(keepends=True)
        if any(_USER_RE.match(line) for line in lines):
            return None  # already fixed since the scan ran

        insert_at = None
        for i, line in enumerate(lines):
            if _ENTRYPOINT_RE.match(line):
                insert_at = i  # keep updating -- we want the LAST match, not the first
        if insert_at is None:
            insert_at = len(lines)  # no CMD/ENTRYPOINT at all -- append at the end

        family = _detect_family(source.content)
        block = _USER_BLOCK_BY_FAMILY[family]

        new_lines = lines[:insert_at] + [block] + lines[insert_at:]
        new_content = "".join(new_lines)

        patch = make_patch(finding.file_path, "modify", source, new_content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=2,
            summary="Add a non-root USER before the container's entrypoint.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )


# ---- DockerLatestTagFixer --------------------------------------------------

_FROM_TOKEN_RE = re.compile(r"^\s*FROM\s+(\S+)", re.IGNORECASE)

_DOCKER_AUTH_URL = "https://auth.docker.io/token"
_DOCKER_REGISTRY_URL = "https://registry-1.docker.io"

# The Accept header order matters: a multi-arch manifest list/OCI index is
# asked for first, since that's what a plain `docker pull image:latest`
# actually resolves against for a normal published image -- the single-arch
# fallbacks exist for the (rarer) image that only ever publishes one.
_MANIFEST_ACCEPT = ", ".join([
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def docker_hub_repo(base_image: str) -> str | None:
    """Normalize a `FROM` line's image name (tag/digest already stripped) to
    the `namespace/repository` path Docker Hub's own registry API expects,
    or `None` if this doesn't look like a Docker Hub reference at all.

    Mirrors the same heuristic `docker`/`moby` itself uses to tell a
    registry host from a plain image name: the segment before the first
    `/` only counts as a registry host if it contains a `.` or `:`, or is
    exactly `localhost` -- `user/image` is a Docker Hub namespace, not a
    host named `user`. Anything that resolves to a *different* registry
    host declines: this fixer has only ever been verified against Docker
    Hub's own API shape.
    """
    first, sep, rest = base_image.partition("/")
    if not sep:
        return f"library/{first}"  # an official image, e.g. "node" -> "library/node"
    if "." in first or ":" in first or first == "localhost":
        if first in ("docker.io", "index.docker.io"):
            return rest if "/" in rest else f"library/{rest}"
        return None  # a different registry -- can't resolve confidently
    return base_image  # e.g. "user/image" -- already a Docker Hub repo path


async def resolve_latest_digest(client: httpx.AsyncClient, repo: str) -> str | None:
    """The immutable digest `repo:latest` currently resolves to on Docker
    Hub, or `None` if it can't be resolved -- an anonymous token request
    followed by a manifest HEAD-equivalent GET, the same two-request shape
    `docker pull` itself makes for a public image. `None` on any failure
    (missing repo, missing tag, a registry response with no digest) rather
    than raising -- an unresolvable image is exactly the "can't say this
    confidently" case `WorkflowPinFixer.plan` already treats as a decline,
    not an error.
    """
    token_response = await client.get(
        _DOCKER_AUTH_URL,
        params={"service": "registry.docker.io", "scope": f"repository:{repo}:pull"},
    )
    if token_response.status_code != 200:
        return None
    token = token_response.json().get("token")
    if not token:
        return None

    manifest_response = await client.get(
        f"{_DOCKER_REGISTRY_URL}/v2/{repo}/manifests/latest",
        headers={"Authorization": f"Bearer {token}", "Accept": _MANIFEST_ACCEPT},
    )
    if manifest_response.status_code != 200:
        return None  # repo or "latest" tag doesn't exist -- refuse to guess
    return manifest_response.headers.get("Docker-Content-Digest")


def rewrite_from_line(line: str, image_token: str, digest: str) -> str | None:
    """Replace one `FROM` line's image reference with `{name}:latest@{digest}`,
    preserving everything else on the line (a multi-stage `AS alias`,
    indentation, the line ending) -- the same "keep the human-readable name,
    add the immutable anchor" shape `rewrite_uses_line` uses for a `uses:`
    line.

    `image_token` must be the *exact* substring `plan()` just matched on
    this same line -- re-verified with `.find()` here, the same
    independent-of-the-caller check `rewrite_uses_line` makes, rather than
    trusting that nothing between the two calls changed.
    """
    idx = line.find(image_token)
    if idx == -1:
        return None
    if "@" in image_token:
        return None  # already pinned to a digest

    name = image_token.rsplit(":", 1)[0] if ":" in image_token else image_token
    before = line[:idx]
    after = line[idx + len(image_token):]
    return f"{before}{name}:latest@{digest}{after}"


class DockerLatestTagFixer(Fixer):
    slug = "docker-latest-tag"
    display_name = "Pin the floating :latest tag"

    def handles(self, finding: Finding) -> bool:
        return finding.id.startswith("docker-latest-tag-")

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        if not finding.file_path or not finding.line:
            return None

        source = await files.get(finding.file_path)
        if source is None:
            return None  # the Dockerfile is gone since the scan ran

        lines = source.content.splitlines(keepends=True)
        if finding.line < 1 or finding.line > len(lines):
            return None
        line = lines[finding.line - 1]

        match = _FROM_TOKEN_RE.match(line)
        if match is None:
            return None  # no longer a FROM line -- edited since the scan
        image_token = match.group(1)

        if "@" in image_token:
            return None  # already pinned to a digest

        base, tag = image_token.rsplit(":", 1) if ":" in image_token else (image_token, "latest")
        if tag != "latest":
            return None  # no longer floating -- edited since the scan

        repo = docker_hub_repo(base)
        if repo is None:
            return None  # not a Docker Hub reference -- can't resolve confidently

        digest = await resolve_latest_digest(files.client, repo)
        if digest is None:
            return None  # couldn't resolve -- refuse to guess rather than write a wrong digest

        new_line = rewrite_from_line(line, image_token, digest)
        if new_line is None or new_line == line:
            return None

        new_lines = list(lines)
        new_lines[finding.line - 1] = new_line
        new_content = "".join(new_lines)

        patch = make_patch(finding.file_path, "modify", source, new_content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=2,
            summary=f"Pin {base}:latest to digest {digest[:19]}... (Docker Hub).",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
