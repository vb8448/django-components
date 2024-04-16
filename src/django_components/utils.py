import glob
from pathlib import Path
from typing import List, NamedTuple, Optional

from django.template.engine import Engine

from django_components.template_loader import Loader


class SearchResult(NamedTuple):
    searched_dirs: List[Path]
    matched_files: List[Path]


def search(search_glob: Optional[str] = None, engine: Optional[Engine] = None) -> SearchResult:
    """
    Search for directories that may contain components.

    If `search_glob` is given, the directories are searched for said glob pattern,
    and glob search results are returned as a flattened list.
    """
    current_engine = engine
    if current_engine is None:
        current_engine = Engine.get_default()

    loader = Loader(current_engine)
    dirs = loader.get_dirs()

    if search_glob is None:
        return SearchResult(searched_dirs=dirs, matched_files=[])

    component_filenames: List[Path] = []
    for directory in dirs:
        for path in glob.iglob(str(Path(directory) / search_glob), recursive=True):
            component_filenames.append(Path(path))

    return SearchResult(searched_dirs=dirs, matched_files=component_filenames)


# Global counter to ensure that all IDs generated by `gen_id` WILL be unique
_id = 0


def gen_id(length: int = 5) -> str:
    """Generate a unique ID that can be associated with a Node"""
    # Global counter to avoid conflicts
    global _id
    _id += 1

    # Pad the ID with `0`s up to 4 digits, e.g. `0007`
    return f"{_id:04}"
