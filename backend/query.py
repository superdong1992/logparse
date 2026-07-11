"""旧查询服务导入路径 façade。

产品范围、生命周期和 slot/CPU 路径解析已迁入 current-product 扩展；保留
``backend.query`` 供 issue-locator、LAN 脚本和既有测试渐进迁移。
"""

import sys

from backend.extensions.products.current import query as _implementation

sys.modules[__name__] = _implementation
