import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = (
    ROOT / "installer" / "BrahmaEcho.spec",
    ROOT / "installer" / "BrahmaEcho_Setup.spec",
)


def test_installer_specs_parse_and_have_no_developer_drive_paths():
    for path in SPECS:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
        assert "D:\\\\" not in source
        assert "TiTech Prabha Solution" not in source


def test_setup_spec_uses_workspace_relative_paths():
    source = (ROOT / "installer" / "BrahmaEcho_Setup.spec").read_text(
        encoding="utf-8"
    )
    assert "cwd = os.path.abspath(os.getcwd())" in source
    assert "os.path.join(cwd, 'installer', 'dist', 'BrahmaEcho')" in source
    assert "os.path.join(cwd, 'installer', 'install_wizard.py')" in source
