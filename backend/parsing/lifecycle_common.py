"""旧生命周期公共模块 façade。"""

import sys

from backend.domain.lifecycle import common as _implementation

sys.modules[__name__] = _implementation
