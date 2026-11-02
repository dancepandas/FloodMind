"""Build controlled source -> sdist -> wheel, then probe an isolated install."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

PROBE = '''
from pathlib import Path
import importlib.metadata
import importlib.resources
import sys
import floodmind
from floodmind import Agent, ModelClient
from floodmind.agent.runtime.contracts.tools import ToolSpec

assert Path(floodmind.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), floodmind.__file__
assert importlib.metadata.version("floodmind") == floodmind.__version__
resources = importlib.resources.files("floodmind")
for name in ("config/settings_template.json", "skills/csv/SKILL.md"):
    resource = resources.joinpath(name)
    assert resource.is_file(), name
    assert resource.read_text(encoding="utf-8").strip(), name
'''


def verify_installed_package(python: Path, cwd: Path) -> None:
    subprocess.run([str(python), "-I", "-c", PROBE], cwd=cwd, check=True)
    entry = python.parent / ("floodmind.exe" if sys.platform == "win32" else "floodmind")
    subprocess.run([str(entry), "--help"], cwd=cwd, check=True)


def copy_tracked_source(root: Path, destination: Path, *, source_manifest: Path | None = None) -> None:
    output = (source_manifest.read_bytes() if source_manifest is not None
              else subprocess.check_output(["git", "ls-files", "-z"], cwd=root))
    names = [name for name in output.decode("utf-8").split("\0") if name]
    if not names:
        raise ValueError("No tracked source files; run from a populated Git checkout")
    destination.mkdir(parents=True)
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe tracked path: {name}")
        if any(part in {".git", ".venv", "venv", "__pycache__", "build", "dist"}
               or part.endswith(".egg-info") for part in relative.parts):
            continue
        source = root / relative
        if source.is_symlink():
            raise ValueError(f"Symlink not allowed in distribution source: {name}")
        if not source.is_file():
            raise ValueError(f"Missing source file: {name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def check_distribution(root: Path, *, source_manifest: Path | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="floodmind-dist-") as temporary:
        work = Path(temporary)
        source = work / "source"
        if source_manifest is None:
            copy_tracked_source(root, source)
        else:
            copy_tracked_source(root, source, source_manifest=source_manifest)
        sdists = work / "sdist"
        subprocess.run([sys.executable, "-m", "build", "--sdist", "--outdir", str(sdists)], cwd=source, check=True)
        archives = list(sdists.glob("*.tar.gz"))
        if len(archives) != 1:
            raise ValueError(f"Expected one sdist, got {len(archives)}")
        unpacked = work / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archives[0]) as archive:
            members = archive.getmembers()
            for member in members:
                target = (unpacked / member.name).resolve()
                if not target.is_relative_to(unpacked.resolve()) or not (member.isfile() or member.isdir()):
                    raise ValueError(f"Unsafe sdist member: {member.name}")
            archive.extractall(unpacked, members=members)
        projects = list(unpacked.iterdir())
        if len(projects) != 1 or not projects[0].is_dir():
            raise ValueError("Expected one sdist project directory")
        wheels = work / "wheel"
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels)], cwd=projects[0], check=True)
        artifacts = list(wheels.glob("*.whl"))
        if len(artifacts) != 1:
            raise ValueError(f"Expected one wheel, got {len(artifacts)}")
        environment = work / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(artifacts[0])], cwd=work, check=True)
        outside = work / "probe"
        outside.mkdir()
        verify_installed_package(python, outside)
        print("Distribution check passed: controlled source -> sdist -> wheel -> isolated install")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path,
                        help="NUL-delimited file inventory from git ls-tree of the archived commit")
    args = parser.parse_args()
    check_distribution(Path(__file__).resolve().parents[1], source_manifest=args.source_manifest)
