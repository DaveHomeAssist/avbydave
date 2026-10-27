import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def stage(source, destination):
    workflow = (ROOT / ".github/workflows/pages.yml").read_text()
    commands = [line.strip() for line in workflow.splitlines()
                if line.strip().startswith("rsync ")]
    if len(commands) != 1:
        raise AssertionError("Expected one canonical rsync staging command")
    args = shlex.split(commands[0])
    if args[-2:] != ["./", "_site/"]:
        raise AssertionError("Unexpected staging source or destination")
    destination.mkdir()
    subprocess.run(args[:-2] + [str(source) + "/", str(destination) + "/"],
                   check=True, capture_output=True, text=True)


class PagesStaging(unittest.TestCase):
    def test_root_readme_is_excluded_and_nested_readme_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            files = {
                "README.md": "Repository instructions",
                "equipment-lab/data/models/reference/README.md": "Required package file",
                ".git/internal": "Not public",
                ".github/workflows/pages.yml": "Not public",
                "_site/previous": "Not recursive",
            }
            for name, text in files.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            destination = root / "published"
            stage(source, destination)
            self.assertFalse((destination / "README.md").exists())
            self.assertEqual(
                (destination / "equipment-lab/data/models/reference/README.md").read_text(),
                "Required package file")
            for name in (".git", ".github", "_site"):
                self.assertFalse((destination / name).exists(), name)

    def test_every_declared_equipment_package_file_survives_staging(self):
        manifest = json.loads((ROOT / "equipment-lab/data/manifest.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "published"
            stage(ROOT, destination)
            for name, expected in manifest["files"].items():
                with self.subTest(file=name):
                    data = (destination / "equipment-lab/data" / name).read_bytes()
                    self.assertEqual(len(data), expected["bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), expected["sha256"])


if __name__ == "__main__":
    unittest.main()
