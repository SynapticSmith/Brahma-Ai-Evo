"""Standalone out-of-process runner for synthetic skills (SEC-04, SEC-05).

Invoked exclusively by `core.skill_host` inside an isolated child process.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated Skill Runner")
    parser.add_argument("--code", required=True, help="Path to skill Python file")
    parser.add_argument("--params", required=True, help="Path to input params JSON file")
    parser.add_argument("--output", required=True, help="Path to output result JSON file")
    args = parser.parse_args()

    code_path = Path(args.code)
    params_path = Path(args.params)
    output_path = Path(args.output)

    try:
        with open(params_path, "r", encoding="utf-8") as f:
            kwargs = json.load(f)

        spec = importlib.util.spec_from_file_location("isolated_skill", str(code_path))
        if not spec or not spec.loader:
            raise ImportError(f"Cannot load module from {code_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "execute"):
            raise AttributeError(f"Module in {code_path} has no 'execute' function")

        func = getattr(module, "execute")
        if inspect.iscoroutinefunction(func):
            res = asyncio.run(func(**kwargs))
        else:
            res = func(**kwargs)

        output_path.write_text(json.dumps({"success": True, "result": res}), encoding="utf-8")
        return 0
    except Exception as exc:
        output_path.write_text(
            json.dumps({"success": False, "error": str(exc), "traceback": traceback.format_exc()}),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
