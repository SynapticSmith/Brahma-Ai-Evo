"""
Standalone SkillHost Worker Process (WS-2, SEC-04, SEC-05).

Part of Security Architecture Phase 2.
Launched out-of-process under Windows AppContainer or Job Object boundaries.
Speaks JSON protocol:
Input:  {"code_path": str, "kwargs": dict, "capabilities": list}
Output: {"success": bool, "result": any, "error": str}
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
from typing import Any, Dict


def execute_skill_payload(code_path: Path, kwargs: Dict[str, Any]) -> Any:
    """Imports and executes the entry `execute(**kwargs)` function from the code path."""
    spec = importlib.util.spec_from_file_location("sandboxed_skill", str(code_path))
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load module from {code_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "execute"):
        raise AttributeError(f"Module in {code_path} has no 'execute' function")

    func = getattr(module, "execute")
    if inspect.iscoroutinefunction(func):
        return asyncio.run(func(**kwargs))
    else:
        return func(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillHost Worker Process")
    parser.add_argument("--code", required=True, help="Path to skill Python code file")
    parser.add_argument("--params", required=True, help="Path to input params JSON file")
    parser.add_argument("--output", required=True, help="Path to output result JSON file")
    args = parser.parse_args()

    code_path = Path(args.code)
    params_path = Path(args.params)
    output_path = Path(args.output)

    try:
        kwargs = json.loads(params_path.read_text(encoding="utf-8"))
        result = execute_skill_payload(code_path, kwargs)
        output_path.write_text(
            json.dumps({"success": True, "result": result}),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        output_path.write_text(
            json.dumps({
                "success": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
