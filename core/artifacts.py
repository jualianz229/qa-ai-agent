from pathlib import Path


JSON_DIRNAME = "JSON"


def run_json_dir(run_dir: str | Path, create: bool = True) -> Path:
    path = Path(run_dir) / JSON_DIRNAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def json_artifact_path(run_dir: str | Path, filename: str, create: bool = True) -> Path:
    return run_json_dir(run_dir, create=create) / filename


def execution_results_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Execution_Results.json", create=create)


def execution_debug_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Execution_Debug.json", create=create)


def execution_learning_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Execution_Learning.json", create=create)


def execution_checkpoint_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Execution_Checkpoints.json", create=create)


def human_feedback_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Human_Feedback.json", create=create)
