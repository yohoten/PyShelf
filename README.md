# Py 书斋 · 拟物复古书架阅读器

纯 Python 桌面工具：扫描本地书籍目录，以拟物木书架呈现，支持 PDF 阅读、收藏、书签、高亮标记与阅读历史。

![](https://i.imgs.ovh/2026/09/25/1d311f978a26c6bae9fa00a8d330f441.png)

## 启动

双击 `launcher.bat`（自动探测 Python 解释器，使用系统 Python 3.12：tkinter + PyMuPDF + Pillow，无需安装任何依赖）。

- `launcher.bat` —— 正常启动（无控制台窗口）
- `launcher.bat debug` —— 控制台运行，报错直接可见，可追加参数如 `launcher.bat debug --smoke`

若程序异常退出（双击后无窗口），查看 `data/crash.log`；应用图标为 `app.ico`（标题栏 / 任务栏 / Alt-Tab）。

或手动运行：

```
C:\Users\yohoten\AppData\Local\Programs\Python\Python312\python.exe app.py
```

## 使用说明

| 操作 | 方式 |
|---|---|
| 选择书库 | 首次启动选择目录，或右上角「选择书库目录」重新选择（递归扫描 PDF/EPUB/TXT/MD） |
| 阅读 | 双击封面；首次扫描时封面在后台逐本生成，未完成前显示彩色书脊占位 |
| 收藏 | 封面右键 → 加入收藏；书架「收藏 ★」分区展示 |
| 书签 | 阅读工具栏「加书签」（可写备注）；红丝带标记当前书签页 |
| 高亮 | 阅读页面上按住左键拖框 → 输入备注（可选）；琥珀色叠加显示，不改原 PDF |
| 目录/书签/高亮 | 阅读界面左侧栏三个标签页，双击条目跳转 |
| 翻页 | ←/→、PageUp/PageDown、滚轮；Home/End 首末页 |
| 缩放 | 工具栏 ＋/－（50%–300%）；「跳页」输入页码直达 |
| 烛光模式 | 工具栏「烛光」按钮，暖色夜间阅读 |
| 阅读历史 | 自动记录进度，封面左上角显示「在读 N%」，重开自动续读 |
| 搜索 | 顶栏输入书名实时过滤 |
| 书源检索 | 顶栏「书源检索」进入，见下节 |

## 书源检索

顶栏「书源检索」打开书源面板，支持多来源搜索与一键下载入库（下载到书库目录下 `_网络下载/`，自动重新扫描）：

| 书源 | 用法 |
|---|---|
| **GitHub 开源书库** | 关键词搜索开源书籍仓库 → 双击仓库行列出其中 PDF/EPUB/TXT 文件 → 选中下载（jsDelivr 主通道 + raw 备用）。可选填 GitHub Token 提升限额 |
| **arXiv 论文** | 关键词检索预印本，直接下载 PDF（官方限流时自动退避重试并提示） |
| **Gutenberg 公版书** | 输入书号（如 `1342`）下载公版 TXT/EPUB |
| **URL 直链导入** | 粘贴任意 http(s) 的 PDF/EPUB/TXT 直链直接入库 |
| **自定义书源**（◆） | 「新增自定义」填写 JSON 规则：`search_url` 模板（含 `{q}` 占位符）+ `results_path` 结果数组路径 + `title/author/format/download_url` 字段映射。可对接任何您有权访问的自建站点 API，内容合法性由配置者自行负责 |

> 说明：内置书源均为公开合法资源（开源仓库、预印本、公版书、自有直链）；
> 不提供也不预设盗版书库（如 Z-Library）接入。

## 显示优化（本次更新）

- Windows 高 DPI 感知：封面与文字在高分屏下锐利不模糊
- 封面渲染升级：圆角 + 左侧书脊高光 + 右侧暗边 + 顶部柔光 + 柔和投影，悬停放大并加金色描边
- 程序化木纹背景与层板纹理（年轮曲线 + 颗粒 + 暗角），每行书本立于各自层板上
- 阅读页 2 倍超采样渲染 + 三层点阵柔和投影；「适配页宽」一键缩放
- 拟物书架楣板（书斋名牌 + 藏书统计）、分区标题装饰线

## 文件结构

```
app.ico             应用图标（多分辨率 16–128px，标题栏/任务栏/Alt-Tab）
launcher.bat        启动脚本（GBK+CRLF，自动探测解释器，支持 debug 模式）
app.py              入口（书架/阅读/书源切换、后台扫描与下载、持久化）
core/library.py     Book 数据模型 + data/library.json 读写
core/scanner.py     目录扫描、书名清洗、封面生成
core/pdf_engine.py  PyMuPDF 渲染/目录/高亮叠加/烛光滤镜
core/sources.py     书源引擎（GitHub/arXiv/Gutenberg/直链/自定义规则）
core/downloader.py  后台下载器（进度回报、备用通道）
ui/theme.py         配色/DPI/字体/程序化木纹纹理/圆角封面渲染
ui/shelf_view.py    Canvas 拟物书架（纹理书架、悬停放大）
ui/reader_view.py   阅读器（超采样渲染/翻页/缩放/书签/拖框高亮）
ui/sidebar.py       目录/书签/高亮侧栏
ui/source_view.py   书源检索面板
data/               运行时生成：library.json、settings.json、covers/*.png
```

阅读数据（收藏/书签/高亮/进度）全部存在本目录 `data/` 下，原 PDF 文件不做任何修改。
# PyShelf
# PyShelf
