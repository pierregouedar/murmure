"""Runs every test module: `python -m tests` (from the repository root, no network, no model)."""
import runpy, sys

failed = []
for name in ("test_rules", "test_tools", "test_backend", "test_web"):
    try:
        runpy.run_module(f"tests.{name}", run_name="__main__")
    except SystemExit as e:
        if e.code:
            failed.append(name)
print("FAILED: " + ", ".join(failed) if failed else "all test modules passed")
sys.exit(1 if failed else 0)
