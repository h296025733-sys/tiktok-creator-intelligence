# TikTok 达人商业内容分析

我做这个项目，是想让达人筛选有一条能回头核对的依据。除了粉丝量，还要看他拍过什么商业内容、哪些视频表现较好、产品如何出镜，以及这套表达方式适不适合我的产品。

项目把数据整理和视频判断分开：Python 负责采集、分类线索、统计和素材准备；当前 Codex 会话负责阅读画面与字幕，最后按结构化结果生成报告。

```text
公开主页 → 视频元数据 → 商业线索分类 → 表现比较
        → 视频与关键帧准备 → 结构化复核 → 产品适配报告
```

## 主要功能

- 区分明确广告标识、商品挂链和推测商业内容。
- 同时比较原始表现和达人自身的相对表现。
- 为候选视频准备镜头、关键帧、公开字幕和证据哈希。
- 整理钩子、演示、证明、剪辑、口播及 CTA，输出产品适配与沟通建议。
- 提供 CLI 和仓库内 Codex Skill；默认技能路径使用当前会话。

## 快速开始

要求 Python 3.11+。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\creator-intel.exe version
.\.venv\Scripts\creator-intel.exe prepare-codex "https://www.tiktok.com/@creator" --limit 30 --top 5 --product-description "便携保温杯"
```

也可以在 Codex 中使用根目录 `SKILL.md`，给出公开主页、产品描述和授权产品图片。`prepare-codex` 只准备证据；报告需要完成结构化复核后再由 `finalize-codex` 生成。

`src/creator_intel/` 是实现，`tests/` 包含分类、排序、媒体与故障恢复测试。商业关系判断有不确定性，挂链不等于付费合作，表现关联也不能证明转化因果。真实采集还受地区、公开可见范围和平台变化影响。

## 本次公开整理的检查

见 [检查记录](docs/verification.md)，其中区分源码与语法检查、隔离测试和未执行的真实环境路径。
