# 显式 runtime 的灵识入口

`R` 是经过 manifest 校验的工具包或完整候选根，`P` 是新项目根。先把包中的 `config/tool_profile.example.json` 复制成 `P/profile.json`，只填写 tenant、相对产物路径、官方 origin 和环境键名；不在 profile 中放密钥。

```text
python R/scripts/orbit_tools.py --runtime-root R help lingshi
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json doctor --capability lingshi
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json preview lingshi media --payload request.json
```

`request.json` 示例：`{"model":"example-model","prompt":"Describe the supplied product facts only","params":{}}`。该示例只构建请求，不声称模型存在或价格有效。

完整 OrbitHive R2 使用 `skills/prepare-product-images/scripts/prepare_product_images.py` 与 `references/paid-recovery.md` 的现有合同。两个 product-family JSON 及 R3 消费者须在组合候选中检查；工具包不携带业务数据库、品牌、店铺或历史 ACTIVE 配置。
