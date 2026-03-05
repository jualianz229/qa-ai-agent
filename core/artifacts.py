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


def execution_network_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Execution_Network.json", create=create)


def human_feedback_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Human_Feedback.json", create=create)


def visual_signature_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Visual_Signature.json", create=create)


def visual_baseline_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Visual_Baseline.json", create=create)


def visual_diff_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Visual_Diff.json", create=create)


def visual_regression_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Visual_Regression.json", create=create)


def visual_regression_approval_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Visual_Regression_Approval.json", create=create)


def scenario_contract_validation_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Scenario_Contract_Validation.json", create=create)


def flaky_analysis_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Flaky_Analysis.json", create=create)


def confidence_analysis_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Confidence_Analysis.json", create=create)


def token_usage_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Token_Usage.json", create=create)


def contradiction_analysis_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Contradiction_Analysis.json", create=create)


def anti_hallucination_audit_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Anti_Hallucination_Audit.json", create=create)


def execution_replay_verification_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Execution_Replay_Verification.json", create=create)


def drift_analysis_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Drift_Analysis.json", create=create)


def policy_pack_report_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Policy_Pack_Report.json", create=create)


def recovery_actions_path(run_dir: str | Path, create: bool = True) -> Path:
    return json_artifact_path(run_dir, "Recovery_Actions.json", create=create)
