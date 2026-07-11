"""旧 Pipeline 导入路径 façade。

正式 CLI 使用 application.ParseService；保留此路径用于 LAN 已有脚本和渐进迁移。
"""

import sys

from backend.extensions.products.current import pipeline as _implementation

sys.modules[__name__] = _implementation
