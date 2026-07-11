"""旧确定性 DFX 导入路径 façade。

正式实现位于 current-product 扩展，以便通用核心不认识 slot/CPU 投影；本
入口继续兼容 ``dfx-output``、``artifact-check`` 和 issue-locator。
"""

import sys

from backend.extensions.products.current import dfx as _implementation

sys.modules[__name__] = _implementation
