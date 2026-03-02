from pathlib import Path


JSON_DIRNAME = "JSON"


def run_json_dir(run_dir: str | Path) -> Path:
    path = Path(run_dir) / JSON_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_artifact_path(run_dir: str | Path, filename: str) -> Path:
    return run_json_dir(run_dir) / filename


def execution_results_path(run_dir: str | Path) -> Path:
    return json_artifact_path(run_dir, "Execution_Results.json")


def execution_debug_path(run_dir: str | Path) -> Path:
    return json_artifact_path(run_dir, "Execution_Debug.json")


def execution_learning_path(run_dir: str | Path) -> Path:
    return json_artifact_path(run_dir, "Execution_Learning.json")


def execution_checkpoint_path(run_dir: str | Path) -> Path:
    return json_artifact_path(run_dir, "Execution_Checkpoints.json")
