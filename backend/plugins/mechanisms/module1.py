"""旧 Module1 类路径 façade；实现位于受保护 LAN 机制区。"""

import sys

from backend.extensions.mechanisms import module1 as _implementation

sys.modules[__name__] = _implementation
