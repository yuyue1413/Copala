import asyncio
from pathlib import Path

from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.learned import LearnSkillTool


def test_learn_skill_saves_script_and_workspace_skill(tmp_path: Path) -> None:
    tool = LearnSkillTool(tmp_path)

    result = asyncio.run(tool.execute(
        action="save",
        skill_name="weather-query",
        description="Use for reusable weather query scripts.",
        category="weather",
        script_name="get_weather.py",
        script_content="print('weather')\n",
        usage="Run `python learned/scripts/weather/get_weather.py`.",
        notes="Historical learned script.",
    ))

    assert "Saved learned skill: learned-weather-query" in result
    assert (tmp_path / "learned" / "scripts" / "weather" / "get_weather.py").exists()
    skill_file = tmp_path / "skills" / "learned-weather-query" / "SKILL.md"
    assert skill_file.exists()
    assert "learned/scripts/weather/get_weather.py" in skill_file.read_text(encoding="utf-8")

    skills = SkillsLoader(tmp_path).list_skills(filter_unavailable=False)
    assert any(s["name"] == "learned-weather-query" for s in skills)


def test_learn_skill_lists_and_refuses_overwrite(tmp_path: Path) -> None:
    tool = LearnSkillTool(tmp_path)
    asyncio.run(tool.execute(
        action="save",
        skill_name="bilibili",
        description="Use for Bilibili lookup.",
        category="bilibili",
        script_name="lookup.py",
        script_content="print('ok')\n",
    ))

    duplicate = asyncio.run(tool.execute(
        action="save",
        skill_name="bilibili",
        description="Use for Bilibili lookup.",
        category="bilibili",
        script_name="lookup.py",
        script_content="print('new')\n",
    ))
    listed = asyncio.run(tool.execute(action="list"))

    assert "already exists" in duplicate
    assert "learned-bilibili" in listed


def test_learn_skill_records_secret_warning(tmp_path: Path) -> None:
    tool = LearnSkillTool(tmp_path)

    result = asyncio.run(tool.execute(
        action="save",
        skill_name="email-reader",
        description="Use for archived email scripts.",
        category="email",
        script_name="read_mail.py",
        script_content="EMAIL_PASS = 'secret'\n",
    ))

    assert "Warnings:" in result
    content = (tmp_path / "skills" / "learned-email-reader" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "credential-like" in content
