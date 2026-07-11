"""旧 result serializer 导入路径 façade。"""

import sys

from backend.extensions.products.current import result_serializer as _implementation

sys.modules[__name__] = _implementation
