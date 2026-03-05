from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT_DIR / "configs"
RESULT_DIR = ROOT_DIR / "Result"
PROFILES_DIR = CONFIGS_DIR / "site_profiles"
FEEDBACK_DIR = PROFILES_DIR / "feedback"
INSTRUCTIONS_DIR = CONFIGS_DIR / "prompt_templates"
AUTH_DIR = CONFIGS_DIR / "auth"
BENCHMARKS_DIR = CONFIGS_DIR / "benchmarks"
