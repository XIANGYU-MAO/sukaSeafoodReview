# 实施计划执行顺序

当前采集与导入的权威设计是 [`2026-08-27-dynamic-collector-admin-integration-design.md`](../specs/2026-08-27-dynamic-collector-admin-integration-design.md)。它取代当前运行、部署和根 README 中关于固定五种鱼、固定行数和旧桌面采集目录的假设；历史计划仍保留其当时的记录价值。

本规格拆成三份历史计划，按以下顺序执行：

1. 2026-08-26-collaborative-review-core.md
   - 先执行 Task 1 至 Task 8，完成 API、数据库、共享待审核池、管理和增量清单接口。
   - 再执行 Task 9 至 Task 13，完成登录、审核、进度、历史和 Mao 中文后台。
2. 2026-08-26-local-training-sync.md
   - 依赖核心计划 Task 8 的 CSV 与回执契约。
   - 完成 Windows 本地训练集同步和可执行文件。
3. 2026-08-26-production-deployment.md
   - 依赖前两份计划全部通过。
   - 先部署独立审核服务，再修改 YGF Caddy、删除旧 /project，最后按当前采集与导入流程验收。

每个 Task 必须按“失败测试 → 最小实现 → 通过测试 → 提交”的顺序执行。生产部署前必须重新运行两套代码的全部测试和 Compose 配置验证。
