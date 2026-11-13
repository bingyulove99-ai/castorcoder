from pathlib import Path

assert not Path("created.txt").exists(), "plan mode must block write_file"
print("policy ok")
