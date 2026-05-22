# 漫画下载器

一个基于 Playwright 的漫画图片批量下载工具，能够自动滚动页面、触发懒加载、并发下载图片，并具备断点续传、失败重试、跳过记录等功能。同时提供独立的 PDF 合并脚本，可将下载的图片整合为一个完整的 PDF 文件。

## 主要功能

- **懒加载图片自动收集**  
  通过小步滚动触发页面图片懒加载，逐张提取漫画原图链接。

- **并发下载**  
  同一章节内支持多张图片同时下载，可配置并发数量，大幅提升下载速度。

- **断点续传**  
  记录当前下载进度，即使中途退出，下次运行也能从上次中断的章节继续。

- **失败处理与重试**  
  图片下载失败会自动重试，重试耗尽后记录至 `failed_downloads.jsonl` 文件，方便手动补下或稍后统一重试。  
  若某章节图片收集不全（如网络波动导致），会跳过该章节并记录至 `skipped_chapters.jsonl`，同时自动继续下一话。

- **友好的交互提示**  
  每下载完一定数量的章节（可配置），会询问是否继续，方便分批次下载。

- **PDF 合并**  
  提供独立脚本，将下载的所有章节图片按顺序合并为单个 PDF 文件，方便阅读和收藏。

## 依赖环境

- Python 3.8 或更高版本
- 需要安装以下 Python 库：

```bash
pip install playwright requests img2pdf Pillow
```

- 安装浏览器内核（Chromium）：

```bash
playwright install chromium
```

`img2pdf` 和 `Pillow` 仅用于 PDF 合并脚本，如果不需要生成 PDF 可忽略。

## 文件说明

| 文件名 | 用途 |
|--------|------|
| `comic_downloader.py` | 漫画下载主脚本 |
| `merge_to_pdf.py` | 将下载的图片合并为 PDF |
| `progress.json` | 下载进度文件（由脚本自动生成） |
| `failed_downloads.jsonl` | 下载失败的图片记录 |
| `skipped_chapters.jsonl` | 因图片不全而跳过的章节记录 |

## 快速开始

### 1. 配置参数

打开 `comic_downloader.py`，修改顶部的配置区。**必须修改的项**已用注释标注：

```python
SAVE_DIR = "downloads"                # 下载根目录，可任意命名
BASE_URL = "https://example.com"      # 目标网站的主域名（用于拼接相对链接和 Referer）
INITIAL_URL = "https://example.com/comic/xxx/chapter/1"   # 第一话的完整 URL
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)                                      # 可改为自己浏览器的 User-Agent，或保持通用值
```

其他参数可按需调整（如并发数、重试次数等），均有详细注释。

### 2. 运行下载脚本

```bash
python comic_downloader.py
```

脚本启动后会：

- 自动读取 `progress.json`（如果存在）恢复进度；
- 若检测到 `failed_downloads.jsonl` 或 `skipped_chapters.jsonl`，会询问是否先处理失败/跳过的内容；
- 按章节顺序下载图片，文件会自动保存为 `第001话/001.01.jpg`、`第001话/001.02.jpg` … 的格式；
- 每下载完一批章节（默认 1 话）会询问是否继续，输入 `n` 可保存进度并退出。

### 3. 合并为 PDF

下载完成后，将 `merge_to_pdf.py` 复制到 `SAVE_DIR` 所指定的文件夹内（例如 `downloads/`），然后运行：

```bash
python merge_to_pdf.py
```

脚本会扫描当前目录下所有 `第XXX话` 的文件夹，并按顺序合并图片，生成 `漫画.pdf`（文件名可在脚本顶部修改）。

## 配置参数详解

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SAVE_DIR` | `"downloads"` | 所有漫画章节的保存根目录 |
| `BASE_URL` | `"https://example.com"` | 目标网站的主域名，用于拼接相对路径和 Referer |
| `INITIAL_URL` | （示例链接） | 第一话的完整 URL，程序从此开始下载 |
| `USER_AGENT` | 通用 Chrome UA | 浏览器标识，可修改为自己浏览器的 UA |
| `CHAPTER_DELAY` | `3` | 两个章节之间的等待秒数，避免请求过快 |
| `BATCH_SIZE` | `1` | 每下载完多少话后询问是否继续 |
| `MAX_CONCURRENT_DOWNLOADS` | `5` | 同一章节内同时下载的图片数量 |
| `PROGRESS_FILE` | `"progress.json"` | 进度记录文件路径 |
| `FAILED_FILE` | `"failed_downloads.jsonl"` | 图片下载失败记录文件路径 |
| `SKIPPED_FILE` | `"skipped_chapters.jsonl"` | 被跳过章节的记录文件路径 |
| `MAX_PAGE_RETRIES` | `3` | 章节页面加载失败的最大重试次数 |
| `MAX_REFRESH_RETRIES` | `2` | 图片收集不全时刷新页面并重新收集的最大次数 |
| `RETRY_DELAY` | `2` | 重试之间的等待秒数 |
| `BROWSER_ARGS` | 一系列 Chromium 参数 | 用于防止无头模式下渲染降级导致懒加载失效 |
| `viewport` | `1280x720` | 浏览器视口大小，可根据需要调整以适配不同网站 |

## 高级用法与常见问题

### 进度文件 `progress.json` 损坏或为空

如果手动修改了该文件导致格式错误，或文件内容为空，程序会直接报错并提示解决方案。  
**解决方法**：直接删除 `progress.json`，程序会自动从第一话重新开始。

### 图片下载失败

下载失败的图片会记录在 `failed_downloads.jsonl` 中，每条记录包含章节编号、图片序号和图片 URL。  
下次运行脚本时，检测到该文件会询问是否立即重试，重试成功则自动移除记录，若仍失败则保留记录以便手动下载。

### 章节被跳过

若某个章节在滚动收集后图片数量仍然不足，该章节会被记录到 `skipped_chapters.jsonl`，程序自动继续下一话。  
下次运行脚本时同样会询问是否处理跳过的章节，处理时会重新打开该话链接，尝试完整下载。

### 调整滚动速度或步长

如果遇到图片漏抓，可以打开 `comic_downloader.py`，找到 `scroll_and_collect_urls` 函数中的：

- `scroll_step = 300`（每次滚动的像素，减小可提高触发概率）
- `await page.wait_for_timeout(500)`（滚动后等待时间，增加可给网络更多缓冲）

根据实际情况微调这两个值即可。

### 无头模式与可视化调试

脚本默认使用无头模式（`headless=True`）运行，以节省资源。若想查看浏览器实际滚动过程，可在 `main()` 函数中找到：

```python
browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
```

将 `headless=True` 改为 `headless=False`，并适当增加 `slow_mo` 参数（例如 `slow_mo=200`），即可看到浏览器窗口和每一步操作。

### 兼容性说明

本工具的目标网站需要满足以下特征（不同网站可能需要调整选择器）：

- 章节页面存在一个元素显示当前总图片数，选择器为 `.comicCount`
- 漫画图片加载后带有类名 `lazyloaded`
- 下一话链接位于 `div.comicContent-next a` 中
- 最后一话时，下一话链接的类名包含 `prev-null`

如果你的目标网站结构与上述不符，可在代码中相应修改选择器。**本工具仅提供一个针对常见漫画网站的通用实现，使用者可根据实际情况自行调整。**

## 免责声明

本工具**仅供技术学习与研究使用**，旨在演示如何使用 Playwright 进行动态网页爬取、并发下载、断点续传等编程技巧。  

使用者应**严格遵守目标网站的 robots.txt 及服务条款**，不得将本工具用于任何侵犯版权或违反法律法规的用途。  

作者**不承担任何因不当使用本工具而产生的法律责任**。下载、使用、分发本工具即表示您已同意自行承担所有风险。

## 许可证

本项目采用 [MIT License](LICENSE) 开源。
