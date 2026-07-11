from backend.domain.lifecycle.common import LifecycleSplitConfig as DomainConfig
from backend.domain.lifecycle.splitter_v3 import LifecycleSplitterV3 as DomainSplitter
from backend.parsing.lifecycle_common import LifecycleSplitConfig as LegacyConfig
from backend.parsing.lifecycle_splitter_v3 import LifecycleSplitterV3 as LegacySplitter


def test_lifecycle_legacy_paths_are_facades() -> None:
    assert LegacyConfig is DomainConfig
    assert LegacySplitter is DomainSplitter
