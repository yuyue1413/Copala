"""Tool for saving learned, reusable skills into the workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.utils.helpers import ensure_dir


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_SCRIPT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SECRET_HINT_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|authorization|EMAIL_PASS)\s*=",
    re.IGNORECASE,
)


def _slug(value: str, *, fallback: str = "general") -> str:
    value = _SLUG_RE.sub("-", value.lower()).strip("-_")
    return value or fallback


def _script_name(value: str) -> str:
    name = Path(value or "helper.py").name
    name = _SCRIPT_RE.sub("_", name).strip("._")
    if not name:
        name = "helper.py"
    if "." not in name:
        name += ".py"
    return name


class LearnSkillTool(Tool):
    """Persist reusable scripts and SKILL.md instructions as learned skills."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    @property
    def name(self) -> str:
        return "learn_skill"

    @property
    def description(self) -> str:
        return (
            "Save a reusable skill learned while solving a task. Stores scripts under "
            "learned/scripts/<category>/ and creates skills/learned-<skill>/SKILL.md "
            "so the agent can discover and reuse it later. Actions: save, list."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "list"]},
                "skill_name": {
                    "type": "string",
                    "description": "Short reusable skill name, e.g. bilibili-lookup or weather-query.",
                },
                "description": {
                    "type": "string",
                    "description": "When this learned skill should be used.",
                },
                "category": {
                    "type": "string",
                    "description": "Task category for script storage, e.g. bilibili, weather, email.",
                },
                "script_name": {
                    "type": "string",
                    "description": "Optional script filename to save under learned/scripts/<category>/.",
                },
                "script_content": {
                    "type": "string",
                    "description": "Optional reusable script content.",
                },
                "usage": {
                    "type": "string",
                    "description": "How to use the learned skill or script.",
                },
                "notes": {
                    "type": "string",
                    "description": "Safety notes, limitations, dependencies, or examples.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Overwrite existing script/SKILL.md if true.",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        skill_name: str = "",
        description: str = "",
        category: str = "general",
        script_name: str = "",
        script_content: str = "",
        usage: str = "",
        notes: str = "",
        overwrite: bool = False,
        **kwargs: Any,
    ) -> str:
        if action == "list":
            return self._list()
        if action != "save":
            return f"Unknown action: {action}"
        if not skill_name:
            return "Error: skill_name is required for save"
        if not description:
            return "Error: description is required for save"
        return self._save(
            skill_name=skill_name,
            description=description,
            category=category,
            script_name=script_name,
            script_content=script_content,
            usage=usage,
            notes=notes,
            overwrite=overwrite,
        )

    def _save(
        self,
        *,
        skill_name: str,
        description: str,
        category: str,
        script_name: str,
        script_content: str,
        usage: str,
        notes: str,
        overwrite: bool,
    ) -> str:
        skill_slug = _slug(skill_name, fallback="skill")
        category_slug = _slug(category, fallback="general")
        learned_name = f"learned-{skill_slug}" if not skill_slug.startswith("learned-") else skill_slug

        script_rel = ""
        warnings = []
        if script_content:
            if _SECRET_HINT_RE.search(script_content):
                warnings.append(
                    "script appears to contain credential-like assignments; review before reuse"
                )
            script_file = self._safe_path(
                self.workspace / "learned" / "scripts" / category_slug / _script_name(script_name)
            )
            if script_file.exists() and not overwrite:
                return f"Error: script already exists: {script_file}. Set overwrite=true to replace it."
            ensure_dir(script_file.parent)
            script_file.write_text(script_content, encoding="utf-8")
            script_rel = self._rel(script_file)

        skill_dir = self._safe_path(self.workspace / "skills" / learned_name)
        skill_file = self._safe_path(skill_dir / "SKILL.md")
        if skill_file.exists() and not overwrite:
            return f"Error: skill already exists: {skill_file}. Set overwrite=true to replace it."
        ensure_dir(skill_dir)
        skill_file.write_text(
            self._skill_markdown(
                learned_name=learned_name,
                description=description,
                category=category_slug,
                script_rel=script_rel,
                usage=usage,
                notes=notes,
                warnings=warnings,
            ),
            encoding="utf-8",
        )

        ensure_dir(self.workspace / "learned")
        self._update_manifest(learned_name, category_slug, description, script_rel, warnings)
        result = [
            f"Saved learned skill: {learned_name}",
            f"Skill file: {self._rel(skill_file)}",
        ]
        if script_rel:
            result.append(f"Script file: {script_rel}")
        if warnings:
            result.append("Warnings: " + "; ".join(warnings))
        return "\n".join(result)

    def _list(self) -> str:
        skills_dir = self.workspace / "skills"
        if not skills_dir.exists():
            return "No learned skills found."
        learned = sorted(
            p.name for p in skills_dir.iterdir()
            if p.is_dir() and p.name.startswith("learned-") and (p / "SKILL.md").exists()
        )
        if not learned:
            return "No learned skills found."
        return "Learned skills:\n" + "\n".join(f"- {name}" for name in learned)

    def _skill_markdown(
        self,
        *,
        learned_name: str,
        description: str,
        category: str,
        script_rel: str,
        usage: str,
        notes: str,
        warnings: list[str],
    ) -> str:
        usage_text = usage.strip() or "Read this SKILL.md, inspect referenced scripts if present, then adapt them to the current task."
        notes_text = notes.strip() or "This is a locally learned workspace skill created from a prior solved task."
        script_text = (
            f"- `{script_rel}`\n"
            if script_rel
            else "- No script was saved; this skill records reusable procedure only.\n"
        )
        warning_text = "\n".join(f"- {w}" for w in warnings) if warnings else "- No special warnings recorded."
        return f"""---
name: {learned_name}
description: {description}
always: false
---

# Learned Skill: {learned_name}

Use this skill when: {description}

## Category

`{category}`

## Reusable Assets

{script_text}
## Usage

{usage_text}

## Notes

{notes_text}

## Safety

{warning_text}
"""

    def _update_manifest(
        self,
        learned_name: str,
        category: str,
        description: str,
        script_rel: str,
        warnings: list[str],
    ) -> None:
        manifest = self.workspace / "learned" / "manifest.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
        else:
            data = {}
        skills = data.setdefault("skills", {})
        skills[learned_name] = {
            "category": category,
            "description": description,
            "script": script_rel,
            "skill": f"skills/{learned_name}/SKILL.md",
            "warnings": warnings,
        }
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            raise PermissionError(f"Refusing to write outside workspace: {path}")
        return resolved

    def _rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.workspace)).replace("\\", "/")
