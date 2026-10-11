# Py 书斋 · 拟物复古书架阅读器

纯 Python 桌面工具：扫描本地书籍目录，以拟物木书架呈现，支持 PDF 阅读、收藏、书签、高亮标记与阅读历史。

![](https://i.imgs.ovh/2026/09/25/1d311f978a26c6bae9fa00a8d330f441.png)

## 启动

双击 `launcher.bat`（自动探测 Python 解释器，使用系统 Python 3.12：PyQt5 + PyMuPDF + Pillow）。

- 首次使用需安装依赖：`pip install PyQt5 pymupdf pillow`（或运行 `build.bat` 后直接使用免安装的 `dist/PyShelf.exe`）。
- GUI 框架为 **PyQt5**（`QMainWindow` + `QStackedWidget` 页面切换，`QWidget` 自绘拟物书架/阅读页）。

- `launcher.bat` —— 正常启动（无控制台窗口）
- `launcher.bat debug` —— 控制台运行，报错直接可见，可追加参数如 `launcher.bat debug --smoke`

若程序异常退出（双击后无窗口），查看 `data/crash.log`；应用图标为 `app.ico`（标题栏 / 任务栏 / Alt-Tab）。

或手动运行：

```
C:\Users\yohoten\AppData\Local\Programs\Python\Python312\python.exe app.py
```

![](https://i.imgs.ovh/2026/09/25/aec6cbde019dc92f399f21feef74f95c.jpg)

![](https://i.imgs.ovh/2026/09/25/f6bc8a3345aed1fd293c63677792a5f5.jpg)

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
| 缩放 | 工具栏 ＋/－ 或 `+`/`-` 键或 **Ctrl+滚轮**（50%–300%）；「跳页」输入页码直达 |
| 收藏快捷键 | 阅读中按 `F` 切换当前书收藏 |
| 主题 | 顶栏「☾ 午后书房」切换深/浅主题（重启生效） |
| 窗口记忆 | 自动记住窗口大小/位置与筛选模式，下次启动还原 |
| 烛光模式 | 工具栏「烛光」按钮，暖色夜间阅读 |
| 阅读历史 | 自动记录进度，封面左上角显示「在读 N%」，重开自动续读；状态栏显示今日/累计阅读时长 |
| 搜索 | 顶栏输入书名实时过滤 |
| 书源检索 | 顶栏「书源检索」进入，见下节 |
| 设置 | 顶栏「⚙ 设置」打开设置中心，见下节 |

## 设置中心

顶栏「⚙ 设置」打开，分页如下（**保存后即时生效**，字体与主题类标注为重启生效）：

| 分页 | 可配置项 |
|---|---|
| 外观 | 配色主题（深色烛光/浅色午后，重启生效）、封面尺寸（小/中/大）、显示「最近阅读 / 收藏」分区、悬停信息提示 |
| 字体 | 界面字体、标题字体、字号缩放 60%–200%（重启生效） |
| 阅读 | 打开时缩放（适配整页 / 适配页宽 / 原始 100%）、打开即烛光模式、滚轮翻页 |
| 性能 | 页面渲染质量（1x / 2x 超采样）、后台渲染线程数（1–8） |
| 书源 | GitHub Token、快捷进入「管理书籍目录」 |

设置持久化在 `data/settings.json`；缺失键自动补齐默认值，旧配置无需手动迁移。

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

## 显示优化

- Windows 高 DPI 感知：封面与文字在高分屏下锐利不模糊
- 封面渲染升级：圆角 + 左侧书脊高光 + 右侧暗边 + 顶部柔光 + 柔和投影，悬停放大并加金色描边
- 程序化木纹背景与层板纹理（年轮曲线 + 颗粒 + 暗角），每行书本立于各自层板上
- 阅读页 2 倍超采样渲染 + 三层点阵柔和投影；「适配页宽」一键缩放
- 拟物书架楣板（书斋名牌 + 藏书统计）、分区标题装饰线
- 超长书名在阅读器标题栏自动截断省略，不再挤压工具按钮

## 性能

- **悬停局部重绘**：鼠标划过书架时只重绘受影响的书本及其层板，
  不再全量重绘整个书架（数百本规模下无卡顿、无闪烁）
- **增量扫描**：以文件指纹（mtime_ns:size）识别未变更文件，重复扫描时
  跳过 PDF 逐本探测（页数/可读性直接复用缓存），书库越大收益越明显
- **后台线程承担全部慢 IO**（扫描/封面/下载），主线程仅做 UI 更新；单条消息异常不拖垮事件泵
- **多线程渲染**：封面书脊/圆角/投影合成与阅读页超采样渲染均在线程池完成
  （`ui/render.py`），主线程只贴图；封面就绪后仅**局部重绘**该书副本，
  大书库滚动与翻页不再卡顿。线程数可在设置中调整

## 文件结构

```
app.ico             应用图标（多分辨率 16–128px，标题栏/任务栏/Alt-Tab）
launcher.bat        启动脚本（GBK+CRLF，自动探测解释器，支持 debug 模式）
build.bat           打包脚本（PyInstaller → dist/PyShelf.exe 单文件）
build.py            一键打包（环境/依赖自检 → UPX 压缩 → PyInstaller）
PyShelf.spec        PyInstaller 配置
requirements.txt    运行依赖（PyQt5 / PyMuPDF / Pillow）
app.py              入口（书架/阅读/书源切换、后台扫描与下载、设置与持久化）
core/models.py      Book/Bookmark/Highlight 数据模型
core/library.py     Library 门面（SQLite 后端 + 旧 JSON 自动迁移 + 阅读会话）
core/storage.py     SQLite 存储层（WAL、FTS5 书名索引、reading_sessions 统计）
core/scanner.py     目录扫描、书名清洗、封面生成
core/engines.py     DocumentEngine 协议 + 引擎工厂（PDF / TXT / MD）
core/pdf_engine.py  PyMuPDF 渲染/目录/高亮叠加/烛光滤镜
core/sources.py     书源引擎（GitHub/arXiv/Gutenberg/直链/自定义规则）
core/downloader.py  后台下载器（进度回报、备用通道）
ui/theme.py         双主题（烛光·深 / 午后书房·浅）、DPI、字体、QSS、木纹纹理、封面渲染
ui/shelf_view.py    QWidget 自绘拟物书架（纹理书架、悬停放大、缓存底板、增量重绘）
ui/reader_view.py   阅读器（后台超采样渲染/翻页/缩放/书签/拖框高亮）
ui/sidebar.py       目录/书签/高亮侧栏（QTabWidget）
ui/source_view.py   书源检索面板（QTableWidget）
ui/settings_dialog.py 设置中心（外观/字体/阅读/性能/书源）
ui/render.py        后台渲染服务（QThreadPool，PIL→QImage 回主线程）
tests/              pytest 测试（scanner 清洗 / storage 迁移与统计 / engines 分派）
.github/workflows/  CI：跑测试 + PyInstaller 打包并上传产物
data/               运行时生成：library.db（SQLite）、settings.json、covers/*.png
```

阅读数据（收藏/书签/高亮/进度/时长统计）全部存在本目录 `data/` 下，原 PDF 文件不做任何修改。

## 数据存储（v1.2 起 SQLite）

- 数据层已由 `library.json` 迁移至 `data/library.db`（SQLite，WAL 模式）：
  写入原子、规模化后无性能退化，并为全文搜索与统计功能提供地基。
- **旧版升级自动迁移**：首次启动检测到旧 `library.json` 且库为空时一次性导入，
  原 JSON 文件保留在原处作为回滚备份。
- 表结构：`books` / `bookmarks` / `highlights` / `notes`（预留）/ `reading_sessions`（时长统计）+ `books_fts`（FTS5 书名索引）。

## 开发与测试

```
python -m pytest tests/ -v      # 19 项核心逻辑测试
python app.py --smoke           # UI 冒烟（9 秒后自动退出）
```

## 打包发布

```
python build.py              # 推荐：环境自检 → UPX 压缩 → PyInstaller 打包
python build.py --test       # 打包前先跑单元测试（失败则中止）
python build.py --upx-dir D:\Tools\upx   # 指定 UPX 目录（默认 H:\UPX）
python build.py --no-upx     # 关闭 UPX 压缩
build.bat                    # 或：python -m PyInstaller PyShelf.spec --noconfirm
```

产物为 `dist/PyShelf.exe` 单文件（免安装，双击即用；阅读数据存于 exe 同目录 `data/`，
可直接把旧机器的 `data/` 整体拷到 exe 同目录完成迁移）。

UPX 压缩需「可用 + 开启」双条件：`PyShelf.spec` 已置 `upx=True`，`build.py` 默认传入
`H:\UPX` 作为 `--upx-dir`；换机或未装 UPX 时用 `--upx-dir` 指定目录，或 `--no-upx` 关闭
（UPX 不可用会被自动跳过，不影响出包）。压缩对象是收集到的 DLL/.pyd，bootloader 不压缩。
推送/PR 会触发 GitHub Actions（`.github/workflows/ci.yml`）：自动跑测试并打包 Windows 产物为构建附件。
推送/PR 会触发 GitHub Actions（`.github/workflows/ci.yml`）：自动跑测试并打包 Windows 产物为构建附件。
