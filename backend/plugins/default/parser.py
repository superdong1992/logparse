"""旧产品解析器类路径 façade；实现位于 LAN 产品扩展区。"""

import sys

from backend.extensions.products.current import parser as _implementation

sys.modules[__name__] = _implementation
