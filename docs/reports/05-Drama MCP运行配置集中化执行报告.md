# 05-Drama MCP运行配置集中化执行报告

执行日期：2026-08-13

## 结论

Drama MCP Server 已在 Plugin 初始化前自动加载根目录私有 `.env`，并保持系统/进程环境变量优先。MCP 已使用整理后的配置重启，initialize、工具发现和 Plugin→Java Bearer 只读调用均通过。

配置集中化本身已经完成。真实验证同时发现 Plugin 现有 Windows `file:///D:/...` 路径归一化问题；按本任务边界未修改 Media 业务代码，因此 Batch 04.1 暂不建议开始。

## 执行结果

| # | 检查项 | 结果 |
|---|---|---|
| 1 | MCP Server 是否支持 `.env` 自动加载 | 是。`Settings.from_environment()` 先调用 `python-dotenv` 加载。 |
| 2 | `.env` 位于哪里 | `drama-mcp-service/.env`。 |
| 3 | `.env` 是否被 Git ignore | 是，`git check-ignore .env` 已确认。 |
| 4 | `.env.example` 是否为无敏感值模板 | 是，三个 Token 均为占位符。 |
| 5 | `DRAMA_PLUGIN_*` 是否集中进入 MCP runtime `.env` | 是，Provider mode、三组 Service URL/Token 和 allowed roots 已集中。 |
| 6 | Java 自身配置是否未混入 MCP `.env` | 是；无 `DRAMA_TOOL_SECRET`、Storage、MySQL/DB 配置。 |
| 7 | shell/system env 是否可覆盖 `.env` | 是，使用 `override=False`；单测已覆盖。 |
| 8 | Plugin Core 是否无 `.env`/MCP 耦合 | 是，`drama-plugin` 零变更。 |
| 9 | 三个 API Token 是否均能被 Plugin 读取 | 是，三项均为 `configured = YES`，且值一致。 |
| 10 | Token 是否未进入日志或报告 | 是，验证仅输出是否配置，不输出值。 |
| 11 | `D:\home\AI` 是否成为 allowed root | 是，runtime 读取结果为 `D:\home\AI`。 |
| 12 | `file:///D:/home/AI/test.png` 是否通过安全检查 | 否。现有 Plugin 将 URI 路径解析为驱动器相对路径并返回 `INVALID_ARGUMENT`；本任务未越界修复。 |
| 13 | MCP initialize 是否 PASS | PASS。Server name 为 `drama-mcp-service`。 |
| 14 | `media.import_media` / `media.resolve_media` 是否可发现 | PASS，两项均存在。 |
| 15 | Plugin→Java Bearer 认证是否真实 PASS | PASS。无认证直连返回 401；同一 Java 服务上的 `work.list_works` 经 MCP/Plugin 调用成功。 |
| 16 | Tool Contract 是否零变更 | 是。 |
| 17 | MCP Tool 数量是否零变化 | 是，基线与实测均为 44。 |
| 18 | Java 业务源码是否零修改 | 是。本任务未修改 Java 源码；用户原有 `application.yml` 工作区改动保持不动。 |
| 19 | 是否可重新开始 Batch 04.1 Media 真实 MinIO E2E | 暂不建议；需在独立 Media 任务修复第 12 项后再开始。 |

## 最小验证

- `python -m pytest -ra`：13 passed。
- MCP 健康检查：HTTP 200，监听 `127.0.0.1:8765`。
- Provider mode：Memory/Asset/Media=`http`，Research/Production=`mock`，Context=`local`。
- Memory/Asset/Media base URL：均已配置。
- Memory/Asset/Media API Token：均为 `configured = YES`。
- `integration/verify_runtime_config.py`：initialize、44 个工具、两个 Media 工具发现、Bearer 只读调用均 PASS；路径安全检查按上表记录为 FAIL。

## 变更边界

本次只修改 MCP Host 的配置加载、模板、说明和验证代码；没有修改 ToolRegistry、Tool Contract、Skill、HTTP endpoint、Java 认证逻辑或 Media Provider 业务语义。
