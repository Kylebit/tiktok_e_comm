# b17b8bfc 的全局图片预算配对修正

复核发现 b17 中区域上传已在下载之后 reserve，但 global 默认上传函数内部仍包含下载、解码压缩；外层提前 reserve 会把这些本地失败计入 mutation，并让 resolver 报 UNKNOWN。

现在默认 `_default_image_upload` 在下载、压缩和本地文件准备完成后、实际 `upload_image` 前才回调 reserve。首次下载/解码失败的占用和写入均为 0；已完成一张后第二张本地失败保留 1；第二张 POST 超时或返回无 image identity 保留 2 次尝试、至少 1 张完成及 UNKNOWN。已确认图片回执后的 checkpoint 落盘失败保留已写数。自定义注入的 image transport 仍约定为一次 mutation 边界，保持既有测试与调用签名。

`tests/test_b4b_global_image_boundary.py` 沿真实默认下载→压缩→临时文件→上传→解析→checkpoint→resolver 链执行，只替换最低层下载和上传。6 个红例保存于审计输出 `global-image-boundary-red.xml`；修后相关 4 测试文件 **85 passed /0skip**，`global-image-boundary-green.xml`。

这是 b17 图片预算范围的必要配对修正；Ozon 预算异常/部分结果恢复和 R3 runner 接入仍需后续配对，不称整包完成。早期 test-stable 隔离事故及其边界仍以 `TEST_ISOLATION_INCIDENT.md/json` 为准，本次使用同一 Python audit-hook 离线 launcher，没有真实平台验证。
