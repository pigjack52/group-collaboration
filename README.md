# Group Collaboration System

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
