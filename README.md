# Group Collaboration System

完整支援 Protocol 0.1（`specs/00–07`）七大協作指令（`SYNC`、`PUB`、`INT`、`REV`、`TEST`、`STATUS`、`FINAL`）、不可變事件發布、可攜式 AI Agent 指引（[AGENTS.md](AGENTS.md)、[AGENT_COLLABORATION.md](AGENT_COLLABORATION.md)、[.agents/skills/group-collaboration/SKILL.md](.agents/skills/group-collaboration/SKILL.md)）與三人獨立 clone 15-step 端到端驗收（`tests/test_three_member_e2e.py`）。

## 完整協作流程新增命令（INT 完成、REV、TEST 多 Scope、FINAL、通用事件發布）

- **INT 提交、驗證與結果發布：**
  - `int --inspect-merge` 於衝突解除後輸出 `staged_tree_oid`（以隔離暫存 index/objects 唯讀計算，不改動原工作樹）。
  - `int --commit-merge` 綁定完整審閱證據與 `--expected-tree`、`--message`，持有 `groupctl-int.lock` 與 `index.lock`、透過 `commit-tree` 建立雙父 `[T, A]` 合併提交、執行目標分支單一 ref 原子 CAS（`update-ref --no-deref`）與前後 `HEAD` symref 雙重驗證，並清理 `MERGE_HEAD` 等待提交狀態（註：Git 上游 `refs.c` 禁止單一交易同時鎖定 `HEAD` 與其指向的目標分支；任何寫入異常皆保留 `write_unverified`，絕不自動 reset）。
  - `int --verify` 對確切 `--merge-commit` 執行 Level 1（Git ancestry、衝突標記掃描）與 Level 2（隔離 detached worktree unittest），並明確標示 `semantic_review: not_performed`。
  - `int --publish-result` 要求明確提供 `--build`、`--tests`、`--semantic-review`（不預設 `pass`），禁止在缺測試或語意失敗時回報 `result=success`，並建立／推送且獨立 bare-fetch 驗證 `integration_result` 事件。
- **REV 審查（絕不自動合併）：**
  - `groupctl.py rev --inspect --person-id <id> --change-id <C-ID>` 唯讀顯示事件宣告與真實 Git diff。
  - `groupctl.py rev --publish --person-id <id> --change-id <C-ID> --inspected-commit <SHA> --diff-inspected --verdict <accept|accept_with_changes|reject|needs_more_evidence> --summary "..." [--remote origin --expected-url <URL> --confirm-reviewed]` 發布 `review` 事件，不合併程式碼。
- **TEST 多 Scope 隔離驗證與發布：**
  - `groupctl.py test --person-id <id> [--scope current|main|<C-ID>] [--isolated] [--publish --remote origin --expected-url <URL> --confirm-reviewed]` 於隔離 detached worktree 執行 unittest，不污染使用者工作目錄。
- **FINAL 群組最終化（真人確認綁定）：**
  - `groupctl.py final --prepare --person-id <id> --result-summary "..." [--disposition C-ID=reason] [--limitation "..."] [--simulation]` 驗證 canonical 狀態、列舉未整合提案與 blockers、於隔離 worktree 跑最終測試，產出 `.group/final/candidate.md`、`report_sha256` 與 `final_candidate` 事件。
  - `groupctl.py final --publish --person-id <id> --candidate-id <E-ID> --confirmation-file <path> --confirm-human [--allow-known-limitations] [--remote origin --expected-url <URL>]` 驗證候選 ID、canonical commit 與報告 SHA-256 綁定後，產出 `.group/final/final_report.md`、`.group/final/final.json` 與 `final_result` 事件並推送驗證。自動化測試與 Demo 確認一律標記 `evidence_mode: "simulation"`，不冒充真人批准。
- **通用事件發布與 STATUS 下一步建議：**
  - `groupctl.py events publish <event.json> --person-id <id> [--remote origin --expected-url <URL> --confirm-reviewed]` 可安全提交並推送驗證 `finding`、`question`、`answer`、`blocker` 等事件。
  - `groupctl.py status` 新增 `Recommended next action:` 依 dirty、blocker、behind 與 HEAD 測試證據給出下一步建議。

## 步驟 3-9C-4：INT 唯讀衝突診斷與重查

在待提交 Merge 的目標個人分支上執行。只提供身分、change ID 和目標分支時，
工具會列出 Git 現場，並回報 `evidence_status: insufficient_evidence`。

```powershell
python -B groupctl.py int --inspect-merge --person-id student01 --change-id C-17 `
  --target-branch people/student01/my-topic
```

要核對先前審閱的候選，請帶入同一次 `--prepare-merge` 的原始參數和保存的
JSON 診斷所對應的完整證據。未提議整合的事件另需 `--confirm-unproposed`。

```powershell
python -B groupctl.py int --inspect-merge --person-id student01 --change-id C-17 `
  --target-branch people/student01/my-topic --expected-head $targetSha --work-sha $workSha `
  --expected-event-id $eventId --expected-event-blob $eventBlobSha `
  --expected-source-ref $sourceRef --expected-source-tip $sourceTipSha `
  --expected-incoming ($reviewedCommits -join ',') --confirm-reviewed
```

JSON 的 `git_state.conflicts` 依路徑列出 index stages、mode 和 object ID。
Stage 1 是共同基底，2 是目標方，3 是來源方。新增或刪除衝突可能缺少某些 stage。
人工審閱兩側內容，逐檔解決並用 `git add -- <path>` 暫存，再重跑同一條命令。
用 `git diff --cached --no-ext-diff --no-textconv` 審閱完整暫存結果。
暫存差異的衝突標記及空白檢查是輔助診斷，未合併 stages 決定 Git 是否仍有衝突。

| 狀態 | 意義 | 退出碼 |
| --- | --- | --- |
| `conflicted` | 仍有 unmerged stages，事件審閱結果另看 `evidence_status` | 1 |
| `blocked` | 證據、目標、工作樹或現場一致性檢查有阻礙 | 1 |
| `awaiting_human_confirmation` | 審閱證據相符，Git 衝突已解除，暫存結果等待人工確認 | 0 |
| `no_pending_merge` | 現場沒有待提交 Merge，原因未確認 | 1 |

部分證據或無效的命令列參數回傳 2。`reviewed_evidence_matches` 表示目前 Git 現場
與提供的證據相符。若原始資料遺失，仍可診斷，但相同的 HEAD、MERGE_HEAD
也可能來自另一次 Merge，無法由它們追溯原始事件或審閱紀錄。來源 ref 使用
本機 tracking 資料，遠端新鮮度未知。檢查期間請暫停外部 Git、IDE 及其他寫入。
這是唯讀操作。即使退出碼為 0，也仍待人工決定是否提交，且尚待建置、測試與語意審查。

## 步驟 3-9C-3：INT 準備非 Fast-forward Merge

先用 `int --preflight` 查證候選，再依下方 Fast-forward 範例核對相同的審閱資料。
目標 T 與候選 A 必須分歧且只有一個可驗證的共同祖先。

```powershell
python -B groupctl.py int --prepare-merge --person-id student01 --change-id C-17 `
  --target-branch people/student01/my-topic --expected-head $targetSha --work-sha $workSha `
  --expected-event-id $eventId --expected-event-blob $eventBlobSha `
  --expected-source-ref $sourceRef --expected-source-tip $sourceTipSha `
  --expected-incoming ($reviewedCommits -join ',') --confirm-reviewed
```

INT 模式互斥。若 T 是 A 的祖先，使用 `--ff-only`。若 A 已在 T 的歷史中，
回傳 `already_in_history`，退出碼為 0。Git 的非 Fast-forward Merge 可能覆寫 ignored
檔案，因此準備前也會拒絕 ignored 的未追蹤路徑，包括工作目錄裡的 `.group/local/` 資料。
INT 互斥鎖只協調本工具，執行期間須暫停外部 Git 與 IDE 寫入。

| 狀態 | 現場 | 退出碼 |
| --- | --- | --- |
| `pending_commit` | HEAD 與所有 refs 維持原值，MERGE_HEAD 指向 A，index 保留待審閱結果 | 0 |
| `conflicted` | 保留 MERGE_HEAD、index stages、工作樹和 Git 診斷，輸出列出未合併路徑 | 1 |
| `blocked` | 寫入前的安全檢查未通過 | 1 |
| `write_unverified` | 已嘗試 Git 寫入，現場保留供查驗 | 1 |

用 `git status` 和 `git diff --cached --no-ext-diff --no-textconv` 審閱待提交結果。
Merge Commit 由操作者另行決定。準備模式僅驗證本機 Git 狀態，建置、測試、
語意審查及整合事件發布屬於後續步驟。

## 步驟 3-9C-2：INT 本機 Fast-forward

先執行下方 `int --preflight`，核對事件、實際 diff、介面與
`incoming_commits` 列出的全部 `T..A` 工作歷史。確認操作者獨占使用此工作目錄
及目標 ref 後，將審閱過的值帶入 PowerShell 指令。

```powershell
python -B groupctl.py int --ff-only --person-id student01 --change-id C-17 `
  --target-branch people/student01/my-topic --expected-head $targetSha --work-sha $workSha `
  --expected-event-id $eventId --expected-event-blob $eventBlobSha `
  --expected-source-ref $sourceRef --expected-source-tip $sourceTipSha `
  --expected-incoming ($reviewedCommits -join ',') --confirm-reviewed
```

`$targetSha` 和 `$workSha` 分別取自 `target_sha` 和 `candidate_work_sha`。
事件路徑由事件 ID 推導為 `.group/events/<event_id>.json`，來源 ref 與 tip 取自
`ancestry_evidence` 的 `source_ref` 及 `source_tip_sha`，該 tip 必須含有審閱的事件 blob。
提交清單以逗號分隔完整 SHA，
順序不限，空範圍以 `none` 表示。若事件未提議整合，須額外提供 `--confirm-unproposed`。
PUB 的事件提交及來源 tip 只供查證，Git 的候選參數使用工作 SHA。

寫入前取得 Git 目錄中的 INT 專用互斥鎖，重新檢查候選、全部 refs、目標及工作目錄。
鎖只協調本工具的 INT 寫入，外部 Git、IDE 和其他命令仍須停止寫入。中斷後若鎖仍存在，
工具會阻擋，保留現場供操作者查驗。支援已開啟且屬於操作者的個人分支，拒絕 canonical、
main、linked worktree、變髒或有未完成操作的工作目錄，以及不支援的 Git 設定與歷史。
已知目標落後或分歧於本機 tracking ref 時，先處理同步。INT 使用本機 tracking refs，
其資料可能過期。命令不 fetch，也不切換分支。

成功狀態 `local_fast_forward_verified` 表示已核對目標精確推進至 A、index 與工作樹
乾淨、沒有待合併或衝突狀態，且其他 refs 維持原值。`already_in_history` 表示 A 已位於
目標歷史，包含以原始審閱資料重試且目標已精確停在 A 的情況。兩者退出碼皆為 0。
`blocked` 表示拒絕操作。`write_unverified` 表示已嘗試 Git 寫入但命令或事後驗證
失敗，須依輸出的現場證據查驗。兩者退出碼皆為 1，參數錯誤為 2。忽略檔案碰撞會
阻擋 FF。Git 嘗試操作時可能更新 `ORIG_HEAD`，工具保留這類現場資訊。以上狀態只涵蓋本機 Git，建置、測試、語意審查及遠端發布
未由這個模式執行。

## 步驟 3-9B：INT 候選查證與安全預檢

```powershell
python -B groupctl.py int --preflight --person-id student01 --change-id C-17 --target-branch people/student01/my-topic
```

指定已登錄的操作者、change ID 及完整本機分支名稱。預檢只讀取本機 Git 資料，從
canonical 和 `people/*` 的 `origin` tracking ref tip 掃描事件，包含先前已讀的事件。
若需刷新遠端資料，請另行執行 `sync --discover`。tracking refs 可能過期。

以事件 ID、路徑與 blob 核對來源，相同事件跨 ref 時保留各來源證據，同一 change 有矛盾
或同一事件 ID 有不同 blob 時拒絕選取。宣告的工作 SHA 必須唯一解析為 Git Commit，
並且可從宣告的個人來源分支 tip 到達。JSON 保留來源 ref、tip SHA、事件 ID、路徑、
blob SHA、目標 SHA 及祖先關係。多 Commit 貢獻的 `files_changed` 仍須依實際範圍審閱。

目標須為操作者的個人分支或配置的 canonical 分支，且已在目前工作目錄開啟，預檢
才會判定目標可供下一步審閱。若目標存在但目前工作目錄在其他分支，仍可回報目標
SHA 和祖先證據，工作樹狀態只反映目前工作目錄。

| `status` | 意義 | 退出碼 |
| --- | --- | --- |
| `review_required` | Git 歷史尚未包含候選，可開始審閱實際 diff、介面、假設與驗證計畫 | 0 |
| `history_contains_candidate` | Git 歷史已包含候選，語意與目前行為仍須核對 | 0 |
| `insufficient_evidence` | 來源、事件或 Git 物件證據不足 | 1 |
| `candidate_conflict` | 候選有矛盾，需要操作者確認 | 1 |
| `target_unsafe` | 目標或工作目錄狀態阻擋下一步 | 1 |

參數錯誤的退出碼為 2。預檢不 fetch、不切換分支、不合併或寫入事件。

## 步驟 3-8D-2：PUB 事件 Push 與遠端驗證

先以 `--push-work` 推送工作 Commit A，再用 `--event-commit` 建立本機事件 Commit E。
檢閱 E 的事件 JSON、A 與 E 的完整 SHA，以及目標個人分支和遠端 URL 後，使用
`--confirm-reviewed` 授權推送。第一次執行時，`--expected-remote-head` 填 A。

```powershell
python -B groupctl.py pub --push-event --person-id student01 --expected-branch people/student01/my-topic --work-sha $workSha --expected-head $eventSha --change-id $changeId --remote origin --expected-url $reviewedUrl --expected-remote-head $workSha --confirm-reviewed
```

工具重新核對本機 HEAD、群組與成員資料、分支歸屬、遠端 URL 和 Git 操作狀態。
E 必須以 A 為唯一父 Commit，tree 只能新增一個符合事件 schema 與指定參數的
`change_published` JSON。工具也會重新核對事件的 `files_changed` 是否與 A 對其唯一父
Commit 的實際變更一致，包含人工建立或恢復的 E。工具以暫存 Git repository 讀取遠端可見分支 tip 上的事件，
檢查事件 ID、change ID 和分支與工作 SHA 的組合是否重複。目標分支須仍指向 A，
才會嘗試以一般 fast-forward Push 將單一 ref 從 A 更新為 E。

Push 前使用即時查詢，傳送時以 pre-push guard 確認伺服器公告的舊 SHA 和目標 ref，
伺服器更新 ref 時再比對舊 SHA。Push 後工具以 `git ls-remote` 查詢指定分支，並在另一個
全新的暫存 bare repository fetch 該分支。驗證會讀取遠端傳回的 E，確認父 Commit、tree
差異、事件路徑、blob SHA 和 JSON 位元組皆符合本機已檢閱的 E，最後再查一次分支 tip。
驗證結果只反映觀察期間的狀態。分支之後仍可能移動，同一 SHA 中途被移走再改回也可能
無法察覺。重複檢查的範圍是該端點對目前憑證公開的分支 tip，不涵蓋隱藏或已刪除的 ref、
僅存於歷史或 tag 的事件、其他遠端，或掃描後在另一分支同時發布的事件。

遇到中斷或未能完成驗證，可用同一份 A、E 和 change ID 重試。若已確認遠端 tip 是 E，
`--expected-remote-head` 也可填 E。遠端已有 E 時，工具會重新查詢並 fetch 驗證，無須再 Push。
遠端 tip 若是其他 SHA，請先檢查實際分支狀態。此操作的發布範圍是指定個人分支上的事件。
整合至 main、REV 和 FINAL 各有自己的流程與證據。

| `result` | 意義 | 退出碼 |
| --- | --- | --- |
| `not_executed` | 前置檢查未通過，未執行 Push | 1 |
| `push_rejected` | Git 或傳送防護拒絕更新 | 1 |
| `verification_failed` | 遠端可能已有 E，查詢、fetch 或內容驗證未完成 | 1 |
| `already_published` | 遠端已有 E，且獨立驗證成功 | 0 |
| `published_verified` | Push 成功，且獨立驗證 E 與事件成功 | 0 |
| `partial` | Push 結果未能確認，後續查詢當時也未觀察到 E | 1 |

參數錯誤的退出碼為 2。JSON 結果會記錄 `event_id`、事件路徑與 blob SHA（若已讀取）、
已觀察的遠端 tip，以及 Git Push 退出碼（若可取得）。

## 步驟 3-8D-1：PUB 本機事件 Commit

工作 Commit A 經 3-8C-2 推送並驗證後，審閱 A 相對其唯一父 Commit 的 diff 與實際
測試證據。準備 UTF-8 JSON 檔，提供以下六個欄位。以下是只改動單一文字檔的測試
情境。請依實際審閱填寫各欄位，並在 `tests_run` 記錄確實執行的檢查。

```json
{
  "summary": "Update the tracked fixture text.",
  "files_changed": ["tracked.txt"],
  "interface_changes": [],
  "tests_run": [],
  "known_issues": [],
  "proposed_for_integration": false
}
```

```powershell
python -B groupctl.py pub --event-commit --person-id student01 --expected-branch people/student01/my-topic --work-sha $workSha --expected-head $workSha --remote origin --expected-url https://github.com/pigjack52/group-collaboration.git --change-id $changeId --review-file $reviewFile --confirm-reviewed
```

`$workSha` 是 A 的完整 SHA，`$changeId` 是為這項工作取用且保留供重試的唯一識別碼
（例如以 `C-` 加 UUID 產生），`$reviewFile` 是已審閱的 JSON 路徑。操作者以
`--confirm-reviewed` 確認審閱內容、身分及目的地，並授權寫入本機事件和 Commit。
工具會核對 `files_changed` 與 A 的 Git diff，其他工程陳述由審閱者確認。

本機個人分支 HEAD 與指定遠端的即時分支 tip 都須等於 A。暫存區須無變更，未選取的
未暫存修改及未追蹤檔案可保留。工具以排他方式建立 `.group/events/<event_id>.json`，
並以隔離 index 驗證候選 tree 僅新增該事件檔。更新前後會檢查目前分支，且 Git
僅在指定個人分支的舊 tip 仍為 A 時更新它。執行期間請避免同時切換分支或編輯
事件目錄。事件的 `commit` 欄位指向 A，事件 Commit E 的唯一父 Commit 也是 A。

| `result` | 意義 | 退出碼 |
| --- | --- | --- |
| `event_validation_failed` | 前置驗證失敗，未寫入事件 | 1 |
| `event_creation_failed` | 寫入事件失敗，請依事件路徑與診斷核查磁碟狀態 | 1 |
| `partial` | 事件已寫入，後續 Commit 或驗證未完整完成。依 `phase` 核查 ref 和 index | 1 |
| `local_event_committed` | E 已在本機建立並驗證，事件尚未發布 | 0 |

參數錯誤的退出碼為 2。遠端驗證只反映查詢當時的 A。事件須以 `--push-event` 推送且可由
共享遠端查得後才算發布。重試會透過工作樹及已知本機分支與遠端追蹤 ref 的 tip
檢查重複 change ID，以及同分支同工作 SHA 的既有事件。不同機器間的唯一性依靠
唯一 ID 分配及後續共享遠端檢查。

### PUB 中斷恢復

保留原審閱資料、完整 SHA、`change_id` 與命令回報。先用 `git symbolic-ref --short HEAD`、
`git rev-parse --verify HEAD`、`git status --short`、`git diff --cached` 和 `git diff`
確認本機分支、提交及 index。遠端以已審閱 URL 上的即時 `git ls-remote --symref` 查詢為準。
追蹤 ref 和 Push 退出碼可協助定位，但查詢失敗時遠端狀態仍未知。

| 中斷位置 | 可採信的證據 | 安全續作與人工介入條件 |
| --- | --- | --- |
| A 已建立、尚未 Push | 本機個人分支指向 A，A 的父 Commit、diff 和 index 符合審閱結果；即時查到原審閱的遠端 tip。 | 以 A 與已確認的遠端舊值執行 `--push-work`。需要重新確認本機 A 時可先用 `--use-existing`。若分支、內容、index 或遠端舊值有疑義，先由操作者核對。 |
| 工作 Push 可能成功、驗證失敗 | `verification_failed` 表示結果待查。即時查到 A 才能確認 A 已在目標分支。 | 同一份 `--push-work` 參數可重試。遠端為 A 時回 `already_present`；仍是原審閱舊值時可按原防護重試。其他 SHA 或持續查詢失敗時，先釐清狀態再繼續。 |
| 事件檔已建立、HEAD 仍為 A | 回報的 `event_path`、磁碟上的原檔及其 JSON、HEAD、index、即時遠端 tip。若有 `candidate_commit`，檢查其父 Commit 與 tree。 | 保留原檔及 ID。這時沒有可直接 Push 的 E；重跑 `--event-commit` 會擋下原檔。由人查明檔案是否完整、與原審閱一致、是否已有發布，並確認暫存區後，才以原事件完成本機提交。E 驗證後再走 `--push-event`。 |
| E 已建立、index 安裝失敗 | `phase=ref_updated`、本機 ref 指向 E，且 staged diff 與候選 tree 不一致。比較 `E^ = A`、E 的單一事件 blob、index 及工作樹。 | 保留 E 與本機內容，由人判定 staged 對應舊 index 或其他待保存變更，確認 lock 歸屬後再校正。核對完成後以原 E 續作 `--push-event`。 |
| E 完整建立、尚未 Push | 本機 HEAD 為 E，父 Commit 為 A，E 只新增原事件，index 已核對；即時遠端 tip 為 A。 | 以原 A、E 和 `change_id` 執行 `--push-event`，預期遠端為 A。遇到結構、身分、內容或 tip 不符時停止，先人工查明。 |
| 事件 Push 可能成功、驗證失敗 | `verification_failed` 或 `partial`；以即時 ref 和重新 fetch 的 E 與事件 blob 判斷。 | 保留原參數重試 `--push-event`。遠端為 E 時重新驗證；仍為 A 且原預期為 A 時走原防護。遠端已移動或內容持續無法驗證時，由人查明。 |
| 遠端已存在 E | 即時 tip 精確等於 E，且獨立 fetch 取得與本機已審閱 E 一致的事件。 | 原 `--push-event` 可用 A 或 E 作為預期遠端 tip，驗證成功回 `already_published`，不再 Push。若遠端已前進到其他 SHA，人工核對 E 的可達性與原事件是否仍在 tip。 |

遇到來源未明的 lock、未完成的 Git 操作或無法辨認的 staged 內容，停止寫入並先保存證據。
恢復期間保留原 Commit、原事件和其他本機變更，避免強制更新遠端或清除工作樹。

## 步驟 3-8C-2：PUB 工作 Commit Push

先審閱要公開的 Commit 範圍、個人分支和遠端 URL。以
`git ls-remote --symref origin refs/heads/people/student01/my-topic` 取得並核對遠端目前的
完整 SHA。查詢成功且確認分支不存在時，使用 `absent` 授權建立該個人分支。
`--confirm-reviewed` 表示操作者已審閱內容與目的地，並授權這次 Push。

```powershell
python -B groupctl.py pub --push-work --person-id student01 --expected-branch people/student01/my-topic --expected-head $workSha --remote origin --expected-url https://github.com/pigjack52/group-collaboration.git --expected-remote-head $remoteSha --confirm-reviewed
```

先將已審閱的完整工作 SHA 填入 `$workSha`，並將已審閱的完整遠端 SHA 或字串
`absent` 填入 `$remoteSha`。

遠端名稱必須各自解析到唯一且相同的 fetch 與 push URL，並與 `--expected-url` 完全一致。
若已提交的 `.group/group.json` 有 `repository`，也必須一致。支援無內嵌憑證的 HTTPS、
`ssh://` 與絕對本機路徑。Push 在暫存 Git repository 執行，使用 Git 的使用者及系統層
認證設定，或已配置的 SSH／認證代理。依賴來源 repository 專用認證設定、已啟用的
`pre-push` hook、淺層歷史或 graft 的工作流程需另行處理。工具會保留未提交的本機內容。

工具即時查詢目標分支，檢查遠端 tip 是否符合審閱值，並確認既有 tip 是工作 Commit
的祖先。若當地缺少遠端 tip 的歷史，先取得並審閱該歷史，再決定是否重試。
Push 前會檢查新增到目標分支的提交歷史。若其中有尚未發布的 `.group/events/` 變更，
即使最後的 tree 已移除該事件，Work Push 仍會拒絕，請使用對應的事件發布程序。
沿用已發布歷史中的事件可以繼續推送工作。其他分支上的既有歷史以遠端即時公布的
tip 與本機可驗證的 Git 物件為準。
Push 只送出指定 SHA 到指定個人分支的 refspec。Push 當下的 `pre-push` guard 會核對
Git 收到的目的地、來源 SHA、目標 ref 與伺服器宣告的舊 SHA。伺服器接受更新時，
Git 再以該舊 SHA 比對 ref。Push 返回成功後，工具另執行一次 `git ls-remote`，
只有查到目標 ref 指向完全相同的工作 SHA 才會回報 `verified`。

| `result` | 意義 | 退出碼 |
| --- | --- | --- |
| `not_executed` | 前置驗證未通過，未呼叫 Git Push | 1 |
| `push_rejected` | Git 或 Push guard 明確拒絕更新 | 1 |
| `verification_failed` | Push 可能已生效，遠端結果未獲確認 | 1 |
| `already_present` | 即時查詢已看到指定 SHA，省略重複 Push | 0 |
| `verified` | Push 成功，且獨立查詢確認指定 SHA | 0 |

參數錯誤的退出碼為 2。遇到連線中斷、逾時或驗證失敗時，可以用相同參數重試。
如果遠端已指向指定 SHA，就會回報 `already_present`。如果遠端改為其他 SHA，
必須先查明並審閱新狀態，才能再次授權。確認只反映查詢當下，遠端之後仍可能更新。
以 SHA 比對也無法察覺 ref 在兩次觀察之間變更後又回到同一 SHA。請使用信任的 Git
伺服器與認證設定，並讓目標分支使用一般直接 ref。工具會拒絕查詢中顯示的符號 ref，
Git 協定所能揭露的 ref 資訊取決於伺服器。此步驟的範圍是工作 Commit，
`change_published` 事件由 `--event-commit` 和 `--push-event` 處理。

## 步驟 3-8C-1：PUB 工作 Commit

先執行下方的唯讀預檢，記下 `facts.branch` 和完整的 `facts.head`。AI 和人類應審閱
HEAD 到 index 的 diff、index 到工作樹的 diff，以及選取的 untracked 檔案內容。
若有既有 Commit，也要檢視實際提交歷史和 diff。確認貢獻範圍、秘密或私人資料、
生成產物、介面與假設變更、工程品質，以及適用的測試與實際證據。工具只檢查 Git
狀態與提交範圍。取得使用者對檔案與訊息的明確授權後，才傳入 `--confirm-reviewed`。

```powershell
python -B groupctl.py pub --work-commit --person-id student01 --expected-branch people/student01/my-topic --expected-head <完整SHA> --file src/parser.py --file tests/test_parser.py --message "Implement nested parsing" --confirm-reviewed
```

將範例中的分支、SHA 和檔案替換為審閱值。以重複的 `--file` 指定 repo 根目錄相對的
單一檔案。Git 將路徑當作字面值，且工具核對整棵候選 tree 對父 Commit 的實際變更
清單。改名需選取來源及目的地。未選取的 staged 內容、同一路徑同時有 staged 和
工作樹改動、衝突、未完成的 Git 操作、index lock、隱藏 index 旗標都會阻擋。
群組設定及該成員的描述檔須存在於預期 HEAD，且 JSON 內容須與工作樹一致。
建立 Commit 的模式也拒絕作用中的提交或 ref transaction hooks、已設定的外部 filters、
split index、sparse checkout 與選取的 gitlink 變更。需要這些功能時請使用專案既有的
提交流程。若倉庫要求簽章，請依專案規範獨立確認簽章政策。

請從審閱到命令完成期間暫停編輯和其他 Git 操作。`--confirm-reviewed` 是操作人的
確認，工具不會保存先前審閱時的工作檔案快照。工具持有 Git index lock，使用隔離
index 建立候選 tree，核對 Commit 的 tree 和唯一父 SHA，再以預期舊 SHA 更新指定的
個人 ref。若低階 Git 操作繞過 index lock 並切換 HEAD，分支更新後的檢查會回報可觀察的
變化。分支更新與安裝 index 是兩個步驟。若出錯，請依 `phase`、`candidate_commit`、
`commit` 和 `git_status` 查核。可能留下未引用的 Git 物件，或留下已更新的個人分支與
原 index，工具不會自動回滾。

已經有審閱過的工作 Commit 時，可明確選取個人分支目前的完整 HEAD：

```powershell
python -B groupctl.py pub --use-existing --person-id student01 --expected-branch people/student01/my-topic --expected-head <完整SHA> --confirm-reviewed
```

這個模式以唯讀方式回報已有的非空白單一父 Commit，其 HEAD 必須尚未納入本機可見的
canonical ref。遠端狀態仍待確認。`created` 和 `existing` 的退出碼為 `0`，
`refused` 和 `partial` 為 `1`，參數錯誤為 `2`。本機 Commit 只是 PUB 的第一個
寫入階段，發布仍需要後續 Push 與共享事件。

## 步驟 3-8B：PUB 唯讀預檢

在 Repository 內執行 `python -B groupctl.py pub --preflight --person-id student01`
或 `.\groupctl.cmd pub --preflight --person-id student01`，並以已登錄的成員 ID 取代範例值。
JSON 的 `facts` 顯示本機 Git 和身分資料，`blockers` 列出阻擋原因，`review_required`
和 `unverified` 說明待審閱與尚未確認的證據。退出碼 `0` 表示本機檢查未發現阻擋項，
仍須審閱實際貢獻。`1` 表示預檢受阻或無法完成，`2` 表示參數錯誤。

staged、unstaged、untracked 與已提交的淨變更路徑供審閱。`comparison` 使用可取得的
本機 upstream，否則使用本機可見的 canonical ref，並指出比較來源與 SHA。
淨變更清單不包含已變更又還原的路徑，需另閱 `history_range` 中實際提交的 diff。
本機 ref 可能過時。操作期間請避免同時編輯或執行其他 Git 操作。

預檢停用外部 Git filters 及 fsmonitor，使用 filter 的路徑可能與平常的 Git 狀態判定不同。
子模組回報 superproject 可見的 gitlink 狀態，子模組工作樹需另行審閱。
此階段只蒐集本機證據，遠端狀態、權限、秘密與測試結果均待確認。
預檢不選擇提交範圍、不執行測試、不修改 Git 狀態或建立事件，也不執行完整 PUB。

## 步驟 3-7D-2D：STATUS 的 TEST 證據

在 Repository 執行 `python -B groupctl.py status`。完整原始事件仍列在
`Event:`，`TEST evidence` 逐筆呈現自動執行結果、scope、受測 SHA、分支與證據限制。

- `pass`、`fail`、`partial`、`not_available` 分別表示事件回報通過、失敗、
  部分完成、無法取得。STATUS 區分 runner 觀察到的案例失敗和事件回報的失敗，
  根因仍須調查。相同 SHA 和 scope 出現不同結果時，列出所有事件 ID 供查核。
- `Counts: observed` 顯示可核對的計數，包含實際的 0。`unknown` 和
  `unavailable` 代表沒有可用的計數證據。舊事件仍保留原始計數、命令與摘要，
  缺少執行證據時不將其報為已觀察。環境與診斷的詳細內容可查看原始事件。
- HEAD 與 canonical 摘要依完整 SHA、分支／scope 及事件記載的 Git 前後狀態
  核對結果。缺少或未通過狀態驗證的事件仍可逐筆查看，歸屬標為未確認。
  個人分支的 `current_branch` 結果即使 SHA 相同也不列入 canonical 摘要。
  canonical 參照仍是本機快照。
- 自動通過與完整 TEST 程序各自呈現。現有 runner 會記錄 AI 語意審查未執行。
  `human_checks_required=[]` 只代表沒有列出待查項目，評估狀態仍未知。
  已列出的項目顯示待補證據。若同一事件記錄了可歸屬的人工觀察，STATUS 可顯示
  選用 `execution.human_observations` 中的 `check`、`observed`、`evidence`
  文字。它只呈現觀察與佐證位置，需由人查核原始佐證。
- STATUS 只讀取本機事件，發布狀態一律顯示未確認。檔案存在、已追蹤、已提交或有
  upstream 都不足以證明發布。查證時可用既有 `sync --discover` 檢視成功 fetch
  後的遠端分支、tip SHA 與事件內容，並核對事件 ID 和完整 payload。

端到端測試在暫存 Git Repository 驗證 TEST 寫入事件後的 STATUS 輸出。

## 步驟 3-7D-2C：AI 代理的 TEST 工程指引

Repository 根目錄的 [AGENTS.md](AGENTS.md) 是 `TEST [scope]` 的操作指引。
請讓 Codex 或其他 AI 代理在收到 `TEST` 時先讀取此檔。若代理未自動載入 Repository 指引，
可直接輸入「先讀 AGENTS.md，再執行 TEST current」。
指引涵蓋受測 Commit、驗證探索、預期結果依據、失敗調查、人工驗證與事件狀態。
以下 `groupctl test` 提供目前乾淨 HEAD 的 unittest 與本機事件紀錄，工程判斷依指引完成。

## 步驟 3-7B：TEST 最小本機機械支援

```powershell
.\groupctl.cmd test --person-id student01
python -B groupctl.py test --person-id student01
```

`--person-id` 必填，必須存在於 `.group/members/<person_id>.json`；不從
Git user.name 或分支名稱推定身分。可從 Repository 子目錄執行。
第一版只驗證目前分支的乾淨 HEAD，不接受 scope、canonical 或變更 ID。
拒絕 detached／無 HEAD、staged、unstaged、非忽略 untracked、未完成 Git 操作，
以及可能隱藏變更的 assume-unchanged／skip-worktree index flags。

使用目前 Python 解譯器、`-B` 與固定內部 unittest runner，執行
`unittest.defaultTestLoader.discover('tests')`，以 verbosity 2 執行；上限 120 秒。
不接受任意 shell 命令。測試本身仍是會執行的專案 Python 程式，請使用可信任的測試。
執行前後檢查分支、完整 HEAD、Git 工作目錄狀態及未完成操作；請勿同時編輯或操作 Git。
這是前後快照檢查，不是隔離沙箱，無法證明期間改動後又還原的內容未影響執行；
忽略檔案與外部環境也不由 commit 完整描述。

每次建立新的 `.group/events/<event_id>.json`，沿用既有驗證器與獨占建立，
不覆寫既有事件。事件的 commit 是受測 HEAD，不是未來提交事件的 commit。
建立事件本身通常會新增 untracked 檔案，使工作目錄變 dirty；再次執行會安全拒絕，
請先由人檢閱並依既有工程程序處理事件，不要為重跑而覆寫或刪除紀錄。
輸出「本機事件已建立，尚未 Commit／Push」；不自動執行 Fetch、Add、Commit、
Push、Merge、Checkout、Reset 或 Clean，不能據此宣稱已上傳 GitHub。

只有 runner 已啟動並完成、退出碼為 0、`successful=true`、逐案例計數有效且總和
等於執行數、`failed=0`、至少一個實際成功案例，而且前後 Git 快照皆驗證成功、
分支與 HEAD 一致時，才記為 `pass`。
有效計數有失敗案例時記為 `fail`，即使退出碼或 `successful` 與之矛盾。
退出碼 1 與 `successful=false` 一致時也記為 `fail`，例如案例之外的 fixture 錯誤。
其餘互相矛盾的 runner 證據、只有 skip、逾時，或原本可判為 pass 但後續 Git 驗證
失效時，記為 `partial`。正常完成但零測試、缺少 tests、無法啟動或無有效計數時，
記為 `not_available`。
Git 驗證失效時保留觀察到的失敗與計數，`commit` 記錄開始時的 HEAD，
`execution.state_verified=false` 與摘要標示觀察結果的 commit 歸屬未確認。
STATUS 會個別顯示這類紀錄；事件讀取與 SYNC 沿用既有協定驗證，歷史事件可省略 `execution`。
同一測試方法的多個失敗 subtest 只計一個 failed；expected failure 計入 skipped，
一個案例同時有 skip 與失敗時以 failed 優先。class/module fixture 錯誤造成
無法完整對應案例計數時保留診斷並回報 unavailable，不偽造計數或 pass。
只有 pass 且事件成功建立回傳 0；失敗、證據不完整、前置拒絕或寫入失敗回傳 1；
參數錯誤回傳 2。失敗或驗證失效仍盡可能建立真實事件，不自動回復測試造成的變更。

`execution` 是 specs/03 §14 允許的**選用實作擴充**，未新增正式必填欄位。
包含遮蔽路徑的實際 argv、branch、是否啟動與完成、退出碼、runner 的 `successful`、
計數是否可用、有效時的執行數 `ran`、狀態驗證及合併輸出尾段 `output_tail`。
臨時結果檔執行後移除。

步驟 3-7D-2B 在 `execution` 加入以下執行證據。

- `environment` 由 runner 回報 Python 實作與版本、作業系統名稱與發行版本、框架名稱及
  可取得的獨立版本，以及內部 runner 程式碼的 SHA-256 識別值。標準函式庫的
  unittest 通常沒有獨立版本欄位，此時為 `null`，以 Python 版本提供重現線索。
- `counts` 以 `observed` 標示有效計數（包含實際零），以 `unknown` 標示已啟動但沒有
  有效計數，以 `unavailable` 標示未執行。後兩種狀態的數值為 `null`。正式整數欄位
  保留既有的 0 相容表示，應搭配 `counts_available=false` 解讀。
- `diagnostics` 記錄 unittest 回呼觀察到的 assertion failure、error、unexpected success，
  以及逸出 runner 的例外。包含可取得的案例或 subtest 識別碼、例外型別與訊息。
  依發生順序最多保留 50 筆，每個文字欄位保留遮蔽後前 2000 個 Unicode 字元。
  `total` 是診斷筆數，subtest 可占多筆。`entries_truncated`、`truncated_fields`
  與 `truncated` 標示省略情況。未取得 runner 回報時，環境及診斷使用 `null`。
- `output` 分別保留 stdout、stderr 去識別後最後 12000 個 Unicode 字元，
  並各自標示 `truncated`。`output_tail` 沿用 stdout 接 stderr 的順序，保留最後
  12000 字元，`combined_truncated` 標示是否截斷。事件摘要中的輸出部分保留合併後
  最後 2000 字元，由 `summary_truncated` 標示。逾時保留子程序已擷取的部分輸出。

這些資料用於重現與定位失敗。例外類型只描述 runner 觀察到的結果，無法確認是產品缺陷、
測試錯誤、依賴或環境問題，也無法完整還原本機環境。AI 仍須調查失敗根因。

步驟 3-7C-1：`commands` 保留實際內部 runner 的參數順序與旗標，
路徑運算元以 `<python-executable>`、`<group-test-runner>`、`<temporary-result-json>`
明確遮蔽，並非可直接貼上執行的 shell 命令。`execution.command_representation` 說明此表示法，
`path_roles` 解釋各代號；`cwd` 是 Repository 根目錄。`started=false` 表示沒有啟動證據，
缺少 tests 或啟動失敗時 `commands=[]`；逾時仍保留已啟動的命令。
原始私人 argv 會遮蔽。summary、輸出與診斷也會在截斷前遮蔽已知 Repository、工具、
Python、家目錄及暫存路徑，並遮蔽可辨識的其他絕對路徑。已知位置會儘量保留相對檔名。
任意輸出仍可能含有未辨識的路徑、帳號或秘密，事件發布前須檢閱內容。

選用 `reproduction` 提供從獨立 checkout 的受測 commit 根目錄執行
`python -B -m unittest discover -s tests -v` 的等價操作（必要時改用 python3）。
此指令使用相同 discovery 與 verbosity，不建立 TEST 事件；不是本次實際執行的命令，
也不包含內部 runner 的逐案例計數政策、120 秒期限及 Git 狀態檢查。
這兩個擴充依 specs/03 §14 添加，不改變必填欄位語意或老師的 specs。

此命令不完成整套 TEST：尚未支援其他 scope、自動探索 build/lint/UI、
失敗根因調查、AI 語意審查、人工檢查規劃及事件發布。
`human_checks_required=[]` 表示本機 helper 尚未提出人工項目，不代表不需要人工檢查。
`automated_result="pass"` 只代表本次已執行的自動檢查通過。AI 語意審查、
人工驗證與完整 TEST 程序的完成情況須另有證據。
功能驗證使用暫存 Git Repository；不要為驗證此功能在正式 Repository 建立測試事件。

## 步驟 3-6C：SYNC 安全更新

執行 `.\groupctl.cmd sync --update` 或 `python -B groupctl.py sync --update`。
與 `--discover` 互斥；兩者是完整 SYNC 的不同階段，更新不替代事件發現與 AI 審查。
只從 `.group/group.json` 的 canonical branch fetch 到 origin 遠端追蹤 ref，
不整合任何個人遠端分支，不修改 canonical 本機分支，不自動 Commit 或 Push。
請避免同時使用其他工具修改此 Repository。

操作前輸出分支、HEAD、index／worktree／untracked 清單及快取 canonical SHA；
fetch 後輸出精確來源 SHA 與目標 HEAD。僅接受乾淨的 `people/<person_id>/<topic>`
分支，拒絕 detached HEAD、canonical、其他分支及未完成的 Git 操作。
所有非忽略的未追蹤檔案都阻擋更新；不偷偷 stash、reset 或 clean。

直線歷史採 `merge --ff-only`；分歧歷史採 `merge --no-commit --no-ff`，
合併成功仍保持待提交並回傳 1，HEAD 不變，不能宣稱 canonical 已納入歷史。
請先檢閱 diff、測試與語意，再由人決定是否提交；本命令不代為提交。
衝突保留現場、列出檔案並回傳 1；進一步編輯前可用 `git merge --abort`
取消待提交 merge。已完成 fast-forward 後測試失敗不自動回退，保留結果供診斷。

更新後用 Git ancestry 驗證來源是否納入，輸出前後 SHA、incoming diff、
受影響檔案、提交摘要及合併後 diff，供 AI 比對個人變更、介面與假設。
機械檢查包含 `git diff --check` 與本專案的
`python -B -m unittest discover -s tests -v`（120 秒上限）。
沒有 `tests/test*.py`、零測試、測試失敗／逾時或待提交 merge 均回傳 1。
其他測試框架／建置目前須由 AI 或人另行執行；測試程式會在更新後的工作樹執行，
應只對可信任的 canonical 使用此操作。
回傳 0 表示已納入且快速驗證通過，或已納入而不需更新；
重複無更新不重跑測試，不宣稱目前測試證據仍有效。參數錯誤回傳 2。
輸出明確標示語意審查尚未執行；仍須判斷介面相容性、行為及測試充分性。
本機輸出不是已發布的 collaboration event；此階段不建立或發布事件。

## 步驟 3-6A：SYNC 安全發現

執行 `.\groupctl.cmd sync --discover`。這是完整 SYNC 的發現階段，
只 fetch origin 的分支到遠端追蹤 refs（含清除已刪除的遠端追蹤 refs），
不整合程式碼、不更新本機分支、index 或工作檔案。
fetch 不下載 tags、不遞迴子模組，使用明確 refspec，不使用設定中的額外映射。

執行前顯示分支、HEAD、已暫存、未暫存及未追蹤清單。
比較 canonical 與遠端分支的提交，從 canonical 及 people/* 分支的
Git blob 讀取並嚴格驗證事件；不需要 checkout，也不要求本機存在事件檔案。
報告來源分支、完整 tip SHA、事件完整 payload、HEAD 與 canonical ahead/behind。
payload 中的 commit、測試及整合宣告仍須人工／AI 查核，不能當成已證實的事實。

`.group/local/sync-discover.json` 是可刪除、可重建的本機已讀收據，
成功掃描後以原子替換保存；刪除快取會重新報告仍在遠端分支上的事件。
相同事件跨分支或重複執行不重複通知；歷史改寫、事件修改／刪除、
同 ID 不同內容及同 change_id/question_id 多筆同類宣告都會警告，
不選定勝出紀錄、不推定 blocker 已解決。
任何錯誤或風險回傳 1，且不推進收據；乾淨完成回傳 0，參數錯誤為 2。
沒有 origin、fetch 失敗、canonical 不存在、事件格式錯誤均明確報告。
請勿同時執行多個 discovery 或其他修改 Repository 的工具。

目前沒有 canonical 自動整合、衝突處理、語意審查或整合後測試，
也未完成老師要求的完整 SYNC。自動化測試僅使用暫存 Repository 與本機 bare remote。

AI-assisted collaboration through GitHub.

## 第一階段功能

目前提供唯讀的 `groupctl --help` 與 `groupctl config show`。
設定檔位置固定為**目前工作目錄**下的 `.group/group.json`，請在儲存庫根目錄執行。

依正式契約驗證四個必要欄位是否存在：`protocol_version`、`group_id`、
`canonical_branch`、`assignment_file`。額外欄位會保留並顯示；
本階段不額外限制欄位值、不檢查 assignment 檔案或分支是否存在。
輸出為格式化 JSON（Unicode 以 JSON 跳脫表示），不修改設定。
支援 UTF-8 與 Windows 常見的 UTF-8 BOM。

成功或顯示說明的結束碼為 `0`；找不到設定、JSON 或編碼錯誤、
讀取失敗、頂層不是物件、缺少必要欄位時，錯誤寫入 stderr 並回傳 `1`。
命令或參數錯誤回傳 `2`。

`SYNC`、`PUB`、`INT`、`REV`、`TEST`、`STATUS`、`FINAL`
是規格定義的人對 AI 指令，本階段尚未實作其程序，也未提供對應 CLI。
當時事件、同步、發布、整合、審查、測試結果發布、最終報告與可攜式 agent 指引仍待後續實作。

## Windows PowerShell 安裝與使用

安裝 Python 3.10 或以上版本，並將 Python 加入 PATH；工具與測試僅使用標準函式庫，
不需要 pip 套件。若系統只有 Microsoft Store 的 python 捷徑，請先完成 Python 安裝。

在 PowerShell 切換至本機儲存庫根目錄後執行：

```powershell
Set-Location 'C:\Users\user\group-collaboration'
python --version
.\groupctl.cmd --help
.\groupctl.cmd config show
$LASTEXITCODE
python -m unittest discover -s tests -v
```

若要在本次 PowerShell 工作階段直接輸入 `groupctl`，將儲存庫根目錄加入 PATH：

```powershell
$env:Path = (Get-Location).Path + [IO.Path]::PathSeparator + $env:Path
groupctl --help
groupctl config show
```

不需要修改 PowerShell execution policy。
也可以直接使用 Python（Windows、macOS、Linux 皆可）：

```powershell
python groupctl.py --help
python groupctl.py config show
python -m unittest discover -s tests -v
```

macOS / Linux 若只有 `python3`，將上述 `python` 換成 `python3`。
在其他目錄執行時，可使用腳本的絕對路徑；設定仍從目前工作目錄讀取。
測試使用暫存目錄，不會改寫儲存庫內的 `.group/`。
## JSON 事件 CLI（本地驗證與發布）

在 Repository 根目錄可驗證、建立、查詢、列出或安全發布事件 JSON：

```powershell
.\groupctl.cmd events --help
.\groupctl.cmd events validate C:\temp\event.json
.\groupctl.cmd events create C:\temp\event.json
.\groupctl.cmd events show E-20261113T000000Z-P01-ab12
.\groupctl.cmd events list
.\groupctl.cmd events publish C:\temp\event.json --person-id student01 --remote origin --expected-url <URL> --confirm-reviewed
```

`validate` 只驗證格式不寫檔；`create` 驗證後以獨占模式寫入 `.group/events/<event_id>.json`（已存在則拒絕覆寫）；
`show` 依 ID 讀取單一事件；`list` 依檔名順序列出所有已驗證事件（無事件時回傳 `[]`）；
`publish` 透過隔離 index 建立事件 Commit 並可選推送與遠端驗證。
正常執行輸出格式化 JSON 並回傳 `0`，驗證或操作失敗回傳 `1`，參數錯誤回傳 `2`。

### 事件格式與契約

依 `specs/03_event_format_contract.md` 第 2 節驗證共同欄位，支援 protocol `0.1`，`created_at` 須為含時區的 ISO 8601 時間戳。
事件 ID 須為可移植單一檔名元件（上限 180 字元，避開 Windows 保留裝置名稱）。

第 4–13 節支援的十種事件型別與必要欄位：

| event_type | 必要 payload 欄位 |
| --- | --- |
| change_published | change_id, branch, commit, summary, files_changed, interface_changes, tests_run, known_issues, proposed_for_integration |
| finding | summary, evidence |
| question | question_id, text, blocking |
| answer | question_id, text |
| review | change_id, verdict, summary, issues, tests_run |
| test_result | scope, commit, commands, automated_result, passed, failed, skipped, human_checks_required, summary |
| integration_result | change_id, target_branch, result, merge_commit, conflicts, conflict_resolution_summary, verification, summary |
| blocker | summary, blocking_change_id, requested_action |
| final_candidate | canonical_commit, candidate_report_path, tests_evidence, known_issues |
| final_result | canonical_commit, final_report_path, test_evidence, known_limitations, initiated_by |

`verification` 須包含 `build`、`tests`、`semantic_review`；`verdict`、`automated_result`、`result` 依規格列舉值驗證。
`answer.related_events` 建議關聯原 `question` 事件 ID。

以下為 `finding` 事件範例：

```json
{
  "protocol_version": "0.1",
  "event_id": "E-20261113T000000Z-P01-ab12",
  "event_type": "finding",
  "group_id": "G-01",
  "person_id": "P01",
  "created_at": "2026-11-13T00:00:00Z",
  "related_events": [],
  "summary": "Verified empty event listing on a fresh repository.",
  "evidence": ["Ran events list on a fresh repository and observed [] with exit code 0."]
}
```

完整測試套件皆使用 `TemporaryDirectory` 隔離執行：

```powershell
python -B -m unittest discover -s tests -v
```

## 步驟 3-5A：唯讀 Git 狀態檢查

需要 Git 與 Python 3.10 以上版本：

```powershell
.\groupctl.cmd git status
python -B groupctl.py git status
```

可在工作樹子目錄執行；從 Repository 根目錄讀取 .group/group.json，
若沒有設定檔則 canonical branch 預設為 main。既有設定檔有錯時會回報錯誤。

group_git.py 提供 inspect_status() 與 GitStatus，回傳目前分支（detached HEAD 為 None）、
完整 HEAD SHA、已暫存／未暫存／未追蹤檔案、origin 是否存在、upstream、
ahead／behind 與 canonical ref／commit。Canonical 優先使用
refs/remotes/origin/<canonical_branch>，其次使用 refs/heads/<canonical_branch>，
輸出明示來源。忽略檔案不列入 dirty；衝突會依 Git index/worktree 狀態列入清單。

所有遠端資訊只來自本機 refs，可能過時，不能當成 GitHub 即時狀態。
指令不執行 fetch、pull、merge、rebase、checkout、commit 或 push，
停用 Git optional locks 與 partial-clone lazy fetch。多次唯讀查詢並非原子快照，
請避免同時以其他工具修改 Repository。

缺少 origin 或未設定 upstream 會明確顯示，ahead／behind 不可用時不以零冒充。
找不到 canonical branch 會顯示可取得的狀態並回傳 1。
已設定但無法解析的 upstream、沒有 HEAD commit、非 Git 工作樹、
Git 不存在／失敗／逾時及無效設定也會回傳 1。
成功為 0；參數錯誤為 2。

這只是 Git 基礎檢查，尚未實作完整 STATUS 程序：不更新遠端 metadata、
不整理事件、身分、測試證據、blockers 或建議行動，也不實作 SYNC／PUB／INT／FINAL。
新增測試僅在暫存 Git Repository 建立 fixture，不連線 GitHub。

```powershell
python -B -m unittest discover -s tests -v
```

## 步驟 3-5B：群組 STATUS 本機分析層

執行 .\groupctl.cmd status 或 python -B groupctl.py status。
可從工作樹子目錄執行；群組設定必須存在於 Repository 根目錄的 .group/group.json。

輸出包含群組、canonical branch、Git 快照、成員、完整事件清單、
各類事件數量，以及利用 change_id、related_events（含間接關聯）
或完全相同 commit 字串整理的變更證據清單。
所有事件列出 ID、作者、時間與 payload，便於追查不同或矛盾的宣告。
未取得資料會顯示 unknown 或 not yet recorded。

成員必要欄位為 protocol_version、person_id、display_name、joined_at；
驗證非空字串、版本 0.1、檔名與 person_id 相符，以及含時區的日期時間。
事件沿用既有驗證器；資料錯誤或 Git 查詢失敗回傳 1 並附來源路徑或 Git 指令。
成員清單為空、canonical ref 不存在、缺漏的事件關聯、未登錄作者、
找不到發布紀錄的 change_id 或重複 change_id 會明確警告，部分資料報告仍回傳 0。
參數錯誤回傳 2。

這不是完整 STATUS 工程程序，不執行 fetch 或任何 Git 寫入操作。
遠端 refs 僅為本機快照，可能過時。事件清單包含本機未提交檔案，
不保證已發布到 GitHub。成員清單不能證明目前操作者身分。
review accept 不代表已整合；integration_result 只是事件宣告，
尚未以 Git ancestry 驗證是否進入 HEAD 或 canonical。
test_result 僅適用其記錄的 commit 與 scope，不延伸至關聯事件或其他 commit；
HEAD/canonical 測試對應使用完整字串相等，縮寫 SHA 不自動推定相同。
不以時間、事件數量或較晚事件推定 blocker 已解決，也不挑選單一紀錄掩蓋矛盾。

暫存 Repository 測試涵蓋空事件、各類工程證據、缺少整合證據、
非法成員／事件、缺失關聯、dirty、upstream、唯讀性及非 Git 目錄。
