"""Repository documentation and sanitized-example checks."""

import json
import re
from pathlib import Path

from copala.config.schema import Config

ROOT = Path(__file__).parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def test_markdown_relative_links_resolve():
    files = [ROOT / "README.md", ROOT / "README_EN.md", ROOT / "SECURITY.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend(
        ROOT / name
        for name in ("CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SUPPORT.md", "NOTICE.md")
    )
    failures: list[str] = []
    for document in files:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or re.match(r"^(?:https?://|mailto:)", target):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {raw_target}")
    assert not failures, "Broken relative Markdown links:\n" + "\n".join(failures)


def test_active_docs_do_not_contain_machine_specific_paths():
    files = [ROOT / "README.md", ROOT / "README_EN.md"]
    files.extend(path for path in (ROOT / "docs").glob("*.md"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert re.search(r"(?i)\b[A-Z]:\\(?:Users|Environment|Coder)\\", text) is None


def test_example_config_is_valid_and_uses_placeholder_credentials():
    raw = (ROOT / "examples" / "config.example.json").read_text(encoding="utf-8")
    config = Config.model_validate(json.loads(raw))
    assert config.tools.restrict_to_workspace is True
    assert config.providers.openrouter.api_key == "YOUR_OPENROUTER_API_KEY"
    assert "sk-" not in raw
    assert "Bearer " not in raw
