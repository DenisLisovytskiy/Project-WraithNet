"""Load/save a ScenarioConfig to/from a JSON file. JSON, not YAML -- see
docs/09_..._Design_Document.md §4 for why (no YAML dependency exists
anywhere else in this project)."""

from pathlib import Path

from wraithnet.scenario_schema import ScenarioConfig


def load_scenario(path: str | Path) -> ScenarioConfig:
    return ScenarioConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_scenario(config: ScenarioConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
