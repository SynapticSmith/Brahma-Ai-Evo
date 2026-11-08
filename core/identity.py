import json
from pathlib import Path
from typing import Dict, Any, List

from core.user_paths import atomic_write_json, get_user_data_dir

def get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BEHAVIOR_DIRECTIVES: Dict[str, str] = {
    "professional": (
        "Maintain an executive, structured, objective, and polished tone. "
        "Provide concise, high-precision answers with structured bullet points. "
        "Avoid informal slang or unnecessary conversational filler."
    ),
    "casual": (
        "Be friendly, conversational, empathetic, and natural. "
        "Use warm, approachable language while staying helpful, accurate, and supportive."
    ),
    "technical": (
        "Adopt an in-depth engineering and architectural perspective. "
        "Provide exact technical terminology, code snippets, root-cause diagnostics, and structural explanations."
    ),
    "minimal": (
        "Be extremely concise, direct, and terse. "
        "Answer questions with maximum brevity; omit pleasantries, conversational preambles, and filler."
    ),
    "proactive": (
        "Anticipate the user's next logical steps, suggest performance or workflow optimizations, "
        "surface potential risks unprompted, and recommend actionable follow-up tasks."
    ),
}

class IdentityService:
    def __init__(self):
        self.config_file = get_user_data_dir() / "config" / "identity.json"
        self._legacy_config_file = get_base_dir() / "config" / "identity.json"
        self.data: Dict[str, Any] = {
            "owner": {
                "name": "",
                "preferred_name": "",
                "role": "",
                "location": "",
                "interests": [],
                "about": ""
            },
            "assistant": {
                "name": "Brahma",
                "application_name": "Brahma Evo",
                "title": "Personal AI Assistant"
            },
            "behavior": {
                "mode": "professional",
                "proactive": True,
                "custom_instructions": ""
            },
            "system": {
                "shared_computer": False
            }
        }
        self.load()

    def load(self):
        source = self.config_file if self.config_file.exists() else (
            self._legacy_config_file if self._legacy_config_file.exists() else None
        )
        if source is not None:
            try:
                with open(source, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                    
                    # Deep merge to preserve defaults for missing keys
                    for section, values in loaded_data.items():
                        if section in self.data and isinstance(values, dict):
                            self.data[section].update(values)
                        else:
                            self.data[section] = values
            except Exception as e:
                print(f"Error loading identity config: {e}")

    def save(self):
        try:
            atomic_write_json(self.config_file, self.data)
        except Exception as e:
            print(f"Error saving identity config: {e}")

    # Assistant methods
    def get_assistant_name(self) -> str:
        val = self.data["assistant"].get("name", "Brahma")
        return val if val is not None else "Brahma"
        
    def set_assistant_name(self, name: str):
        self.data["assistant"]["name"] = name
        self.save()

    def get_application_name(self) -> str:
        val = self.data["assistant"].get("application_name", "Brahma Evo")
        return val if val is not None else "Brahma Evo"
        
    def set_application_name(self, name: str):
        self.data["assistant"]["application_name"] = name
        self.save()

    def get_assistant_title(self) -> str:
        val = self.data["assistant"].get("title", "Personal AI Assistant")
        return val if val is not None else "Personal AI Assistant"
        
    def set_assistant_title(self, title: str):
        self.data["assistant"]["title"] = title
        self.save()

    # Owner Profile methods
    def get_owner_name(self) -> str:
        val = self.data["owner"].get("name", "")
        return val if val is not None else ""
        
    def set_owner_name(self, name: str):
        self.data["owner"]["name"] = name
        if not self.data["owner"].get("preferred_name"):
            self.data["owner"]["preferred_name"] = name
        self.save()

    def get_owner_role(self) -> str:
        val = self.data["owner"].get("role", "")
        return val if val is not None else ""
        
    def set_owner_role(self, role: str):
        self.data["owner"]["role"] = role
        self.save()

    def get_owner_location(self) -> str:
        val = self.data["owner"].get("location", "")
        return val if val is not None else ""
        
    def set_owner_location(self, location: str):
        self.data["owner"]["location"] = location
        self.save()

    def get_owner_interests(self) -> List[str]:
        val = self.data["owner"].get("interests", [])
        return val if val is not None else []
        
    def set_owner_interests(self, interests: List[str]):
        self.data["owner"]["interests"] = interests
        self.save()

    def get_owner_about(self) -> str:
        val = self.data["owner"].get("about", "")
        return val if val is not None else ""
        
    def set_owner_about(self, about: str):
        self.data["owner"]["about"] = about
        self.save()

    # Behavior methods
    def get_behavior_mode(self) -> str:
        val = self.data["behavior"].get("mode", "professional")
        return val if val is not None else "professional"
        
    def set_behavior_mode(self, mode: str):
        self.data["behavior"]["mode"] = mode
        self.save()

    def get_behavior_directive(self, mode: str | None = None) -> str:
        target_mode = (mode or self.get_behavior_mode()).lower()
        return BEHAVIOR_DIRECTIVES.get(target_mode, BEHAVIOR_DIRECTIVES["professional"])

    @staticmethod
    def get_behavior_descriptions() -> Dict[str, str]:
        return dict(BEHAVIOR_DIRECTIVES)

    def get_custom_instructions(self) -> str:
        val = self.data["behavior"].get("custom_instructions", "")
        return val if val is not None else ""
        
    def set_custom_instructions(self, instructions: str):
        self.data["behavior"]["custom_instructions"] = instructions
        self.save()
        
    def is_proactive(self) -> bool:
        val = self.data["behavior"].get("proactive", True)
        return val if val is not None else True
        
    def set_proactive(self, proactive: bool):
        self.data["behavior"]["proactive"] = proactive
        self.save()

    # System methods
    def is_shared_computer(self) -> bool:
        val = self.data["system"].get("shared_computer", False)
        return val if val is not None else False
        
    def set_shared_computer(self, is_shared: bool):
        self.data["system"]["shared_computer"] = is_shared
        self.save()

    def is_setup_complete(self) -> bool:
        # Consider setup complete if owner name is provided
        val = self.data["owner"].get("name", "")
        return bool(val.strip()) if val else False

# Global singleton instance
identity = IdentityService()
