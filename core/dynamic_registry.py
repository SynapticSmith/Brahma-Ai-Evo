"""
Dynamic Tool Registry & Hot-Plugging Engine
Part of Project Ultron for Brahma AI.

Manages persistent synthetic skills, dynamic tool declarations for Gemini Live,
hot-reloading, execution dispatch, and lifecycle management.
"""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.routing import normalize
from core.user_paths import get_user_data_dir

logger = logging.getLogger("DynamicToolRegistry")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = PROJECT_ROOT / "features"
APPDATA_SKILLS_DIR = get_user_data_dir() / "skills"


class DynamicSkill:
    """Represents a loaded, runnable feature or synthetic skill in Brahma AI."""

    def __init__(self, skill_path: Path, manifest: Dict[str, Any], module: Any = None):
        self.skill_path = skill_path
        self.skill_dir = skill_path if skill_path.is_dir() else skill_path.parent
        self.manifest = manifest
        self.module = module
        self.name: str = manifest.get("name", skill_path.stem)
        self.description: str = manifest.get("description", "")
        self.parameters: Dict[str, Any] = manifest.get("parameters", {"type": "OBJECT", "properties": {}})
        self.triggers: List[str] = manifest.get("triggers", [])
        self.aliases: List[str] = manifest.get("aliases", [])
        self.active: bool = manifest.get("active", True)
        self.version: str = manifest.get("version", "1.0.0")
        self.created_at: float = manifest.get("created_at", time.time())
        self.invocations: int = manifest.get("invocations", 0)
        self.last_error: Optional[str] = manifest.get("last_error", None)
        self.is_synthetic: bool = bool(manifest.get("is_synthetic", False) or manifest.get("source") == "synthetic")
        self.grant: Optional[Dict[str, Any]] = manifest.get("grant")

    def _load_module(self) -> Any:
        if self.module is None:
            code_path = self.skill_path / "skill.py" if self.skill_path.is_dir() else self.skill_path
            module_name = f"brahma_skill_{self.name}_{abs(hash(str(code_path.resolve())))}"
            spec = importlib.util.spec_from_file_location(module_name, str(code_path))
            if not spec or not spec.loader:
                raise ImportError(f"Unable to load skill module: {code_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            self.module = module
        return self.module

    def to_tool_declaration(self) -> Dict[str, Any]:
        """Returns Gemini function declaration dict."""
        return {
            "name": self.name,
            "description": f"{self.description} (Autonomous dedicated tool: prefer calling this tool directly over generic web search).",
            "parameters": self.parameters,
        }

    def execute_sync(self, **kwargs) -> Any:
        """Executes the skill synchronously (safe for worker threads)."""
        if self.is_synthetic and self.module is None:
            from core.skill_host import execute_in_skill_host
            code_path = self.skill_path / "code.py" if (self.skill_path / "code.py").exists() else (
                self.skill_path / "skill.py" if self.skill_path.is_dir() else self.skill_path
            )
            self.invocations += 1
            return execute_in_skill_host(code_path, kwargs, grant=self.grant)

        module = self._load_module()
        if not hasattr(module, "execute"):
            raise AttributeError(f"Feature '{self.name}' has no 'execute' function.")
        func = getattr(module, "execute")
        self.invocations += 1
        if inspect.iscoroutinefunction(func):
            return asyncio.run(func(**kwargs))
        return func(**kwargs)

    async def execute_async(self, **kwargs) -> Any:
        """Executes the skill asynchronously without thread-pool registration conflicts."""
        if self.is_synthetic and self.module is None:
            return await asyncio.to_thread(self.execute_sync, **kwargs)

        module = self._load_module()
        if not hasattr(module, "execute"):
            raise AttributeError(f"Feature '{self.name}' has no 'execute' function.")

        func = getattr(module, "execute")
        self.invocations += 1

        if inspect.iscoroutinefunction(func):
            return await func(**kwargs)
        else:
            return await asyncio.to_thread(func, **kwargs)


class _ExternalSkillModule:
    """Adapts a plain synchronous callable to the ``module.execute`` skill contract."""

    def __init__(self, executor: Callable[..., Any]):
        self._executor = executor

    def execute(self, **kwargs) -> Any:
        return self._executor(**kwargs)


class DynamicToolRegistry:
    """Central registry for all hot-loaded features and synthetic Brahma skills."""

    _skills: Dict[str, DynamicSkill] = {}
    # Externally-supplied tools (e.g. custom MCP servers). Kept in a separate
    # overlay so that initialize()'s `_skills.clear()` cannot wipe them, and so
    # they are never persisted to the filesystem skills tree.
    _external_skills: Dict[str, DynamicSkill] = {}
    _initialized: bool = False

    @classmethod
    def register_external_tool(
        cls,
        name: str,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        executor: Optional[Callable[..., Any]] = None,
        *,
        version: str = "1.0.0",
        active: bool = True,
        source: str = "external",
    ) -> "DynamicSkill":
        """Registers a tool backed by an in-process synchronous callable.

        Used by the custom MCP bridge to expose third-party MCP tools through
        the exact same tool-declaration and execution paths as native features.

        `executor` must be a plain synchronous callable taking **kwargs. It is
        invoked from whichever thread the registry dispatches on, so it must
        not assume it is running inside the caller's event loop.
        """
        if not name:
            raise ValueError("External tool requires a non-empty name.")
        if executor is None or not callable(executor):
            raise ValueError(f"External tool '{name}' requires a callable executor.")

        if parameters is None:
            parameters = {"type": "OBJECT", "properties": {}}

        manifest = {
            "name": name,
            "description": description or f"External tool {name}",
            "parameters": parameters,
            "active": bool(active),
            "version": version,
            "source": source,
            "created_at": time.time(),
        }
        # A non-existent sentinel path: DynamicSkill only needs .parent/.stem,
        # and the module is supplied directly so nothing is read from disk.
        sentinel = get_user_data_dir() / "skills" / "external" / f"{name}.tool"
        skill = DynamicSkill(sentinel, manifest, module=_ExternalSkillModule(executor))
        cls._external_skills[name] = skill
        logger.info(f"[Registry] Registered external tool '{name}' (source={source}).")
        return skill

    @classmethod
    def unregister_external_tool(cls, name: str) -> bool:
        """Removes a previously registered external tool. Returns True if present."""
        if name in cls._external_skills:
            del cls._external_skills[name]
            logger.info(f"[Registry] Unregistered external tool '{name}'.")
            return True
        return False

    @classmethod
    def is_external_tool(cls, name: str) -> bool:
        return name in cls._external_skills

    @classmethod
    def _all_skills(cls) -> Dict[str, DynamicSkill]:
        """Merged view of filesystem skills and externally-registered tools.

        Native skills win on name collision so an external registration can
        never shadow a real feature.
        """
        if not cls._initialized:
            cls.initialize()
        if not cls._external_skills:
            return cls._skills
        merged = dict(cls._external_skills)
        merged.update(cls._skills)
        return merged

    @classmethod
    def get_skills_directory(cls) -> Path:
        FEATURES_DIR.mkdir(parents=True, exist_ok=True)
        return FEATURES_DIR

    @classmethod
    def initialize(cls) -> int:
        """Loads all valid features and skills from the codebase features/ directory and AppData vault."""
        cls._skills.clear()
        FEATURES_DIR.mkdir(parents=True, exist_ok=True)
        count = 0

        # 1. Load native features from codebase features/ directory
        for item in sorted(FEATURES_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
            if item.name.startswith((".", "_")):
                continue

            # Case A: Single Python module file (e.g. features/internet_speed_test.py)
            if item.is_file() and item.suffix == ".py":
                try:
                    source = item.read_text(encoding="utf-8")
                    tree = ast.parse(source, filename=str(item))
                    meta: Dict[str, Any] = {}
                    for node in tree.body:
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
                        if any(isinstance(target, ast.Name) and target.id == "FEATURE_METADATA" for target in targets):
                            value = node.value
                            parsed = ast.literal_eval(value)
                            if isinstance(parsed, dict):
                                meta = parsed
                            break
                    if not meta:
                        logger.warning(
                            f"[Registry] Skipping unverified file '{item.name}': no FEATURE_METADATA declaration found."
                        )
                        continue

                    if not meta.get("active", True):
                        logger.debug(f"[Registry] Skipping inactive feature '{item.stem}'.")
                        continue

                    manifest_path = item.with_suffix("") / "manifest.json"
                    if manifest_path.exists():
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            package_manifest = json.load(f)
                        meta = {**meta, **package_manifest}

                    feat_name = meta.get("name", item.stem)
                    meta.setdefault("created_at", item.stat().st_mtime)
                    skill = DynamicSkill(item, meta)
                    cls._skills[feat_name] = skill
                    for alias in meta.get("aliases", []):
                        if alias not in cls._skills:
                            cls._skills[alias] = skill
                    count += 1
                except Exception as e:
                    logger.warning(f"[Registry] Failed to load codebase feature '{item.name}': {e}")

            # Case B: Feature directory with manifest.json & skill.py
            elif item.is_dir():
                manifest_file = item / "manifest.json"
                code_file = item / "skill.py"
                if manifest_file.exists() and code_file.exists():
                    try:
                        with open(manifest_file, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        if not manifest.get("active", True):
                            continue
                        skill = DynamicSkill(item, manifest)
                        cls._skills[skill.name] = skill
                        count += 1
                    except Exception as e:
                        logger.warning(f"[Registry] Failed to load feature package '{item.name}': {e}")

        # 2. Check SkillStore for verified, installed synthetic skills
        try:
            from core.skill_store import SkillStore, STATE_INSTALLED
            store = SkillStore()
            for artifact in store.list_artifacts(state=STATE_INSTALLED):
                if artifact.name not in cls._skills:
                    ok, _, reason = store.verify_installed_artifact(artifact.name)
                    if ok:
                        skill_path = store.store_dir / artifact.artifact_hash
                        manifest_with_grant = {
                            **artifact.manifest,
                            "is_synthetic": True,
                            "grant": artifact.grant,
                        }
                        skill = DynamicSkill(skill_path, manifest_with_grant)
                        cls._skills[artifact.name] = skill
                        count += 1
                    else:
                        logger.warning(f"[Registry] Skipping unverified installed skill '{artifact.name}': {reason}")
        except Exception as e:
            logger.debug(f"[Registry] SkillStore integration notice: {e}")

        # 3. Check legacy AppData skills vault for backward compatibility
        if APPDATA_SKILLS_DIR.exists():
            for item in APPDATA_SKILLS_DIR.iterdir():
                if item.is_dir() and item.name not in cls._skills and item.name not in ("store", "logs"):
                    manifest_file = item / "manifest.json"
                    code_file = item / "skill.py"
                    if manifest_file.exists() and code_file.exists():
                        try:
                            with open(manifest_file, "r", encoding="utf-8") as f:
                                manifest = json.load(f)
                            if not manifest.get("active", True):
                                continue
                            skill = DynamicSkill(item, manifest)
                            cls._skills[skill.name] = skill
                            count += 1
                        except Exception:
                            pass

        cls._initialized = True
        logger.info(f"[Registry] Initialized with {count} features and skills.")
        return count

    @classmethod
    def register_installed_artifact(cls, artifact_hash: str, store: Optional[Any] = None) -> bool:
        """Registers a freshly approved and installed skill artifact into the active registry."""
        if not cls._initialized:
            cls.initialize()
        try:
            from core.skill_store import SkillStore, STATE_INSTALLED
            store = store or SkillStore()
            artifact = store.get_artifact(artifact_hash)
            if not artifact:
                logger.warning(f"[Registry] Cannot register artifact {artifact_hash}: not found in store")
                return False
            if artifact.state != STATE_INSTALLED:
                logger.warning(
                    f"[Registry] Cannot register artifact {artifact_hash}: state is '{artifact.state}', expected '{STATE_INSTALLED}'"
                )
                return False
            ok, _, reason = store.verify_installed_artifact(artifact.name)
            if not ok:
                logger.warning(f"[Registry] Skipping unverified installed skill '{artifact.name}': {reason}")
                return False
            skill_path = store.store_dir / artifact.artifact_hash
            manifest_with_grant = {
                **artifact.manifest,
                "is_synthetic": True,
                "grant": artifact.grant,
            }
            skill = DynamicSkill(skill_path, manifest_with_grant)
            cls._skills[artifact.name] = skill
            for alias in artifact.manifest.get("aliases", []):
                cls._skills[alias] = skill
            logger.info(f"[Registry] Live registered installed skill '{artifact.name}' ({artifact.artifact_hash[:12]})")
            return True
        except Exception as e:
            logger.warning(f"[Registry] Failed to register installed artifact {artifact_hash}: {e}")
            return False

    @classmethod
    def get_tool_declarations(cls) -> List[Dict[str, Any]]:
        """Returns Gemini-compatible tool declarations for all active skills."""
        if not cls._initialized:
            cls.initialize()
        return [skill.to_tool_declaration() for skill in cls._all_skills().values() if skill.active]

    @classmethod
    def has_tool(cls, name: str) -> bool:
        if not cls._initialized:
            cls.initialize()
        skill = cls._all_skills().get(name)
        return bool(skill and skill.active)

    @classmethod
    def get_skill(cls, name: str) -> Optional[DynamicSkill]:
        if not cls._initialized:
            cls.initialize()
        return cls._all_skills().get(name)

    @classmethod
    def get_latest_skill(cls) -> Optional[DynamicSkill]:
        """Returns the most recently created active synthetic skill."""
        if not cls._initialized:
            cls.initialize()
        active = [s for s in cls._all_skills().values() if s.active]
        if not active:
            return None
        active.sort(key=lambda s: getattr(s, "created_at", 0), reverse=True)
        return active[0]

    @classmethod
    def find_matching_skill(cls, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Match an active skill only when the user names it exactly.

        A fuzzy score is not authority: a >=70 token score can claim a turn the
        user meant for another tool, and a pre-LLM claim cannot fall through on
        a near miss. The model sees every active skill as a ``dynamic_skill``
        declaration, so an approximate request still reaches it — it just is
        not routed before the model (ROUTING-AUDIT §2, Phase 4 A6).

        Exact means the normalised utterance equals a configured trigger,
        alias, or the skill name, optionally after one imperative verb
        (``run``/``use``/``launch``/...). Parameter extraction is unchanged.
        """
        if not text or not isinstance(text, str):
            return None
        if not cls._initialized:
            cls.initialize()

        q_lower = text.lower().strip()
        q_words = re.findall(r"[a-z0-9]+", q_lower)
        if not q_words:
            return None

        stopwords = {
            "a", "an", "the", "and", "or", "to", "for", "when", "that", "will", "you", "i", "me", "my",
            "on", "in", "at", "by", "from", "of", "with", "about", "is", "are", "was", "were", "be",
            "this", "that", "it", "can", "please", "do", "what", "how", "give", "brahma", "tell", "show",
            "run", "use", "start", "execute", "check", "get", "fetch", "try", "call", "launch",
            "skill", "skills", "feature", "features", "tool", "tools", "test", "tests", "testing",
            "made", "make", "using", "u", "ur", "your", "created", "create", "built", "build",
            "forged", "forge", "just", "recently", "newly", "new", "latest", "now", "already",
            "one", "did", "have", "current", "currently", "present", "status", "view", "info", "data", "details", "as", "like"
        }

        q_meaningful = [w for w in q_words if w not in stopwords and len(w) > 2]

        # 1. Unnamed skill directive: exact grammar, no domain word.
        generic_directives = {
            "use the skill", "run the skill", "test the skill", "execute the skill",
            "the skill", "use skill", "run skill", "test skill", "use latest skill",
            "run latest skill", "test latest skill", "use new skill", "run new skill",
            "use that skill", "run that skill", "test that skill", "execute that skill",
            "that skill", "this skill", "use this skill", "run this skill", "the feature",
            "use the feature", "run the feature", "launch the skill", "try the skill",
            "use the tool", "run the tool", "test the tool", "execute the tool"
        }
        cleaned_q = " ".join(q_words)
        is_generic_unnamed = (
            cleaned_q in generic_directives or
            (len(q_meaningful) == 0 and any(w in q_words for w in ("skill", "skills", "feature", "features", "tool", "tools"))) or
            (len(q_meaningful) == 0 and any(cleaned_q.startswith(g) for g in ("use", "run", "test", "execute", "start", "launch", "try")))
        )
        if is_generic_unnamed:
            latest = cls.get_latest_skill()
            if latest:
                return latest.name, {}

        # 2. Exact trigger, alias or skill name. Similarity is not authority.
        spoken = normalize(text)
        stripped = spoken
        for verb in ("run", "use", "execute", "start", "launch", "test", "try", "do"):
            if spoken.startswith(verb + " "):
                stripped = spoken[len(verb) + 1 :].strip()
                break

        def _spoken_forms(value: str) -> set[str]:
            forms = {normalize(value), normalize(str(value).replace("_", " "))}
            forms.discard("")
            return forms

        best_skill = None
        for skill in cls._skills.values():
            if not skill.active:
                continue
            phrases: set[str] = set(_spoken_forms(skill.name))
            for trigger in getattr(skill, "triggers", []):
                phrases.update(_spoken_forms(trigger))
            for alias in getattr(skill, "aliases", []):
                phrases.update(_spoken_forms(alias))
            if spoken in phrases or stripped in phrases:
                best_skill = skill
                break

        if not best_skill:
            return None

        # 3. Dynamic parameter extraction from skill manifest
        props = best_skill.parameters.get("properties", {})
        args: Dict[str, Any] = {}
        for p_name, p_spec in props.items():
            p_desc = str(p_spec.get("description", ""))
            p_type = str(p_spec.get("type", "STRING")).upper()

            # Location / Area extraction based on semantic description
            if any(w in p_desc.lower() for w in ("city", "district", "region", "area", "location")):
                for prep in ("around ", "in ", "near ", "over ", "for ", "at "):
                    if prep in q_lower:
                        cand = text[q_lower.find(prep) + len(prep):].strip()
                        cand = re.split(r"[,.?!;]|(?:and\s+)|(?:with\s+)", cand)[0].strip()
                        if cand:
                            args[p_name] = cand
                        break

            # Quoted options / tickers from description e.g. 'AAPL', 'BMW.DE', 'robot', 'floss'
            for opt in re.findall(r"'([a-zA-Z0-9_.-]+)'", p_desc):
                if opt.lower() in q_words:
                    args[p_name] = opt
                    break

            # Label to symbol mapping from description e.g. 'AAPL' for Apple
            examples = re.findall(r"'([A-Za-z0-9_.-]+)'\s+for\s+([A-Za-z0-9]+)", p_desc)
            for code, label in examples:
                if label.lower() in q_words:
                    args[p_name] = code
                    break

            # Number / duration extraction for numeric parameters
            if p_type in ("INTEGER", "NUMBER"):
                m_num = re.search(r"\b(\d+)\s*(?:seconds?|sec|mins?|points?|hours?|kline|bars?|times?)\b", q_lower)
                if m_num:
                    args[p_name] = int(m_num.group(1))

        # 4. Strict validation: If the skill requires parameters, they MUST be extracted!
        required = best_skill.parameters.get("required", [])
        for req in required:
            if req not in args or not args[req]:
                logger.debug(f"[Registry] Match rejected for '{best_skill.name}': missing required argument '{req}'")
                return None

        return best_skill.name, args


    @classmethod
    def list_skills(cls) -> List[Dict[str, Any]]:
        """Returns metadata for all installed skills for UI presentation."""
        if not cls._initialized:
            cls.initialize()
        results = []
        seen = set()
        for s in cls._all_skills().values():
            if id(s) in seen:
                continue
            seen.add(id(s))
            results.append({
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "active": s.active,
                "created_at": s.created_at,
                "invocations": s.invocations,
                "path": str(s.skill_dir),
                "last_error": s.last_error,
            })
        return results

    @classmethod
    def toggle_skill(cls, name: str, active: Optional[bool] = None) -> bool:
        """Enables or disables a synthetic skill."""
        skill = cls.get_skill(name)
        if not skill:
            return False

        new_status = not skill.active if active is None else bool(active)
        skill.active = new_status

        # External tools are not filesystem-backed: keep the flag in memory only.
        if cls.is_external_tool(name):
            return True

        manifest_path = skill.skill_dir / "manifest.json"
        try:
            skill.manifest["active"] = new_status
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(skill.manifest, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"[Registry] Failed to save toggle status for '{name}': {e}")
            return False

    @classmethod
    def delete_skill(cls, name: str) -> bool:
        """Permanently removes a synthetic skill."""
        skill = cls.get_skill(name)
        if not skill:
            return False

        # External tools are in-memory only: drop the overlay entry, never touch disk.
        if cls.is_external_tool(name):
            return cls.unregister_external_tool(name)

        try:
            if skill.skill_dir.exists():
                shutil.rmtree(skill.skill_dir)
            if name in cls._skills:
                del cls._skills[name]
            return True
        except Exception as e:
            logger.error(f"[Registry] Failed to delete skill '{name}': {e}")
            return False

    @classmethod
    def execute_sync(cls, name: str, args: Dict[str, Any]) -> Any:
        """Dispatches execution synchronously (safe for background worker threads)."""
        skill = cls.get_skill(name)
        if not skill or not skill.active:
            return f"Error: Feature '{name}' is unavailable or deactivated."

        try:
            return skill.execute_sync(**args)
        except Exception as e:
            skill.last_error = str(e)
            logger.error(f"[Registry] Execution error in feature '{name}': {e}", exc_info=True)
            try:
                import traceback
                from actions.auto_heal_engine import AutoHealEngine
                AutoHealEngine.record_last_error(traceback.format_exc())
            except Exception:
                pass
            raise

    @classmethod
    def execute_guarded(
        cls,
        name: str,
        args: Dict[str, Any],
        *,
        call_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Dispatches a dynamic skill or external MCP tool through ``agent.runtime`` (I-01)."""
        skill = cls.get_skill(name)
        if not skill or not skill.active:
            return f"Error: Feature '{name}' is unavailable or deactivated."

        if cls.is_external_tool(name) or str(name).startswith("mcp_"):
            tool_class = "mcp"
        else:
            raw_cls = str(skill.manifest.get("tool_class") or "exec").strip().lower()
            tool_class = raw_cls if raw_cls in ("read", "write", "exec", "network", "device", "mcp", "native") else "exec"

        def _dispatch(n: str, a: Dict[str, Any]) -> str:
            run_res = cls.execute_sync(n, a or {})
            return (
                str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res)
                if isinstance(run_res, dict)
                else str(run_res)
            ).strip()

        from agent import runtime as agent_runtime

        merged_extra = dict(extra or {})
        merged_extra["tool_class"] = tool_class
        return agent_runtime.execute(
            name,
            args or {},
            native_dispatch=_dispatch,
            call_id=call_id or f"call_{name}",
            extra=merged_extra,
        )

    @classmethod
    async def execute_async(cls, name: str, args: Dict[str, Any]) -> Any:
        """Dispatches an async execution to a loaded synthetic skill."""
        skill = cls.get_skill(name)
        if not skill or not skill.active:
            return f"Error: Synthetic skill '{name}' is unavailable or deactivated."

        try:
            result = await skill.execute_async(**args)
            return result
        except Exception as e:
            skill.last_error = str(e)
            logger.error(f"[Registry] Execution error in skill '{name}': {e}", exc_info=True)
            # Route to auto heal engine if available
            try:
                import traceback
                from actions.auto_heal_engine import AutoHealEngine
                AutoHealEngine.record_last_error(traceback.format_exc())
            except Exception:
                pass
            raise
