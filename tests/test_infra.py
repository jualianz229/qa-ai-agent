import pathlib
import re

replacements = {
    r'\bfrom test_case_generator\b': 'from modules.test_case_generator',
    r'\bimport test_case_generator\b': 'import modules.test_case_generator',
    r'\bfrom end_to_end_automation\b': 'from modules.end_to_end_automation',
    r'\bimport end_to_end_automation\b': 'import modules.end_to_end_automation',
    r'\bfrom visual_regression_testing\b': 'from modules.visual_regression_testing',
    r'\bimport visual_regression_testing\b': 'import modules.visual_regression_testing',
    r'\bfrom core\.common\b': 'from core',
    r'\bimport core\.common\b': 'import core',
    r'\bfrom dashboard\b': 'from website.dashboard',
    r'\bimport dashboard\b': 'import website.dashboard'
}

root = pathlib.Path('.')
for p in root.rglob('*.py'):
    if '__pycache__' in str(p):
        continue
    try:
        content = p.read_text(encoding='utf-8')
        new_content = content
        for pattern, repl in replacements.items():
            new_content = re.sub(pattern, repl, new_content)
        
        if new_content != content:
            p.write_text(new_content, encoding='utf-8')
            print(f"Updated: {p}")
    except Exception as e:
        print(f"Skipped {p}: {e}")
