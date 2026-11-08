"""Project skills: a directory of instructions the model loads on demand.

A skill is one folder with a ``SKILL.md`` inside it. The frontmatter holds a
name and a short description; the body holds the actual instructions. Only the
name and description go into the system prompt. The body is loaded by the
``load_project_skill`` tool when the model decides the skill matches the task.

That split is the point. A repository with twenty skills would blow the window
if every body were resident, and a body the model did not need is noise that
competes with the instructions it does need. Progressive disclosure keeps the
resident cost proportional to *how many* skills exist, not how large they are.
Discovery reads only a bounded metadata prefix for the prompt; the full body is
read only when the loader is called.

The tool is named ``load_project_skill``, not ``load_skill``. ``load_skill``
already belongs to the app's Skills Library (``core/tool_index.py``), and a
harness-registered name shadows the legacy dispatcher in ``core/tool_exec.py``.
Two different catalogues must not share one function name.

Discovery is deliberately narrow: ``.brahma/skills/<name>/SKILL.md`` in the
workspace, the same directory in up to three ancestors (a package inside a
monorepo inherits the monorepo's skills), and one user-level directory. The
nearest definition wins when two roots define the same name; the shadowed one is
reported as a diagnostic rather than silently changing behaviour.

Skills are trusted project input, in the same sense as ``AGENTS.md``. The body
is instructions for the model, not code the harness executes. ``allowed-tools``
is parsed and exposed for humans and future policy work but is **not** enforced:
the runtime's policy remains the only execution boundary, and a second
tool-filter path here would be a hole, not a feature.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .spec import ToolSpec, register

#: The one file a skill directory must contain.
SKILL_FILE = "SKILL.md"

#: The registry name. Deliberately distinct from the Skills Library's
#: ``load_skill``: the harness dispatcher runs first, so a shared name would
#: break the legacy tool instead of adding a second one.
LOAD_SKILL_TOOL = "load_project_skill"

#: A skill body is prompt input. Anything bigger is a document, not a skill;
#: refuse it rather than reading megabytes on every discovery pass.
MAX_SKILL_BYTES = 256 * 1024

#: How much of a file the resident-prompt pass reads. Frontmatter and the first
#: heading live at the top; reading a 256 KB body just to list a name would make
#: discovery proportional to body size, which is the thing this module avoids.
METADATA_BYTES = 8 * 1024

#: How many skills the resident list mentions before it says "and N more".
MAX_PROMPT_SKILLS = 24

#: Description budget per skill in the resident list.
MAX_PROMPT_DESCRIPTION = 200

#: Total resident budget for the whole skills block.
MAX_PROMPT_CHARS = 4_000

#: Parent directories searched for ``.brahma/skills``. Same bound as
#: instruction discovery: enough for a monorepo, not the filesystem root.
MAX_SKILL_PARENTS = 3

#: OpenAI function names and this project's registry allow only this grammar.
#: The stricter hyphen-only spelling used by some skill ecosystems is accepted
#: too, because it is a subset.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillError(ValueError):
    """One skill file is malformed. Discovery turns this into a diagnostic."""


@dataclass(frozen=True)
class Skill:
    """One discovered skill."""

    name: str
    description: str
    body: str
    path: Path
    directory: Path
    allowed_tools: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_body: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "allowed_tools": list(self.allowed_tools),
        }
        if include_body:
            payload["body"] = self.body
        return payload


@dataclass(frozen=True)
class SkillCatalog:
    """The result of one discovery pass."""

    skills: Tuple[Skill, ...] = ()
    diagnostics: Tuple[str, ...] = ()
    roots: Tuple[Path, ...] = ()

    def get(self, name: str) -> Optional[Skill]:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roots": [str(root) for root in self.roots],
            "skills": [skill.to_dict() for skill in self.skills],
            "diagnostics": list(self.diagnostics),
        }


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _parse_header(header: str) -> Dict[str, Any]:
    """Parse the small YAML subset a ``SKILL.md`` frontmatter needs.

    Supported: ``key: value``, quoted scalars, block lists (``- item``) and
    inline lists (``[a, b]``). A full YAML parser would be a dependency the
    stdlib-only harness cannot take, and skill metadata does not need anchors,
    multi-document streams or typed scalars.
    """
    meta: Dict[str, Any] = {}
    current: Optional[str] = None
    for raw in header.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current is not None:
            value = _unquote(stripped[2:])
            existing = meta.get(current)
            if isinstance(existing, list):
                existing.append(value)
            else:
                meta[current] = [value]
            continue
        if ":" not in line:
            # A continuation line belongs to the current scalar (fold it).
            if current is not None and isinstance(meta.get(current), str):
                meta[current] = f"{meta[current]} {stripped}".strip()
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace("_", "-")
        current = key
        value = value.strip()
        if not value:
            meta[key] = []
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [
                _unquote(item) for item in inner.split(",") if item.strip()
            ]
        else:
            meta[key] = _unquote(value)
    return meta


def _split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """``(metadata, body)``; text without a closed fence is all body."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return _parse_header("".join(lines[1:index])), "".join(lines[index + 1 :])
    return {}, text


def _derive_description(body: str) -> str:
    for line in body.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:MAX_PROMPT_DESCRIPTION]
    return "No description provided."


def _as_string_list(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return tuple(out)
    raise SkillError("'allowed-tools' must be a string or a list of strings")


def parse_skill(
    path: Path,
    *,
    directory: Optional[Path] = None,
    with_body: bool = True,
) -> Skill:
    """Read and validate one ``SKILL.md``.

    ``with_body=False`` reads only :data:`METADATA_BYTES` and leaves
    ``Skill.body`` empty. The resident-prompt pass uses it so listing a skill
    never costs a read proportional to the body size. ``utf-8-sig`` strips a
    BOM; without it a BOM-prefixed file silently loses its frontmatter and
    advertises ``---`` as its description.
    """
    path = Path(path)
    folder = Path(directory) if directory is not None else path.parent
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SkillError(f"cannot stat file: {exc}") from exc
    if size > MAX_SKILL_BYTES:
        raise SkillError(
            f"file is {size} bytes; the limit is {MAX_SKILL_BYTES}"
        )
    limit = MAX_SKILL_BYTES if with_body else METADATA_BYTES
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read(limit)
    except OSError as exc:
        raise SkillError(f"cannot read file: {exc}") from exc

    meta, raw_body = _split_frontmatter(text)
    body = raw_body if with_body else ""
    name = str(meta.get("name") or folder.name).strip()
    if not NAME_RE.fullmatch(name):
        raise SkillError(
            f"invalid skill name {name!r}; use lowercase letters, digits, "
            "hyphens or underscores (1-64 characters)"
        )
    if folder.name != name:
        raise SkillError(
            f"directory {folder.name!r} does not match frontmatter name {name!r}"
        )
    description = str(meta.get("description") or "").strip()
    if not description:
        description = _derive_description(raw_body)
    description = " ".join(description.split())[:MAX_PROMPT_DESCRIPTION]
    if not description:
        description = "No description provided."
    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
        directory=folder,
        allowed_tools=_as_string_list(meta.get("allowed-tools")),
        metadata=dict(meta),
    )


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser()


def _parent_dirs(root: Path, levels: int = MAX_SKILL_PARENTS) -> List[Path]:
    dirs = [root]
    parent = root.parent
    for _ in range(levels):
        if parent == parent.parent or parent in dirs:
            break
        dirs.append(parent)
        parent = parent.parent
    return dirs


def user_skills_root() -> Path:
    """Where a user keeps skills that apply to every project."""
    local = os.getenv("LOCALAPPDATA")
    if local:
        return Path(local) / "BrahmaAI" / "skills"
    return Path.home() / ".config" / "brahma" / "skills"


def project_roots(workspace: Optional[str | Path]) -> List[Path]:
    """Workspace-scoped skill directories, nearest first.

    ``BRAHMA_SKILLS_DIR`` replaces this walk entirely (``os.pathsep``-separated),
    mirroring ``BRAHMA_MCP_CONFIG``: a test or a deployment can point at exactly
    one tree and pay for no other discovery.

    This is the one definition of "where a project's skills live". The harness
    uses it for discovery, and ``core.skills_library`` uses it to make the same
    project skills visible to ``search_skills``/``load_skill`` (D42), so the two
    surfaces cannot disagree about which directories are in scope.
    """
    override = os.getenv("BRAHMA_SKILLS_DIR")
    if override is not None:
        return [
            Path(part).expanduser()
            for part in override.split(os.pathsep)
            if part.strip()
        ]
    roots: List[Path] = []
    if workspace is not None:
        for directory in _parent_dirs(_resolved(workspace)):
            roots.append(directory / ".brahma" / "skills")
            repo_skills = directory / "skills"
            if repo_skills.is_dir():
                roots.append(repo_skills)
    return roots


def default_roots(workspace: Optional[str | Path]) -> List[Path]:
    """Skill directories, nearest first: project roots, then the user root.

    An explicit ``BRAHMA_SKILLS_DIR`` override is the whole list — the user
    directory is not appended — so a deployment that pins one tree gets exactly
    that tree.
    """
    roots = project_roots(workspace)
    if os.getenv("BRAHMA_SKILLS_DIR") is None:
        roots.append(user_skills_root())
    return roots


def discover(
    workspace: Optional[str | Path] = None,
    *,
    roots: Optional[Sequence[str | Path]] = None,
    with_body: bool = True,
) -> SkillCatalog:
    """Read every skill under ``roots`` (or the default roots).

    A malformed skill is a diagnostic, never an exception: one bad file in a
    repository must not disable the other skills or break the prompt.
    ``with_body=False`` is the cheap listing pass; the loader re-discovers with
    full bodies so it always returns the current file.
    """
    selected = (
        [Path(root).expanduser() for root in roots]
        if roots is not None
        else default_roots(workspace)
    )
    skills: List[Skill] = []
    diagnostics: List[str] = []
    by_name: Dict[str, Skill] = {}

    for root in selected:
        root = Path(root)
        try:
            if not root.is_dir():
                continue
            entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            diagnostics.append(f"{root}: cannot list skills: {exc}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
            except OSError:
                continue
            path = entry / SKILL_FILE
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            try:
                skill = parse_skill(path, directory=entry, with_body=with_body)
            except SkillError as exc:
                diagnostics.append(f"{path}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - never break discovery
                diagnostics.append(f"{path}: {exc}")
                continue
            existing = by_name.get(skill.name)
            if existing is not None:
                try:
                    if existing.path.read_bytes() == path.read_bytes():
                        continue
                except OSError:
                    pass
                diagnostics.append(
                    f"{path}: skill {skill.name!r} is already defined by "
                    f"{existing.path}; this copy is ignored"
                )
                continue
            by_name[skill.name] = skill
            skills.append(skill)

    return SkillCatalog(
        skills=tuple(skills),
        diagnostics=tuple(diagnostics),
        roots=tuple(selected),
    )


# --------------------------------------------------------------------------- #
# Resident prompt block
# --------------------------------------------------------------------------- #

def prompt_block(
    workspace: Optional[str | Path],
    *,
    max_skills: int = MAX_PROMPT_SKILLS,
    max_chars: int = MAX_PROMPT_CHARS,
) -> str:
    """The name/description list placed in the system prompt.

    Bodies are never included, and discovery reads only the metadata prefix.
    ``max_chars`` keeps a repository with unusually long descriptions from
    crowding the instructions the model actually needs.
    """
    catalog = discover(workspace, with_body=False)
    if not catalog.skills:
        return ""

    lines = [
        "## Project skills",
        "",
        "Task-specific instructions kept in `.brahma/skills`. When a description "
        f"matches the work, call `{LOAD_SKILL_TOOL}` with the skill name and "
        "follow the returned instructions. Do not guess a skill's contents.",
        "",
    ]
    used = sum(len(line) + 1 for line in lines)
    shown = 0
    for skill in catalog.skills[: max(0, max_skills)]:
        entry = f"- `{skill.name}`: {skill.description}"
        if used + len(entry) + 1 > max_chars:
            break
        lines.append(entry)
        used += len(entry) + 1
        shown += 1

    remaining = len(catalog.skills) - shown
    if remaining > 0:
        lines.append(f"- {remaining} more skill(s) are installed.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tool
# --------------------------------------------------------------------------- #

def _load_skill(arguments: Dict[str, Any], context: Any) -> str:
    raw = arguments.get("name") if isinstance(arguments, Mapping) else None
    name = str(raw or "").strip()
    if not name:
        return (
            f"error: 'name' is required (call {LOAD_SKILL_TOOL} with a skill name)"
        )
    catalog = discover(context.workspace)
    skill = catalog.get(name)
    if skill is None:
        available = ", ".join(s.name for s in catalog.skills) or "none"
        return f"error: no skill named {name!r}. Available skills: {available}"
    body = skill.body.strip()
    if not body:
        return f"# Skill: {skill.name}\n\n(source: {skill.path})\n\n(no instructions)"

    try:
        from core.user_paths import is_workspace_trusted
        trusted = is_workspace_trusted(context.workspace)
    except Exception:
        trusted = True

    if not trusted:
        from agent.prompts import wrap_untrusted_instructions
        body = wrap_untrusted_instructions(body)

    return (
        f"# Skill: {skill.name}\n\n"
        f"Source: {skill.path}\n"
        f"Skill directory: {skill.directory}\n\n"
        f"{body}"
    )


register(
    ToolSpec(
        name=LOAD_SKILL_TOOL,
        description=(
            "Load a project skill's full instructions by name. The system prompt "
            "lists the project skills kept in .brahma/skills and what each is "
            "for; call this only when one matches the task, then follow its "
            "instructions. This is not the Skills Library tool (load_skill)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact skill name from the system prompt list.",
                }
            },
            "required": ["name"],
        },
        tool_class="read",
        handler=_load_skill,
        tags=("skill",),
        max_output_chars=MAX_SKILL_BYTES,
    )
)


__all__ = [
    "LOAD_SKILL_TOOL",
    "MAX_PROMPT_CHARS",
    "MAX_PROMPT_DESCRIPTION",
    "MAX_PROMPT_SKILLS",
    "MAX_SKILL_BYTES",
    "METADATA_BYTES",
    "NAME_RE",
    "SKILL_FILE",
    "Skill",
    "SkillCatalog",
    "SkillError",
    "default_roots",
    "discover",
    "parse_skill",
    "project_roots",
    "prompt_block",
    "user_skills_root",
]
