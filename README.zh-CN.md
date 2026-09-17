# PaperPipeline

在论文页面点一下，自动完成：识别题录 → 获取 PDF → 翻译 → 入库 Zotero。

产出四样东西一起进库：**原文 PDF、双语对照 PDF、中文译本 PDF、术语表 CSV**。

## 目录

- 它解决什么问题
- 安装
- 准备翻译环境
- 配置项详解
- 日常使用
- 常见问题
- 卸载

## 它解决什么问题

手动流程通常是：找到论文 → 下载 PDF → 丢进文件夹 → 右键翻译 → 等 →
把原文和译文拖进 Zotero → 填题录 → 想想放哪个分类。

PaperPipeline 把它压成一次点击：打开论文页 → 点右下角按钮 →
剩下的它做。论文带着完整题录进 Zotero 的收件箱分类，等你读完再归类。

## 安装

### 需要什么

| 依赖 | 说明 |
| --- | --- |
| Windows 10 / 11 | 用了注册表自启和 Windows 路径 |
| Python 3.9+ | 跑本地服务。从 python.org 下载，安装时勾选 Add to PATH |
| Zotero 7 或更新 | 文献库 |
| Chrome 或 Edge | 插件运行的地方 |
| 一个 OpenAI 兼容的模型接口 | 翻译用，见第 3 步 |

### 三步

**1. 准备翻译环境**（只需一次，见下一节）

**2. 双击 install.cmd**

会出现向导，依次问五件事：Zotero 数据目录、翻译环境路径、
模型凭据、下载目录、收件箱分类名。每项都有说明，回车即用默认值。

**3. 装 Chrome 插件**

1. 地址栏输入 chrome://extensions
2. 右上角打开「开发者模式」
3. 点「加载已解压的扩展程序」
4. 选择本项目里的 extension 文件夹

装完打开任意论文页（arXiv、ACL、IEEE 这类），右下角会出现按钮。

## 准备翻译环境

翻译由 **pdf2zh-next** 完成。它依赖较重，需要独立环境。

### 用 conda（推荐）

装好 Miniconda 后，打开 Anaconda Prompt 执行：

    conda create -n pdf2zh_next python=3.11 -y
    conda activate pdf2zh_next
    pip install pdf2zh-next

记下这个环境的 python 路径，一般是：

    C:\Users\你的名字\miniconda3\envs\pdf2zh_next\python.exe

向导第 2 步会要这个路径，并且会真的导入一次引擎来验证是否可用。

### 用 venv

    py -3.11 -m venv C:\tools\pdf2zh
    C:\tools\pdf2zh\Scripts\pip install pdf2zh-next

对应路径就是 C:\tools\pdf2zh\Scripts\python.exe。

> 首次翻译时引擎会自动下载模型资源（数百 MB），可能要等几分钟。

## 配置项详解

所有配置在 **config.json**（向导生成）。改完重启服务生效：
双击 pipeline.cmd，或运行 pipeline.cmd restart。

### service 段

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| port | 8787 | 本地服务端口。只监听 127.0.0.1，外部访问不到。被占用时改成别的（如 8788），**改完要重跑 install.cmd**，因为插件里也记了端口 |

### paths 段

| 字段 | 含义 |
| --- | --- |
| pythonw | 无窗口版 Python，用于开机自启。留空表示不注册自启 |
| translatorPython | 装有 pdf2zh-next 的 python.exe。翻译时由它启动子进程 |
| zoteroDataDir | Zotero 数据目录（含 zotero.sqlite）。只在安装时用来创建分类 |
| downloadDirs | 寻找已下载 PDF 时搜索的文件夹 |

**downloadDirs 的用途**：如果你已经用浏览器下过这篇论文的 PDF，
程序会直接复用那个文件，不再从网上重新下载。它拿论文标题去比对这些目录里的
文件名。留空则自动尝试 ~/Downloads。

### translator 段

翻译引擎设置。三个 openai_ 开头的字段必填。

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| openai_base_url | — | 接口地址，多数服务要求以 /v1 结尾 |
| openai_api_key | — | 密钥。**明文存在本地文件里**，别把这个文件夹发给别人 |
| openai_model | — | 模型名，要与接口方文档一致 |
| lang_in | en | 原文语言 |
| lang_out | zh-CN | 译文语言 |
| qps | 10 | 每秒最多发几个翻译请求 |
| pool_max_workers | 20 | 并发线程数，一般不用动 |
| term_qps | 10 | 术语提取那一轮的请求速率，单独限制 |
| term_pool_max_workers | 20 | 术语提取的并发数 |
| watermark_output_mode | no_watermark | 水印模式：no_watermark / watermark / both |
| stop_at_references | true | 自动跳过参考文献，只翻正文 |

**关于 qps**：调大更快，但接口方会限流。付费接口 10 通常没问题；
免费额度或共享密钥建议 2-4。报错里出现 429 就是调太高了。

**关于 stop_at_references**：开启后先分析 PDF 结构，找到参考文献起始页，
只翻译它前面的部分。既省钱又让译文更好读。个别排版奇怪的论文识别不出来时，
会自动退回全篇翻译（多花点钱，但不会出错）。

### zotero 段

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| tempCollection | 00 TEMP | 新论文先进这个分类。名字前的数字让它排在最上面 |
| tags | 待读、自动入库 | 每篇自动打的标签，方便筛选 |

读完一篇，在 Zotero 里把它拖到正式分类即可。**Zotero 的拖拽是「加归属」
而不是「移动」**，所以条目仍然留在收件箱里——正好留下一份阅读顺序的记录。

## 日常使用

### 一次典型操作

1. 浏览器打开论文页（arXiv、ACL、Springer、IEEE 等都支持）
2. 右下角出现「翻译并入库」，点它
3. 面板里确认标题对不对，点「翻译并入库」
4. 看进度走完（10 页论文通常 3-6 分钟）
5. 去 Zotero 的收件箱分类取货

任务跑在本地服务里，**关掉页面不影响进度**。重开工具栏图标还能看到。

### 工具栏图标

点 Chrome 工具栏的 PaperPipeline 图标可以看到：

- 当前页面识别出的论文信息（也能在这里直接提交）
- 最近任务列表和进度
- 图标角标数字 = 正在跑的任务数

### 管理服务

| 操作 | 怎么做 |
| --- | --- |
| 看状态 | 双击 pipeline.cmd |
| 重启 | pipeline.cmd restart（服务出问题先试这个） |
| 看日志 | pipeline.cmd logs |
| 停止 | pipeline.cmd stop |

装了开机自启就平时不用管；没装就每天双击一次 pipeline.cmd start。

### 速度预期

一篇 10 页论文，取决于模型速度和并发：

- 结构分析：约 10 秒
- 术语提取 + 正文翻译：2-5 分钟
- 漏翻检查：几秒
- 入库：约 5 秒

如果检查出漏翻，会**自动用兼容模式重跑一遍**，时间翻倍。

### 文件都在哪

    paper-pipeline\
      config.json          你的配置（含密钥，别外传）
      state.json           运行时状态和令牌
      logs\service.log     服务日志
      work\<任务号>\       每篇的中间产物
        original.pdf       下载或复用的原文
        translated\        译文三件套
        worker.err.log     翻译引擎日志（排错看这个）
      extension\           浏览器插件
      vendor\              参考文献检测、漏翻检查模块

入库时 Zotero 会把文件**复制**进自己的存储目录，
所以 work 里的副本是冗余的，可以定期清理。

## 常见问题

**点按钮没反应，或提示「无法连接本地服务」**

服务没在跑。打开项目目录双击 pipeline.cmd 看状态，必要时 restart。

**提示「找不到或无法下载这篇论文的 PDF」**

多半是付费墙。手动下载 PDF 放进 downloadDirs 里的某个目录再试，
或者在 Zotero 里把 PDF 拖到已生成的条目上。

**翻译报 429 或很慢**

把 translator.qps 调小（试 3），重启服务。

**Zotero 里找不到条目**

确认 Zotero 开着。没开的话任务会排队，启动后自动补上。
一直不入库就看 pipeline.cmd logs。

**分类没建成功**

建分类要求 Zotero 处于关闭状态。关掉 Zotero，重跑 install.cmd。

**翻译卡住不动**

看 work\<任务号>\worker.err.log 最后几行。确认卡死了就
pipeline.cmd restart，再重新提交那篇。

**想换模型**

改 config.json 里 translator 的三个字段，重启服务。

**换电脑或换 Zotero 数据目录**

重跑 install.cmd，向导会重新检测并更新配置。

## 卸载

1. pipeline.cmd stop（或在任务管理器结束进程）
2. 去掉开机自启，在命令提示符执行：

       reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v PaperPipeline /f

3. 在 chrome://extensions 里移除插件
4. 删掉整个项目文件夹

Zotero 里的文献和分类不会被删除。想连收件箱分类也去掉，
在 Zotero 里右键删除即可（删分类不删条目，只是取消归类）。

## 它不做什么

- 不替你判断论文该放哪个正式分类，那是你读完之后的判断
- 不改动 Zotero 里已有的条目和分类
- 不上传任何东西到第三方。网络请求只有两处：查题录（Crossref / OpenAlex /
  arXiv），以及调用你自己配置的翻译接口

## 数据与隐私

- 本地服务只监听 127.0.0.1，并用随机令牌校验请求来源
- API 密钥只写在 config.json 里，不经过任何中转
- 论文内容只发给你自己配置的那个翻译接口
