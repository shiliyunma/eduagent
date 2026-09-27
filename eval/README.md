# eval · 自动化评测

一套可复现的教育智能体评测流程：**43 条输入 × 5 条对照臂 × 三层指标 → 一份可视化报告**。

指标口径、为什么这么选、以及每张图回答什么问题，写在
[`../../09-自动化评测流程与指标标准.md`](../../09-自动化评测流程与指标标准.md)。本文件只说怎么跑。

## 四条命令

```bash
cd "D:/AI+教育毕设毕设/毕设规划/脚手架"

# ① 全量跑（不需要 key：没有 API key 时自动用 mock 模型，只验链路）
python eval/run_eval.py

# ② 用真模型跑（**写进论文的数字必须这么来**）
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-chat
python eval/run_eval.py --arms a0,a1,a2 --runs 3 --tag real

# ③ 出报告（自包含 HTML + Markdown 表格）
python eval/report.py --tag real --open

# ④ 语义层与人工环节（可选）
python eval/judge.py --arm a2 --tag real --sample 20
python eval/manual_sheet.py --arm a2 --tag real
python eval/manual_sheet.py --stats eval/out/sheet_a2_real.csv
```

本机也可以用 Ollama 上的本地模型跑（不花钱、不出网）：

```bash
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_API_KEY=ollama        # 占位，Ollama 不校验
export LLM_MODEL="modelscope.cn/Qwen/Qwen2.5-3B-Instruct-GGUF:latest"
python eval/run_eval.py --tag qwen3b
```

> ⚠️ 本地 3B 模型的分数**只说明链路通**，不能代表目标模型的效果，更不能写进论文当结论。

## 五条臂

| 臂 | 名字 | 工具集 | 跑哪些组 |
|---|---|---|---|
| `a0` | 纯 LLM（下界） | 无 | A–E, G |
| `a1` | 纯 RAG 固定流水线（最强基线） | 无（一次性预检索） | A–E, G |
| `a2` | 完整智能体 | core | A–E, G |
| `b0` | 调研单轮直出 | 无（一次性预检索） | F |
| `b1` | 完整调研 | core + research | F |

## 常用参数

```bash
python eval/run_eval.py --arms a2 --groups A,D --limit 5   # 冒烟测试
python eval/run_eval.py --qids A-01,G-03                   # 只跑指定题
python eval/run_eval.py --tag v1 --runs 3                  # 重复 3 次（算 pass^k）
python eval/run_eval.py --verbose                          # 打印每步工具调用
python eval/report.py --tag v1                             # 只报告该批次
```

## 结果文件

| 文件 | 内容 |
|---|---|
| `out/<arm>[_tag].jsonl` | 逐题记录：回答、工具调用、检索到的 chunk、各指标、判分理由 |
| `out/report.html` | 可视化仪表盘（自包含，离线可看，可直接打印成 PDF） |
| `out/report.md` | 同样的表用 Markdown 输出，方便贴进论文 |
| `out/sheet_<arm>.csv` | 人工判分表（生成后交给同学判） |
| `out/judged_<arm>.jsonl` | LLM 判官的逐题判定 |

## 加题 / 改判分规则

只改 `questions.jsonl`，**不用改代码**。每行一道题：

```json
{"qid":"A-01","group":"A","category":"T1","turns":["问题..."],
 "gold_ids":["kb001"],"gold_points":["要点1","要点2"],
 "answerable":1,"expect":"answer","must_contain":["关键词"],"must_not_contain":[],
 "expect_tools":["search_course_kb"],"note":"备注"}
```

- `turns` 多条 = 多轮题（按组计分，看最后一轮）
- `expect` 三选一：`answer`（要答对）｜`refuse`（要拒答）｜`correct_false_premise`（要纠正错误前提）
- `must_contain` / `must_not_contain` 是**规则判分**的依据，写窄了会低估系统，写宽了会放水

## 三条纪律

1. **跑之前先 `git commit`** —— 代码、题库、配置三者版本要对得上，否则结果不可复现。
2. **mock 的结果不能当结论** —— 报告顶部会自动打警示条。
3. **判官分数要人工校准** —— `judge.py --kappa`，kappa < 0.6 就不许写进论文。
