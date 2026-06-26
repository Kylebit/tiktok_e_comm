# 商家 SKU 对照填写（临时工具）

对照 TikTok 批量编辑表中的**商品名称**和**主图**，逐条填写 `seller_sku`（商家 SKU）列，保存后直接写回 Excel。

## 使用

```bash
pip install openpyxl
python tools/sku_editor/app.py --xlsx "c:\Users\Windows11\Desktop\Tiktoksellercenter_batchedit_20260624_all_information_template.xlsx"
```

浏览器打开 http://127.0.0.1:8766/

- **Enter** 或「保存并下一个」：写入 Excel 并跳到下一条
- 勾选「只浏览未填写」：跳过已有 SKU 的商品
- 首次运行会在同目录生成 `.xlsx.bak` 备份

## 说明

- 自动识别主图为 `http` 开头的数据行（跳过表头说明行）
- 每次保存立即写回原 xlsx 文件
