# Group Collaboration System

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
事件、同步、發布、整合、審查、測試結果發布、最終報告與可攜式 agent 指引均待後續實作。

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
## JSON ?? CLI??????

??? Repository ??????????????? JSON????????????

```powershell
.\groupctl.cmd events --help
.\groupctl.cmd events validate C:\temp\event.json
.\groupctl.cmd events create C:\temp\event.json
.\groupctl.cmd events show E-20261113T000000Z-P01-ab12
.\groupctl.cmd events list
```

`validate` ?????????`create` ????????????
`.group/events/<event_id>.json`??? ID ??????????????
`show` ? ID ??????`list` ????????????????????? `[]`?
??????? ID ??????????????????????
??????? JSON???? `0`????????? `1`?????? `2`?
??????????????????????????????????? Git ???

### ?????????

? `specs/03_event_format_contract.md` ?2 ???????????
??? protocol `0.1`?`created_at` ?????? ISO 8601 ???
ID ?????????????????????????????????
?? 180 ????? Windows ??????????????????? ID ???
`related_events` ? ID ???????????????????????? Git ?????

?4?11 ????????????? payload ????????????????
????????????????????? profile??????????????

| event_type | ?????? payload ?? |
| --- | --- |
| change_published | change_id, branch, commit, summary, files_changed, interface_changes, tests_run, known_issues, proposed_for_integration |
| finding | summary, evidence |
| question | question_id, text, blocking |
| answer | question_id, text |
| review | change_id, verdict, summary, issues, tests_run |
| test_result | scope, commit, commands, automated_result, passed, failed, skipped, human_checks_required, summary |
| integration_result | change_id, target_branch, result, merge_commit, conflicts, conflict_resolution_summary, verification, summary |
| blocker | summary, blocking_change_id, requested_action |

???????????????????????????????????
???????????????????`verification` ?? `build`?`tests`?
`semantic_review` ??????????????????????
`verdict`?`automated_result`?`result` ?????????????
`answer.related_events` ???? question ?? ID?????? should????????

?12?13 ?????????? JSON ????????????????
??????????????????????????

| event_type | ????? |
| --- | --- |
| final_candidate | canonical_commit?????candidate_report_path?????tests_evidence???????known_issues?????? |
| final_result | canonical_commit?????final_report_path?????test_evidence???????known_limitations???????initiated_by???? |

????????summary/text ?????????evidence???????????
commit ? related_events ?????????????????????????
????????????????????????????

?? finding ???? validate/create ??????????????? Repository ???
??????????????

```json
{
  "protocol_version": "0.1",
  "event_id": "E-20261113T000000Z-P01-ab12",
  "event_type": "finding",
  "group_id": "G-01",
  "person_id": "P01",
  "created_at": "2026-11-13T00:00:00Z",
  "related_events": [],
  "summary": "???????????????? Repository ???",
  "evidence": ["??? Repository ?? events list??? [] ????? 0?"]
}
```

???????? unittest ???????????? TemporaryDirectory?

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
