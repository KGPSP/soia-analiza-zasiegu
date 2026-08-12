import os
from pathlib import Path

import pytest

from soia.validation import validate_country_release


@pytest.mark.skipif(not os.environ.get("SOIA_RELEASE_DATA_ROOT"), reason="test wydania poza CI")
def test_full_poland_release_acceptance() -> None:
    result = validate_country_release(Path(os.environ["SOIA_RELEASE_DATA_ROOT"]))
    assert result["validation_passed"], result["checks"]
