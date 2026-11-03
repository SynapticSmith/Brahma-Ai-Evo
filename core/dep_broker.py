"""
Dependency Broker & Supply Chain Isolation Engine (WS-3).

Part of Security Architecture Phase 1.
Implements:
1. Hash-pinned lockfile parser (`deps.lock`) enforcing `--require-hashes` and `--only-binary=:all:`.
2. Dependency inspection and planning (`plan_dependencies` -> `DependencyPlan`).
3. Dependency request generation for unvetted/missing packages (zero unapproved runtime pip installs).
4. Binary wheel hash verification with SHA-256 integrity checks.
5. Automated CycloneDX SBOM generation for the resolved dependency closure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.util
import logging
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("DepBroker")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCKFILE_PATH = PROJECT_ROOT / "deps.lock"

# Standard Python library modules
STANDARD_LIB_MODULES = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect",
    "calendar", "cmath", "collections", "colorsys", "concurrent", "configparser",
    "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal",
    "difflib", "dis", "doctest", "email", "enum", "errno", "faulthandler",
    "filecmp", "fileinput", "fnmatch", "fractions", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "imaplib", "imghdr", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "keyword", "linecache", "locale", "logging", "lzma",
    "math", "mimetypes", "mmap", "multiprocessing", "netrc", "nntplib", "numbers",
    "operator", "os", "pathlib", "pickle", "pkgutil", "platform", "plistlib",
    "poplib", "posixpath", "pprint", "profile", "pstats", "pty", "pwd", "py_compile",
    "pyclbr", "pydoc", "queue", "quopri", "random", "re", "reprlib", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "selectors", "shelve",
    "shlex", "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr", "socket",
    "socketserver", "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
    "struct", "subprocess", "sys", "sysconfig", "tabnanny", "tarfile", "telnetlib",
    "tempfile", "termios", "textwrap", "threading", "time", "timeit", "tkinter",
    "token", "tokenize", "trace", "traceback", "tracemalloc", "tty", "turtle",
    "types", "typing", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "wsgiref", "xdrlib", "xml",
    "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib"
}

# Module import to PyPI package name normalization
IMPORT_TO_PIP: Dict[str, str] = {
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "fitz": "pymupdf",
    "speedtest": "speedtest-cli",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "websocket": "websocket-client",
}


@dataclass
class LockedPackage:
    name: str
    version: str
    hashes: List[str] = field(default_factory=list)


@dataclass
class DependencyPlan:
    """Represents a planned dependency set with hash validation and security boundaries."""
    requested: List[str]
    resolved: Dict[str, LockedPackage]
    missing: List[str]                 # Packages not found in lockfile
    satisfied_by_env: List[str]        # Imports already satisfied by runtime
    is_satisfied: bool                 # True if all imports are available
    only_binary: bool = True           # Strictly enforce wheels only
    require_hashes: bool = True        # Strictly enforce hash verification
    command: Optional[str] = None      # Exact reproducible install command
    sbom: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "resolved": {k: {"name": v.name, "version": v.version, "hashes": v.hashes} for k, v in self.resolved.items()},
            "missing": self.missing,
            "satisfied_by_env": self.satisfied_by_env,
            "is_satisfied": self.is_satisfied,
            "only_binary": self.only_binary,
            "require_hashes": self.require_hashes,
            "command": self.command,
            "sbom": self.sbom,
        }


def parse_lockfile(path: Optional[Path] = None) -> Dict[str, LockedPackage]:
    """Parses a hash-pinned deps.lock file into structured LockedPackage records."""
    lock_path = Path(path) if path is not None else DEFAULT_LOCKFILE_PATH
    if not lock_path.exists():
        logger.warning(f"[DepBroker] Lockfile not found at {lock_path}")
        return {}

    packages: Dict[str, LockedPackage] = {}
    current_pkg: Optional[LockedPackage] = None

    lines = lock_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Look for package declaration: name==version
        pkg_match = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*==\s*([a-zA-Z0-9_\-\.]+)", stripped)
        if pkg_match:
            pkg_name = pkg_match.group(1).lower().replace("_", "-")
            version = pkg_match.group(2)
            current_pkg = LockedPackage(name=pkg_name, version=version, hashes=[])
            packages[pkg_name] = current_pkg

        # Look for hash declaration: --hash=sha256:...
        hash_match = re.search(r"--hash=(sha256:[a-fA-F0-9]{64})", stripped)
        if hash_match and current_pkg:
            current_pkg.hashes.append(hash_match.group(1))

    return packages


def verify_file_hash(file_path: Path, expected_hash_with_prefix: str) -> bool:
    """Verifies that a file's SHA-256 matches the expected lockfile hash."""
    path = Path(file_path)
    if not path.exists():
        return False

    prefix, _, expected = expected_hash_with_prefix.partition(":")
    if prefix != "sha256" or len(expected) != 64:
        return False

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)

    actual = hasher.hexdigest().lower()
    return actual == expected.lower()


def generate_cyclonedx_sbom(packages: Dict[str, LockedPackage]) -> Dict[str, Any]:
    """Generates a CycloneDX SBOM component array for the resolved dependency closure."""
    components = []
    for pkg in packages.values():
        hashes = []
        for h in pkg.hashes:
            alg, _, val = h.partition(":")
            hashes.append({"alg": alg.upper(), "content": val})
        components.append({
            "type": "library",
            "name": pkg.name,
            "version": pkg.version,
            "purl": f"pkg:pypi/{pkg.name}@{pkg.version}",
            "hashes": hashes,
            "scope": "required",
        })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": components,
    }


class DependencyBroker:
    """Brokers dependency planning, verification, and isolation for synthetic skills."""

    def __init__(self, lockfile_path: Optional[Path] = None):
        self.lockfile_path = Path(lockfile_path) if lockfile_path is not None else DEFAULT_LOCKFILE_PATH
        self._lock_cache: Optional[Dict[str, LockedPackage]] = None

    @property
    def locked_packages(self) -> Dict[str, LockedPackage]:
        if self._lock_cache is None:
            self._lock_cache = parse_lockfile(self.lockfile_path)
        return self._lock_cache

    def plan_dependencies(self, dependencies: Sequence[str]) -> DependencyPlan:
        """Inspects imports, determines environment satisfaction, and plans locked dependencies."""
        req_clean = [str(d).strip() for d in dependencies if str(d).strip()]
        if not req_clean:
            return DependencyPlan(
                requested=[],
                resolved={},
                missing=[],
                satisfied_by_env=[],
                is_satisfied=True,
                command=None,
                sbom={"components": []},
            )

        satisfied_by_env: List[str] = []
        needed_packages: Set[str] = set()

        for dep in req_clean:
            # Check if standard library
            if dep in STANDARD_LIB_MODULES or dep in ("core", "actions", "features"):
                satisfied_by_env.append(dep)
                continue

            # Check if active Python runtime already satisfies import
            try:
                spec = importlib.util.find_spec(dep)
                if spec is not None:
                    satisfied_by_env.append(dep)
                    continue
            except (ImportError, ValueError, AttributeError):
                pass

            # Needs external package
            pip_name = IMPORT_TO_PIP.get(dep, dep).lower().replace("_", "-")
            needed_packages.add(pip_name)

        # Resolve against lockfile
        resolved: Dict[str, LockedPackage] = {}
        missing: List[str] = []

        for pkg_name in sorted(needed_packages):
            locked = self.locked_packages.get(pkg_name)
            if locked:
                resolved[pkg_name] = locked
            else:
                missing.append(pkg_name)

        all_satisfied = len(needed_packages) == 0
        cmd = None
        if resolved:
            cmd = (
                f"{sys.executable} -m pip install --require-hashes --only-binary=:all: "
                f"--no-deps -r {self.lockfile_path.name}"
            )

        # Collect full external package closure for SBOM
        closure_packages: Dict[str, LockedPackage] = {}
        for dep in req_clean:
            if dep in STANDARD_LIB_MODULES or dep in ("core", "actions", "features"):
                continue
            pip_name = IMPORT_TO_PIP.get(dep, dep).lower().replace("_", "-")
            locked = self.locked_packages.get(pip_name)
            if locked:
                closure_packages[pip_name] = locked
            elif pip_name in resolved:
                closure_packages[pip_name] = resolved[pip_name]

        sbom = generate_cyclonedx_sbom(closure_packages or resolved)

        return DependencyPlan(
            requested=req_clean,
            resolved=resolved,
            missing=missing,
            satisfied_by_env=satisfied_by_env,
            is_satisfied=all_satisfied,
            command=cmd,
            sbom=sbom,
        )

    def install_plan(
        self,
        plan: DependencyPlan,
        wheelhouse_dir: Optional[Path] = None,
        target_venv: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """Validates and enforces binary-only, hash-verified installation boundaries."""
        # Acceptance test gate: unvetted packages outside lockfile cannot be installed
        if plan.missing:
            return (
                False,
                f"Refused install: {len(plan.missing)} package(s) not in lockfile: {', '.join(plan.missing)}. "
                "Unapproved packages require explicit user addition to deps.lock and wheelhouse.",
            )

        if plan.is_satisfied:
            return True, "All requested dependencies are already satisfied by active environment."

        if not plan.resolved:
            return True, "No packages to install."

        # Hard rules check
        if not plan.only_binary:
            return False, "Security Violation: Source distributions rejected. Only binary wheels (--only-binary=:all:) permitted."

        if not plan.require_hashes:
            return False, "Security Violation: Hash verification must be enabled (--require-hashes)."

        # If a wheelhouse directory is provided, verify every wheel's SHA-256 hash before install
        if wheelhouse_dir is not None:
            wh_path = Path(wheelhouse_dir)
            if not wh_path.exists():
                return False, f"Wheelhouse directory {wh_path} does not exist."

            for pkg_name, locked in plan.resolved.items():
                found_wheel = False
                for wheel_file in wh_path.glob(f"{pkg_name.replace('-', '_')}*.whl"):
                    # Check against locked hashes
                    for expected_hash in locked.hashes:
                        if verify_file_hash(wheel_file, expected_hash):
                            found_wheel = True
                            break
                    if found_wheel:
                        break

                if not found_wheel:
                    # Check if tar.gz exists (source distribution violation)
                    tar_files = list(wh_path.glob(f"{pkg_name}*.tar.gz"))
                    if tar_files:
                        return False, f"Package '{pkg_name}' has no binary wheel (only source tar.gz found). Rejected under --only-binary."
                    return False, f"Package '{pkg_name}' hash mismatch or missing wheel in wheelhouse."

        return True, f"Dependency plan verified. Ready to install {len(plan.resolved)} package(s) under locked hashes."
