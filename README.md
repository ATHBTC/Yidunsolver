# Yidunsolver

网易易盾（YiDun）滑块拼图验证的自动求解脚本，通过 Chrome DevTools Protocol (CDP) 驱动已打开的浏览器标签页。

## 原理
1. 把 `.yidun` 组件滚动到可见区域（拼图面板只有可见时才渲染）
2. 读取 `.yidun_bg-img` 和 `.yidun_jigsaw` 的图片地址并下载
3. 用拼图块的 alpha 轮廓与背景图对应行带的 Canny 边缘做模板匹配，定位缺口
4. 按显示宽度/原图宽度换算成屏幕像素
5. 用 `Input.dispatchMouseEvent` 模拟拖动：先加速后减速、上下抖动、轻微过冲再回拉，并根据拼图块实际位置闭环微调

## 安装
```bash
pip install -r requirements.txt
```

## 使用
```bash
# 启动带调试端口的 Chrome/Chromium，打开出现滑块的页面
chromium --remote-debugging-port=9222

# 参数为目标标签页 URL 中的关键字
python3 yidun_solver.py login.example.com
python3 yidun_solver.py login --cdp http://127.0.0.1:9222 --tries 5
```

在代码中调用：
```python
from yidun_solver import Tab, solve
r = solve(Tab('login'))   # {'status': 'ok', ...}
```

返回 `status`：`ok` 成功，`no-slider` 页面没有滑块，`fail` / `no-images` / `fetch-fail` / `no-match` 为失败（会自动刷新重试）。

## 说明
- 只支持易盾"拼图滑块"类型，不支持点选、文字等其他验证。
- 仅供学习研究，请遵守目标网站的使用条款。
