"""旧 compact 发现类路径 façade。"""

import sys

from backend.extensions.products.compact import scanner as _implementation

sys.modules[__name__] = _implementation
