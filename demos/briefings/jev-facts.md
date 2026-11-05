# Jev brief — facts and sources (researched 2026-11-05)

Every number in `jev-brief.yaml` comes from this list. "Vendor" = TypeSafe AI's own claim;
"reported" = only one secondary source.

| Fact | Source |
|---|---|
| Jev = TypeSafe AI's first "System One" model; typed decisions, not text | em360, olostep, mayhemcode |
| Early access 15–16 Sept 2026; on Vercel AI Gateway 16 Sept 2026 | olostep, Vercel changelog |
| Founded 2024, San Francisco; CEO is a former OpenAI researcher (name spelled differently across sources, so omitted) | olostep, mindstudio |
| $40M seed led by DCVC (reported) | olostep |
| Question types: Choice, Score, yes/no ("Noul"); questions in one call run in parallel; Choice ≤ 255 options | mayhemcode, fireup |
| Output = allowed value + probability + confidence; trained with RLCD | mayhemcode, em360 |
| Latency 70–500 ms depending on load; ~64k context, 32k state cap | mayhemcode |
| $0.042 per million input tokens ($42/billion); output free | mayhemcode, mindstudio, olostep |
| Up to 193.6× faster, 444.6× cheaper on TypeSafe's workflow evals (vendor) | Vercel changelog |
| LangChain: classification step 5–6× faster than Sonnet; Stagehand decision step 1.97 s → 0.46 s (4.3×) | fireup |
| ~13% of Vercel paid teams used it within 24 h (reported) | mayhemcode |
| LangChain published two guides within a week | fireup |
| 2,170 public GitHub projects (Sept 2026 study) | em360 |
| Available via Vercel AI Gateway, Cloudflare Workers AI (typesafe/jev), Opper | mayhemcode |
| Benchmark 29 Sept: 37 datasets, 346,009 requests, < $10, 95–99% on several | em360 |
| Weak: low-resource languages, noisy/fine-grained labels, rubric scoring, arithmetic & dates, multi-document (invoice matching) | em360, fireup |
| Demos only in simulators/games; no real hardware | mindstudio, mayhemcode |
| Sync only: no streaming, no tool calls; SDK API is `experimental` | mayhemcode, fireup |
| Text-only input; closed commercial API, no open weights | olostep, em360 |
| Open alternative: Laya (Apache 2.0) | em360 |
| AI SDK example (`experimental_evaluate`, model `typesafe-ai/jev`, AI SDK ≥ 7.0.105) | Vercel changelog |

Sources:
- https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway
- https://www.mayhemcode.com/2026/09/what-is-jev-ai-and-how-is-it-different.html
- https://www.mindstudio.ai/blog/jev-system-one-model-launch
- https://www.olostep.com/blog/jev-ai
- https://fireup.pro/news/what-is-jev-how-system-one-models-could-change-ai-agents
- https://em360tech.com/tech-articles/what-is-jev-ai
