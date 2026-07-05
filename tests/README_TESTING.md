# Orbit 测试入口

当前仓库的测试分两层：

## 1. Python 契约 / 逻辑测试

现有测试位于：

- `tests/test_catalog_listings.py`
- `tests/test_miaoshou_client.py`
- `tests/test_new_product_workbench.py`
- 以及其他 `tests/test_*.py`

## 2. Playwright 浏览器级烟测

新加目录：

- `tests/playwright/`

首批覆盖：

- Orbit OS 打开
- Orbit Treasury 打开
- Orbit Rus 打开
- 商品目录表格壳子
- 商品目录图片壳子
- TK -> Shopee 同步入口
- Treasury 输入区
- Treasury 审核区壳子
- Desktop Bot 面板壳子

## 3. 统一执行命令

```bash
python scripts/run_regression.py --mode python
python scripts/run_regression.py --mode playwright
python scripts/run_regression.py --mode all
```

## 4. Playwright 环境准备

仓库不内置 Node.js。首次使用时需要先安装 Node.js，然后在仓库根目录执行：

```bash
npm install
npm run pw:install
```

之后执行：

```bash
npm run pw:test:smoke
```

## 5. 基础地址

如果端口变化，可通过环境变量覆盖：

- `ORBIT_OS_BASE_URL`
- `ORBIT_TREASURY_BASE_URL`
- `ORBIT_RUS_BASE_URL`
- `ORBIT_DESKTOP_HTML`
