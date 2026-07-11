"""旧 V3 生命周期模块 façade。"""

import sys

from backend.domain.lifecycle import splitter_v3 as _implementation

sys.modules[__name__] = _implementation
