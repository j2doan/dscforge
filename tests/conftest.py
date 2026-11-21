import tempfile

import pytest


# the tool makes temp folders (the clean copy, the throwaway environment); keep the ones made during tests inside
# pytest's own temp area, which pytest cleans up, instead of leaving dscforge_* folders in the system temp folder
@pytest.fixture(scope="session", autouse=True)
def _temp_inside_pytest_area(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("temp")))
    yield
    patch.undo()
