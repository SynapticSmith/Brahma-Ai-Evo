# Cookbook: Monorepo Symbol & Call Hierarchy Tracing

## Objective
Trace every caller and dependent module of a function or class before executing breaking refactors or API changes.

## Prerequisites
- Target repository with Python source files.

## Execution Recipe
1. **Parse AST and Extract Function Callers**:
   Run an AST visitor script over target directory:
   ```python
   import ast, pathlib
   target_fn = "execute_tool"
   callers = []
   for py_path in pathlib.Path("core").rglob("*.py"):
       tree = ast.parse(py_path.read_text(encoding="utf-8"))
       for node in ast.walk(tree):
           if isinstance(node, ast.Call) and getattr(node.func, "id", None) == target_fn:
               callers.append(f"{py_path.name}:{node.lineno}")
   ```
2. **Trace Upstream Caller Tiers**:
   Map from leaf callers back to public API entry points.
3. **Map Import Dependencies**:
   Inspect module-level imports to detect dependency directions and cycles.
4. **Verification Gate**:
   Every caller is accounted for in refactoring plans with zero unexpected `NameError` or `AttributeError` exceptions.
5. **Remediation**:
   For dynamic dispatches (e.g. `getattr(self, fn_name)`), supplement AST inspection with targeted `grep` checks.
