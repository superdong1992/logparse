"""旧 metadata projection 导入路径 façade。"""

import sys

from backend.extensions.products.current import metadata as _implementation

sys.modules[__name__] = _implementation
