# summarize-to-a4

[![CI](https://github.com/smallyear/summarize-to-a4/actions/workflows/ci.yml/badge.svg)](https://github.com/smallyear/summarize-to-a4/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

> 让 A4 纸变成你的第 X 块屏幕。

把任意来源（文本、网页、PPTX、DOCX、PDF、XLSX、CSV）压成「尽量少纸张」的可打印 A4 PDF。

一个 [Agent Skill](https://agentskills.io/specification)：长文档进来，1–6 页速览版出去。

```
40 页 PPT  ──> 2 页 A4（矢量转曲，任何打印机都不乱码）
网页长文   ──> 1 页 A4
财务表格   ──> 整表保留，不压缩
```

## 它解决什么

用 LLM 总结文档不难，难的是**让它稳稳落在一张 A4 上、还能打印**：

| 常见坑 | 这里的做法 |
|---|---|
| 中文字体嵌进 PDF，换台打印机就乱码 | 最终 PDF 经 ghostscript **文字转曲**，页内 0 个字体对象，不依赖任何字体 |
| 内容多少不知道排几页，手工试错 | 按源规模定目标页数，**10 档字号自动降级**（12.6 → 7.7pt）去凑 |
| 压得狠了就丢关键信息 | A/B/C 三档分级：结论文本必留，并列项合并，过程描述删除 |
| 改一版摘要要重跑整套解析 | 中间态（原文 / 摘要 Markdown / HTML）全部落盘 |
| 表格被压成散句，失去可比性 | 整表保留，跨页自动重复表头 |

## 安装

把它放进你的 skills 目录即可（目录内是 `SKILL.md` 即被发现）：

```bash
git clone <this-repo> ~/.agents/skills/summarize-to-a4
```

Pi / Claude Code / 其他实现了 Agent Skills 规范的宿主都能读。

## 依赖

```bash
~/.agents/skills/summarize-to-a4/scripts/a4 env
```

一条命令自检全部依赖，缺什么会直接打出来。

| 用途 | 需要 |
|---|---|
| 运行时 | **Python 3.10+** |
| 渲染与自检 | `weasyprint`、`pypdfium2`、`pypdf`、`Pillow` |
| 解析各格式 | `python-pptx`、`python-docx`、`openpyxl`、`beautifulsoup4`、`requests` |
| 文字转曲 | `ghostscript`（`gs`）—— **必装**，否则打印可能乱码 |
| 中文字体 | **必装**，如 `Noto Sans CJK`。缺失时中文静默变豆腐块，`verify` 查不出来；`render` 会报警告 |
| 可选 | `slidep`（PPTX 结构化抽取，缺失时回退 `python-pptx`） |

```bash
# macOS
brew install ghostscript
pip install -r requirements.txt

# Debian/Ubuntu：系统包（含中文字体与 gs）
apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
                   libcairo2 libgdk-pixbuf-2.0-0 fonts-noto-cjk ghostscript
```

> `ghostscript` 是系统依赖（`gs` 命令），pip 装不了，必须单独装。

> **macOS 注意**：`weasyprint` 必须先 `a4env.prepare()` 设好 `DYLD_FALLBACK_LIBRARY_PATH` 才能 import。裸 `import weasyprint` 一定失败，那不是缺包，别急着 `pip install`。`scripts/a4` 已经把这一步包进去了——**所有调用都走它**。

## 用法

```bash
A4=~/.agents/skills/summarize-to-a4/scripts/a4

# 1. 解析任意格式 -> Markdown 中间态
$A4 extract report.pptx -o work/report.source.md

# 2. 写摘要（受限 Markdown，见下）
#    -> work/report.outline.md

# 3. 出 PDF（自动选字号凑到目标页数）
$A4 render work/report.outline.md -o work/report.html \
    --target-pages 2 --title "季度经营报告" --subtitle "来源：report.pptx（32 页）"

# 条目型速查（规则清单、核对项）可以开双栏，能省一半纸
$A4 render work/rules.outline.md -o work/rules.html \
    --target-pages 1 --auto-columns --pack-sections --title "..."

$A4 topdf work/report.html -o work/report.pdf

# 4. 自检：页数 / A4 / 字体残留 / 末页填充度 + 缩略图
$A4 verify work/report.pdf --target-pages 2 --thumb work/thumb.png
```

`--target-pages` 是**上限**不是配额：内容只有 1 页、目标写 2，结果就是 1 页，不会被拉长。

## 摘要语法

受限 Markdown，够用且不会把版面搞坏：

```markdown
# 文档大标题          <- 只认第一个，后续 # 自动降为 ##

## 一、小节
### 子标题

正文段落。支持 **粗体** 和 `行内代码`。

- 列表项
- 只有缩进的续行会并入上一项，顶格行是新段落

| 列一 | 列二 |          <- GFM 表格，单元格内换行不支持
|---|---|---|              <- 分隔行必须用裸竖线
| 值 | 含 \| 转义竖线 |    <- 格内竖线要转义

> 灰底提示块
> ! 橙底警示块
```

**表格前后必须空一行。** 这不是风格问题——紧跟在表格后面、不带首尾竖线的短行（如 `短标签 | 短标签`）在 GFM 规范里本身就是合法表格行，无法与说明文字区分，会被并入表格。

## 单栏还是双栏

双栏省的从来不是**字数**，是**短行末尾的空白**。所以只对条目型内容有效。

| 内容形态 | 结论 |
|---|---|
| 条目 10–20 字（规则清单、披露点、核对项） | **用双栏**，实测能省一半纸 |
| 条目 25–35 字 | 可用 |
| 散文、连续论证（行接近满行） | **别用**，页数完全相同还更难读 |
| 表格 ≥ 5 列、格内长句 | **别用**，栏宽撑不下 |
| 合同 / 法规原文 / 财务三表 | 本就该劝退 |

不想自己判断就用 `--auto-columns`：它会把单栏和双栏**各算一遍**，按
`页数 → 字号 → 偏好单栏` 挑更好的那个。不是猜，是实测。

两个进阶开关：

| 开关 | 作用 |
|---|---|
| `--pack-sections` | 语义分栏：按实测高度把小节整块装进各栏，小节绝不跨栏 |
| `--span-headings` | 小节标题通栏（也能防跨栏，但小节多时会多花页） |

分栏时还会自动做三件事：页边距收窄到 `7mm`（栏宽 270.7pt，每行约 30 字）、
**字号给「一条占一行」让位**（否则双栏只是把空白从行末挪到第二行）、
对宽表/超宽等宽块给出警告。

判级标签 `[[C]] [[ND]] [[E]] [[NA]]` 会渲染成四色标签，表格里出现任一标签就**整行着色**，
扫读时看颜色就能定位问题行，不用逐字读。

## 输出示例

```
report.pdf          矢量转曲，0 字体对象，A4，打印即用
report.outline.md   摘要源文件，可直接改
report.source.md    原文中间态，改摘要不必重跑解析
thumb.png           3 列缩略图，用来人眼质检版面
```

## 不适合的场景

压缩即失真，这些应该要"原文摘录"而不是"总结"：

- 合同、法规原文、审计底稿 —— 需要逐字保真
- 招股书风险因素、法律意见书
- 财务三表、大矩阵表 —— 整表保留，别压

## 开发

```bash
python3 regress.py     # 82 条回归断言
```

覆盖：表格语法与转义、参数注入、多级标题、段落与列表边界、提示块分色、五类编码损坏、
HTML 抽取去重、PPTX 备注、DOCX 内容控件、XLSX 表头启发式、PDF 批注、verify 语义、
字号档位、fontconfig 隔离、缺失中文字体、临时目录清理，以及分栏（几何、自动判据、
字号让位、语义分栏）和**页数自洽性**（render 声称的页数必须等于实际 PDF 页数）。

## 目录

```
SKILL.md                    技能说明（Agent 读这个）
scripts/a4                  统一入口：extract / render / topdf / verify / env
scripts/extract.py          任意格式 -> Markdown
scripts/render.py           Markdown -> A4 HTML（自动选字号）
scripts/topdf.py            HTML -> PDF（ghostscript 转曲）
scripts/verify.py           自检 + 缩略图
scripts/a4env.py            weasyprint 环境准备 + fontconfig 隔离
references/layout-spec.md   版面规格、字号档位、分页铁律
references/density-rules.md A/B/C 压缩规则、分格式策略
regress.py                  回归测试
requirements.txt            Python 依赖
.github/workflows/ci.yml    CI：回归 + 端到端冒烟
```

## License

[MIT](LICENSE) © 2026 smallyear

使用到的第三方组件（weasyprint、ghostscript、pypdfium2 等）按各自许可分发，本仓库未内嵌其代码。
