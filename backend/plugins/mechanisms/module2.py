"""旧 Module2 类路径 façade；实现位于受保护 LAN 机制区。"""

import sys

from backend.extensions.mechanisms import module2 as _implementation

sys.modules[__name__] = _implementation
