from pathlib import Path

text = Path("calc.py").read_text(encoding="utf-8")
assert "def sum_values(a, b):" in text, text
assert "def add(" not in text, text
print("rename ok")
