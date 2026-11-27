# Provenance, Licensing Boundaries, and Known Limitations

## 1. Independent Implementation & Provenance

- 本專案（`group-collaboration`）所有 Python 模組（`groupctl.py`、`group_*.py`）、測試套件（`tests/test_*.py`）、以及 AI 代理指引（`AGENTS.md`、`AGENT_COLLABORATION.md`、`.agents/skills/group-collaboration/SKILL.md`）皆依課程共同規格 `specs/00–07`（Protocol 0.1）自行設計與實作。
- 開發過程僅讀取課程參考資料的官方規格與設計檢討，**未複製任何無授權或權利未定之他組原始碼**。
- `specs/00_group_collaboration_assignment.md` 至 `specs/07_testing_and_finalization.md` 為原始課程規格文件，完整保留未修改。

## 2. Git `update-ref` Transaction Constraint (`int --commit-merge`)

- 經實際在 `files` 與 `reftable` 兩種 Git ref backend 驗證（Windows Git 2.56.1 與 macOS Git 2.50.1），Git 上游 `refs.c` 交易引擎禁止在單一 `update-ref --stdin` 交易內同時鎖定符號參照 `HEAD`（含 `symref-verify`）與其指向的目標分支 `refs/heads/<target>`（回傳 `multiple updates for 'HEAD' (including one via its referent ...) are not allowed`）。
- 因此 `group_int_finish.py` 的 `--commit-merge` 採取多層防護：
  1. 獨占持有 `groupctl-int.lock` 與 Git 標準 `index.lock`（凍結 index 並阻擋一般 Git 寫入）；
  2. 嚴格比對操作者審閱之 `staged_tree_oid`（`--expected-tree`）、`HEAD == T` 與 `MERGE_HEAD == A`；
  3. 透過 `commit-tree` 建立雙父 `[T, A]` 合併提交並驗證父鏈與 tree；
  4. 更新前重驗 `HEAD` symref，執行單一目標分支原子 CAS（`git update-ref --no-deref <target> <M> <T>`），更新後立刻重驗 `(symbolic-ref HEAD, rev-parse HEAD) == (target, M)`；
  5. 任何寫入後異常均停在 `write_unverified`，保留現場，絕不自動 `reset` 或回滾。

## 3. Protocol 0.1 & Validation Boundaries

- **Attestation（宣誓）而非密碼學身分認證**：Protocol 0.1 的 `.group/members/<person_id>.json`、`--diff-inspected`、`semantic_review` 與 `FINAL` 確認檔均屬結構化宣誓與雜湊綁定，無法密碼學證明自然人身分或主觀語意判斷品質。
- **隔離 Worktree 不是 OS 安全沙箱**：`int --verify`、`test --isolated` 與 `final --prepare` 使用暫存 detached worktree 避免污染使用者工作樹，但測試程式碼仍以目前作業系統帳號權限執行，應只對可信任的團隊程式碼執行。
- **模擬測試標記（`simulation`）**：自動化 E2E 測試（含 3-clone 15-step 驗收）使用本機 `file://` bare remote 並明確標記 `evidence_mode: "simulation"`，不得當成真實組員的正式結案核准。
- **並發與外部寫入**：工具鎖僅協調 `groupctl` 寫入流程；執行 `PUB`／`INT`／`FINAL` 寫入期間應暫停同一工作目錄上的外部 IDE 或手動 Git 寫入。
