from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _package_name(requirement: str) -> str:
    return (
        requirement.split("[", 1)[0]
        .split("==", 1)[0]
        .split(">=", 1)[0]
        .strip()
        .lower()
    )


def test_vercel_dependencies_match_runtime_requirements():
    """Vercel installs pyproject.toml, so it must include every runtime package."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        _package_name(requirement)
        for requirement in pyproject["project"]["dependencies"]
    }
    required = {
        _package_name(line)
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert declared == required
