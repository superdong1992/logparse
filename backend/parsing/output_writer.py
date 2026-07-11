"""旧机制证据 writer 路径 façade。"""

import sys

from backend.extensions.products.current import artifacts as _implementation

sys.modules[__name__] = _implementation
