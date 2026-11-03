"""
The Skill Forge: Autonomous Capability Synthesis Engine
Part of Project Ultron for Brahma AI.

Transforms natural language goals into fully architected, tested,
and hot-pluggable Python skills for Brahma AI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.user_paths import get_user_data_dir
from core.skill_crucible import SkillCrucible

logger = logging.getLogger("SkillForge")

CONFIG_DIR = get_user_data_dir() / "config"
API_CONFIG_PATH = CONFIG_DIR / "api_keys.json"
STAGED_SKILLS_DIRNAME = "staged_skills"


def staged_skills_dir() -> Path:
    """Directory where forged skills wait for human review.

    Forge output is model-generated code, so it never lands in the live
    ``features/`` package and is never imported by the process that wrote it.
    Promotion is a separate, explicit step (see ARCHITECTURE-SPEC §7.3).
    """
    path = get_user_data_dir() / STAGED_SKILLS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_gemini_api_key() -> str:
    """Retrieves the Gemini API key from api_keys.json or environment variables."""
    if API_CONFIG_PATH.exists():
        try:
            from core.secret_store import load_api_keys
            data = load_api_keys(API_CONFIG_PATH)
            key = data.get("gemini_api_key", "").strip()
            if key:
                return key
        except Exception:
            pass
    return (os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")).strip()


class SkillForge:
    """Autonomous synthesizer of new Brahma AI skills."""

    @classmethod
    def forge_skill(
        cls,
        goal: str,
        skill_name: Optional[str] = None,
        context_hints: str = "",
        max_repair_attempts: int = 2,
    ) -> Dict[str, Any]:
        """
        Synthesizes a brand new skill from natural language, verifies it in the
        Crucible sandbox, and stages it for review. Nothing is activated until a
        human promotes the staged copy.
        """
        logger.info(f"[Forge] Initiating skill synthesis for goal: '{goal}'")

        # Normalize skill name if provided
        name_hint = re.sub(r"[^a-zA-Z0-9_]", "_", (skill_name or "").lower()).strip("_")

        # 1. Synthesize initial specification
        synthesis = cls._call_llm_synthesizer(goal, name_hint, context_hints)
        if not synthesis.get("success"):
            return {
                "success": False,
                "message": f"Skill synthesis generation failed: {synthesis.get('error')}",
            }

        manifest = synthesis["manifest"]
        code = synthesis["code"]
        test_cases = synthesis.get("test_cases", [{"input": {}}])
        actual_name = manifest.get("name", name_hint or "custom_skill")

        # 2. Iterative Verification & Self-Repair Loop
        for attempt in range(max_repair_attempts + 1):
            logger.info(f"[Forge] Crucible verification attempt {attempt + 1}/{max_repair_attempts + 1} for '{actual_name}'")

            # Stage A: AST Validation
            ast_ok, ast_err = SkillCrucible.validate_ast(code)
            if not ast_ok:
                if attempt < max_repair_attempts:
                    logger.warning(f"[Forge] AST check failed ({ast_err}). Requesting repair from LLM...")
                    repair = cls._repair_code(code, ast_err, goal)
                    if repair.get("success"):
                        code = repair["code"]
                        continue
                return {"success": False, "message": f"Crucible AST rejected skill: {ast_err}"}

            # Stage B: Dependency Auto-Resolver
            deps = SkillCrucible.extract_dependencies(code)
            deps_ok, deps_msg = SkillCrucible.resolve_dependencies(deps)
            if not deps_ok:
                return {"success": False, "message": f"Dependency resolution failed: {deps_msg}"}

            # Stage C: Sandboxed Execution Tests
            test_ok, test_msg, test_telemetry = SkillCrucible.run_sandbox_test(code, test_cases)
            if not test_ok:
                if attempt < max_repair_attempts:
                    logger.warning(f"[Forge] Sandbox tests failed ({test_msg}). Requesting repair from LLM...")
                    repair = cls._repair_code(code, test_msg, goal)
                    if repair.get("success"):
                        code = repair["code"]
                        continue
                return {"success": False, "message": f"Crucible Sandbox tests failed: {test_msg}", "telemetry": test_telemetry}

            # All stages passed!
            break

        # 3. Stage the verified skill for review. Forge never writes into the
        # live features/ package, never edits features/__init__.py and never
        # hot-loads the result: generated code only becomes callable after an
        # explicit promotion (ARCHITECTURE-SPEC §7.3).
        staged_dir = staged_skills_dir() / actual_name

        # Prepare triggers & aliases
        triggers = list(manifest.get("triggers", []))
        if goal and goal.strip() not in triggers:
            triggers.append(goal.strip())

        clean_goal = re.sub(
            r"^(?:please\s+|can\s+you\s+|use\s+(?:the\s+)?(?:skill|feature)\s+to\s+|run\s+(?:the\s+)?(?:skill|feature)\s+to\s+|test\s+(?:the\s+)?(?:skill|feature)\s+to\s+)",
            "",
            goal.lower().strip()
        )
        if clean_goal and clean_goal not in triggers:
            triggers.append(clean_goal)

        name_words_trigger = actual_name.replace("_", " ")
        if name_words_trigger not in triggers:
            triggers.append(name_words_trigger)

        aliases = list(manifest.get("aliases", []))
        clean_alias = actual_name.replace("_", "")
        if clean_alias not in aliases:
            aliases.append(clean_alias)

        manifest["name"] = actual_name
        manifest["triggers"] = list(dict.fromkeys(triggers))
        manifest["aliases"] = list(dict.fromkeys(aliases))
        manifest["created_at"] = time.time()
        manifest["version"] = "1.0.0"
        manifest["author"] = "Project Ultron Autonomous Self-Evolution Engine"
        manifest["active"] = True

        # Build clean native feature code with embedded FEATURE_METADATA
        feature_code = code
        if "FEATURE_METADATA" not in feature_code:
            meta_str = repr(manifest)
            header = (
                f'"""\n'
                f'Feature: {actual_name}\n'
                f'Description: {manifest.get("description", "")}\n'
                f'Autonomous Evolutionary Capability synthesized by Brahma AI.\n'
                f'"""\n\n'
                f'FEATURE_METADATA = {meta_str}\n\n'
            )
            feature_code = header + feature_code

        # Primary staged module: staged_skills/{actual_name}/skill.py
        skill_file = staged_dir / "skill.py"
        try:
            staged_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(feature_code, encoding="utf-8")
            (staged_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=4), encoding="utf-8"
            )
            (staged_dir / "test_cases.json").write_text(
                json.dumps(test_cases, indent=4), encoding="utf-8"
            )

            skill_sha256 = hashlib.sha256(feature_code.encode("utf-8")).hexdigest()
            (staged_dir / "staged.json").write_text(
                json.dumps(
                    {
                        "name": actual_name,
                        "sha256": skill_sha256,
                        "created_at": manifest["created_at"],
                        "verified": True,
                        "activated": False,
                    },
                    indent=4,
                ),
                encoding="utf-8",
            )

            artifact_hash = skill_sha256
            try:
                from core.skill_store import SkillStore
                store = SkillStore()
                artifact = store.put_draft(
                    name=actual_name,
                    code=feature_code,
                    manifest=manifest,
                    test_cases=test_cases,
                    telemetry=locals().get("test_telemetry"),
                )
                artifact_hash = artifact.artifact_hash
            except Exception as store_err:
                logger.warning(f"[Forge] Failed to register draft in SkillStore: {store_err}")

            return {
                "success": True,
                "staged": True,
                "activated": False,
                "name": actual_name,
                "description": manifest.get("description", ""),
                "skill_path": str(skill_file),
                "staged_path": str(staged_dir),
                "sha256": skill_sha256,
                "artifact_hash": artifact_hash,
                "message": (
                    f"Drafted '{actual_name}' and verified it in the Crucible sandbox. "
                    f"It is staged at {staged_dir} and is NOT active until it is reviewed "
                    "and promoted."
                ),
                "manifest": manifest,
            }
        except Exception as e:
            return {"success": False, "message": f"Failed staging synthesized feature: {e}"}

    @classmethod
    def _parse_json_response(cls, text: str) -> Dict[str, Any]:
        """Robustly extracts and parses a JSON object from model output, ignoring any extra trailing text."""
        clean = (text or "").strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*\n?", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\n?```\s*$", "", clean).strip()

        # 1. Try direct json.loads
        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        # 2. Use JSONDecoder.raw_decode starting from first '{' (skips trailing notes/extra data)
        start_idx = clean.find("{")
        if start_idx != -1:
            try:
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(clean[start_idx:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        # 3. Match outermost balanced { and }
        if start_idx != -1:
            for end_idx in range(len(clean) - 1, start_idx, -1):
                if clean[end_idx] == "}":
                    candidate = clean[start_idx:end_idx + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        continue

        raise ValueError("Could not extract a valid JSON object from LLM output.")

    @classmethod
    def _call_llm_synthesizer(cls, goal: str, name_hint: str, context_hints: str) -> Dict[str, Any]:
        """Prompts Gemini to generate the complete skill package JSON."""
        system_instructions = """You are the Brahma AI Autonomous Skill Architect ("Project Ultron").
Your mission is to invent, architect, and write a complete, standalone, production-ready Python skill plugin.

Skill Architecture Guidelines:
1. Entry point MUST be `def execute(**kwargs)` or `async def execute(**kwargs)`.
2. Must be clean, robust Python with error handling (try/except) and type annotations.
3. Default Parameter Handling:
   - In `execute(**kwargs)`, ALWAYS assign safe fallback defaults to all expected parameters (e.g. `query = kwargs.get('query') or kwargs.get('search') or 'headphones'`).
   - If called with empty kwargs `{}` (such as during sandbox verification), the skill MUST execute cleanly without throwing KeyError or TypeError.
4. Verified Network & TLS Handling:
   - When external live data or web downloads are needed, use `requests` with `timeout=8` and keep certificate verification enabled (the library default).
   - If using `urllib`, build the connection with `ssl.create_default_context()`.
   - If a TLS handshake fails, catch the error and use a local fallback rather than weakening transport security.
   - NEVER require or assume environment API keys (e.g. `GIPHY_API_KEY`, `OPENAI_API_KEY`). Skills must be 100% self-contained and run out of the box using public open APIs (such as Tenor public key `LIVDSRZULELA` or open REST) or local Python logic.
   - If any network call fails or times out, ALWAYS catch generic `Exception` and supply a working fallback so `execute()` NEVER returns an `{'error': ...}` dictionary.
   - Do NOT use heavy scrapers (avoid selenium/playwright).
5. Visual Deliverables, Images, GIFs, & UI Cards:
   - If the user asks for images, drawings, graphics, headphones, cars, animals, cartoons, plots, scorecards, charts, or GIFs:
     a) ALWAYS produce an actual deliverable image file (.png or .gif) saved to:
        `output_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'BrahmaAI', 'deliverables')`
        `os.makedirs(output_dir, exist_ok=True)`
        `image_path = os.path.join(output_dir, f'{actual_name}_output.png')`
     b) For diagrams, illustrations, charts, or tech visuals: Generate the visual NATIVELY using `PIL` (`from PIL import Image, ImageDraw, ImageFont`) or `matplotlib` (`import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt`).
        Style it with a sleek cyberpunk dark theme: dark slate background `#0B0F19` or `#030712`, glowing cyan `#00F0FF`, ice cyan `#38BDF8`, emerald `#10B981`, and white `#FFFFFF`.
     c) For web images/GIFs: Attempt downloading with certificate verification ON, but if the download fails or if network is unavailable, IMMEDIATELY fall back to drawing a crisp high-tech visual deliverable using PIL/matplotlib so execution always succeeds and displays on screen. Never disable TLS verification to rescue a download.
     d) Return format for visuals:
        `return {'image_path': image_path, 'title': '...', 'summary': '...'}`
        This triggers Brahma Evo's HUD Result Wing to immediately display the card!
6. Output Format:
   Output MUST be clean JSON with exact structure:
{
    "manifest": {
        "name": "snake_case_feature_name",
        "aliases": ["alias_1", "alias_2"],
        "description": "Concise, actionable description of when and how Gemini Live should call this feature",
        "triggers": [
            "direct trigger phrase 1",
            "natural variation 2",
            "query command 3"
        ],
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "param_name": {
                    "type": "STRING|INTEGER|NUMBER|BOOLEAN",
                    "description": "What this parameter is"
                }
            },
            "required": []
        }
    },
    "code": "Full python source code including imports and def execute(**kwargs)",
    "test_cases": [
        {
            "input": {}
        }
    ]
}
Return ONLY this JSON object with no markdown fences around it.
"""

        prompt = f"""Synthesize a new skill for the following user request:
Goal: {goal}
Preferred Name: {name_hint or 'auto_generate'}
Additional Context: {context_hints}
"""

        try:
            from core import llm_gateway

            for model_name in ("gemini-2.5-flash-lite", "gemini-3.6-flash", "gemini-2.5-flash", "gemini-flash-latest"):
                try:
                    resp = llm_gateway.generate_content(
                        prompt,
                        model=model_name,
                        config={
                            "temperature": 0.2,
                            "system_instruction": system_instructions,
                            "response_mime_type": "application/json"
                        },
                        max_tokens=8192,
                        purpose="skill synthesis",
                    )
                    raw = resp.text
                except llm_gateway.GatewayError as unavailable:
                    logger.warning(f"[Forge] LLM gateway unavailable: {unavailable}")
                    break
                except Exception as e:
                    logger.warning(f"[Forge] Model {model_name} failed: {e}")
                    continue
                try:
                    data = cls._parse_json_response(raw)
                except Exception:
                    data = {}
                if "manifest" in data and "code" in data:
                    data["success"] = True
                    return data
                if resp.provider != "gemini":
                    # A non-Gemini link ignores `model`; retrying the loop
                    # would just re-ask the same provider for the same answer.
                    break
        except Exception as exc:
            logger.error(f"[Forge] Synthesis gateway error: {exc}")

        # Fallback to Unified llm_client if available
        try:
            from llm_client import client as unified_client
            resp = unified_client.chat(
                prompt=prompt,
                system=system_instructions,
                max_tokens=8192,
                temperature=0.2,
            )
            data = cls._parse_json_response(resp)
            if not isinstance(data.get("manifest"), dict) or not isinstance(data.get("code"), str):
                raise ValueError("Unified LLM response must contain a manifest object and code string.")
            data["success"] = True
            return data
        except Exception as fallback_exc:
            return {"success": False, "error": f"LLM synthesis failed: {fallback_exc}"}

    @classmethod
    def _repair_code(cls, broken_code: str, error_msg: str, goal: str) -> Dict[str, Any]:
        """Asks LLM to fix syntax or sandbox runtime errors."""
        prompt = f"""You are repairing a Python skill generated for Brahma AI ("Project Ultron").
The skill failed verification in the Crucible sandbox.
User Goal: {goal}
Verification Error: {error_msg}

Broken Code:
```python
{broken_code}
```

Critical Repair Instructions:
1. Ensure `def execute(**kwargs)` handles empty or missing kwargs with safe defaults.
2. If using `matplotlib`, ensure `import matplotlib; matplotlib.use('Agg')` is placed before `pyplot`.
3. If making HTTP requests, use `requests` with `timeout=8` and keep certificate verification enabled, or `urllib` with `ssl.create_default_context()`. Never disable certificate verification, and never build or pass an unverified SSL context. NEVER assume custom library exceptions or unset API keys (like GIPHY_API_KEY).
4. If downloading an image or media fails or has SSL errors, NEVER just return an error dictionary. Generate the image natively using PIL (Pillow) or matplotlib and save to `BrahmaAI/deliverables/<name>.png`.
5. Return a clean deliverable dictionary with `'image_path'`, `'title'`, `'summary'` if visual, or clean structured output.
6. The test runner checks that the returned value does NOT contain an `'error'` key. Do not return `{{'error': '...'}}`. If an error occurs, provide a graceful fallback result.
7. Return ONLY a JSON object:
{{
    "code": "Fully corrected, runnable Python code"
}}
"""
        try:
            from core import llm_gateway

            for model_name in ("gemini-2.5-flash-lite", "gemini-3.6-flash", "gemini-2.5-flash", "gemini-flash-latest"):
                try:
                    resp = llm_gateway.generate_content(
                        prompt,
                        model=model_name,
                        config={"temperature": 0.1, "response_mime_type": "application/json"},
                        purpose="skill repair",
                    )
                    raw = resp.text
                except llm_gateway.GatewayError as unavailable:
                    logger.warning(f"[Forge] LLM gateway unavailable: {unavailable}")
                    break
                except Exception as e:
                    logger.warning(f"[Forge] Repair model {model_name} failed: {e}")
                    continue
                try:
                    data = cls._parse_json_response(raw)
                except Exception:
                    data = {}
                if "code" in data:
                    data["success"] = True
                    return data
                if resp.provider != "gemini":
                    break
        except Exception as exc:
            logger.error(f"[Forge] Repair gateway error: {exc}")

        return {"success": False, "error": "Repair attempt failed."}
