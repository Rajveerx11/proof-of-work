from importlib.metadata import version

import proofofwork


def test_runtime_version_matches_installed_package_metadata():
    assert proofofwork.__version__ == version("proof-of-work-agent")
