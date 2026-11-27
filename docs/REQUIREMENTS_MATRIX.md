# Requirements Traceability Matrix — Group Collaboration System (Protocol 0.1)

本表對照官方規格 `specs/00_group_collaboration_assignment.md` 至 `specs/07_testing_and_finalization.md`，記錄本專案（`groupctl.py`）的實作對應與自動化測試覆蓋。

| 需求 ID | 規格條文 | 需求摘要 | 實作模組 / CLI | 自動化測試 | 狀態 |
|---|---|---|---|---|---|
| **R-CORE-1** | `02 §2–5` | `.group/` 目錄契約、`group.json`、成員檔、作業檔、跨 repo `--repo` 定位 | `groupctl.py` (`--repo`, `--version`, `config show`), `group_status.py` | `tests/test_groupctl.py`, `tests/test_reflections_hardening.py` | ✅ 完成 |
| **R-CORE-2** | `02 §8–9`, `03 §1–14` | 10 種不可變 JSON 事件驗證、獨占寫入、關聯驗證、安全發布 | `group_events.py`, `group_event_publish.py`, `groupctl.py` (`events ...`) | `tests/test_events.py`, `tests/test_reflections_hardening.py` | ✅ 完成 |
| **R-SYNC-1** | `04`, `05 §1` | `SYNC`：記錄本機狀態、fetch 遠端分支事件、比對 canonical、保護 dirty、安全更新與快速驗證 | `group_sync.py` (`sync --discover`), `group_update.py` (`sync --update`) | `tests/test_sync.py`, `tests/test_update.py` | ✅ 完成 |
| **R-PUB-1** | `04`, `05 §2`, `01 §4` | `PUB`：唯讀預檢、隔離 index 提交、Work Push、事件 Commit、Event Push 與遠端獨立驗證 | `group_pub.py`, `group_pub_commit.py`, `group_pub_push.py`, `group_pub_event.py`, `group_pub_event_push.py` | `tests/test_pub*.py`, `tests/test_pub_e2e.py` | ✅ 完成 |
| **R-INT-1** | `04`, `05 §3 (1–8)`, `01 §6` | `INT` 預檢、FF、非 FF Merge 準備、衝突診斷與保留現場、放行安全 `sync-discover.json` 快取 | `group_int.py` (`int --preflight/--ff-only/--prepare-merge/--inspect-merge`) | `tests/test_int.py`, `tests/test_pub_int_e2e.py`, `tests/test_reflections_hardening.py` | ✅ 完成 |
| **R-INT-2** | `05 §3 (9–15)`, `01 §5` | `INT` 提交（staged tree OID 綁定）、Level 1/2 隔離驗證、顯式語意審查與 `integration_result` 發布 | `group_int_finish.py` (`int --commit-merge/--verify/--publish-result`) | `tests/test_int_complete.py`, `tests/test_three_member_e2e.py` | ✅ 完成 |
| **R-REV-1** | `04`, `05 §4`, `03 §8` | `REV`：檢視真實 diff 與介面宣告、4 種 verdict、`review` 事件發布、絕不自動合併 | `group_rev.py` (`rev --inspect/--publish`) | `tests/test_rev.py`, `tests/test_three_member_e2e.py` | ✅ 完成 |
| **R-TEST-1** | `04`, `05 §5`, `07 §1–6` | `TEST`：`current`/`main`/`<change_id>` 多 scope、隔離 worktree、`--human-check`、不假報通過、發布驗證 | `group_test.py` (`test --scope/--isolated/--human-check/--publish`) | `tests/test_test.py`, `tests/test_test_scopes.py`, `tests/test_reflections_hardening.py` | ✅ 完成 |
| **R-STAT-1** | `04`, `05 §6` | `STATUS`：本機狀態、事件與可歸屬測試分析、可選 `--fetch` 遠端新鮮度與待整合提案、下一步建議 | `group_status.py` (`status [--fetch]`) | `tests/test_status.py`, `tests/test_test_scopes.py`, `tests/test_reflections_hardening.py` | ✅ 完成 |
| **R-FIN-1** | `04`, `05 §7`, `07 §7–11` | `FINAL`：任一成員觸發、canonical 檢查、隔離最終測試、候選報告與 hash、確認後漂移防呆、已解 blocker 辨識、真人確認發布 | `group_final.py` (`final --prepare/--publish`) | `tests/test_final.py`, `tests/test_reflections_hardening.py`, `tests/test_three_member_e2e.py` | ✅ 完成 |
| **R-AGNT-1** | `00 §3(2)`, `06 §1–11` | 供應商中立可攜 AI Agent 指引與 Skill（Codex / Gemini / Claude / Antigravity 通用） | `AGENTS.md`, `AGENT_COLLABORATION.md`, `.agents/skills/group-collaboration/SKILL.md` | 檔案契約與指令對應檢核 | ✅ 完成 |
| **R-DEMO-1** | `00 §7`, `07` | 3 位成員、獨立分支與獨立 clone 之 15-step 端到端驗收（含無衝突與衝突解決）與 fresh clone 恢復 | `tests/test_three_member_e2e.py` | `tests/test_three_member_e2e.py` | ✅ 完成 |
