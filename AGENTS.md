# AI 代理的 TEST 工程指引

學生輸入 `TEST [scope]` 時，依本指引執行。Codex 或其他代理都可以從 Repository 根目錄讀取此檔。以 [指令定義](specs/04_command_reference.md)、[TEST 程序](specs/05_command_procedures.md#5-test)、[AI 工程責任](specs/06_agent_behavior_specification.md)及[驗證規格](specs/07_testing_and_finalization.md)為準。

## 1. 確認 scope 與受測 Commit

- `TEST` 或 `TEST current` 指目前分支。`TEST main` 指 `.group/group.json` 設定的 canonical branch。`TEST C-17` 等 change ID 指對應 `change_published` 事件所載的 Commit。未知或有歧義的 scope 須向使用者確認。
- 記錄要求的 scope、來源 ref 或事件 ID、分支與完整 Commit SHA。以 `git rev-parse --verify HEAD`、`git symbolic-ref --short HEAD`、`git status --short` 核對目前工作樹。驗證前後確認分支、HEAD 與工作樹狀態，避免同時編輯。若測到含未提交修改的工作樹，明列修改並將結果標為工作樹驗證，禁止歸為該 HEAD 的證據。
- canonical scope 先唯讀檢視已知 refs 並記下來源。本機遠端追蹤 ref 可能過時，無法確認新鮮度時標為未知。change ID scope 先核對發布事件、分支與 Commit 是否可取得，並閱讀實際 diff。遇到重複或矛盾事件請人確認目標。要執行這兩種 scope，請人提供已準備的獨立工作目錄，驗證其 HEAD 與工作樹後再執行適用的專案檢查。缺少目標、可信工作目錄或授權時，列出未執行項目及原因。禁止自動 checkout、merge、reset、stash 或 clean 來取得受測狀態。
- 現有 `groupctl test` 只跑目前分支的乾淨 HEAD，事件 scope 固定為 `current_branch`，canonical 與 change ID scope 尚未實作。禁止以個人分支的結果直接充當 main 的證據。canonical 驗證須針對已核對的 canonical Commit，並保留驗證來源。

## 2. 探索驗證並核對預期行為

- 從 `.group/group.json` 所指的 assignment、需求、程式與實際 diff 確認要驗證的行為、介面和假設。查閱 README、建置設定、CI（若有）、測試目錄與 fixtures，逐項判斷 repository integrity、build/compile、unit、integration、static analysis、lint、scripted UI 是否適用。對 UI 優先找可自動檢查的互動、DOM／widget、無障礙介面或畫面差異。
- 檢查 test data 的來源、邊界條件和前置狀態。expected outputs 應可獨立由規格、已確認的範例、手算、可信參考資料或已審查的版本化基準推導。無法逐字比對時，檢查有需求依據的不變條件。缺少獨立依據時，標明對應需求仍未知，包括 AI 產生的測試。禁止偷偷修改或重生 expected outputs 使測試轉綠。確有修正理由時，記錄需求依據與差異，依正常程式變更流程審查。

## 3. 執行適用的自動驗證

- **工具的機械工作。** `groupctl test --person-id <已登錄的 person_id>` 可檢查身分和 Git 前置狀態，以固定 unittest runner 驗證目前分支的乾淨 HEAD，收集計數和診斷、重新檢查 Git 狀態，並嘗試建立本機 `test_result`。它會拒絕 detached 或無 HEAD、dirty、未完成 Git 操作或隱藏變更的 index flags。只有允許建立本機事件時才執行。只需驗證此專案的 unittest 或禁止建立事件時，可在 Repository 根目錄執行 `python -B -m unittest discover -s tests -v`，必要時用 `python3`。這條指令不建立事件，Git 狀態由代理另行核對。
- **AI 的工程判斷。** 依需求與覆蓋範圍挑選其他專案命令，執行可信的驗證，記錄各項命令、環境與依賴、退出碼、計數、輸出來源和覆蓋的行為。檢視 diff、介面與相互依賴的假設，回報實際完成的語意審查及其依據。退出碼 0 只能支持該命令的觀察，禁止當成需求已完全驗證的證明。
- **人的確認與操作。** 由人確認尚有歧義的需求或 scope、執行需要實機或主觀判斷的檢查，並決定是否發布。AI 根據實際觀察整理證據，禁止捏造 AI 審查或人工檢查結果。

## 4. 調查失敗與不完整結果

先取得測試 ID、輸入、expected／actual、例外及有關的環境與 Git 狀態。檢查 `execution` 的 `state_verified`、`counts`、`diagnostics`、輸出及截斷旗標（若有）。保留原始失敗，在同一 SHA 和已記錄環境重跑最小相關案例，必要時對照需求、fixture、程式路徑及已知基準。若證據不足，說明還缺什麼。`AssertionError` 只證明斷言失敗。

| 分類 | 可支持分類的依據 |
| --- | --- |
| 產品程式問題 | 依獨立需求推導的預期與實際行為不同，且可信環境下可重現並定位相關程式路徑。 |
| 測試過時或錯誤 | 規格與獨立範例支持目前行為，追查發現 fixture、斷言或預期值仍使用舊契約。 |
| 環境或依賴問題 | 紀錄顯示缺少特定工具、服務或版本，或同一 Commit 在要求的環境可通過。 |
| 原因尚無法確定 | 證據互相矛盾、診斷被截斷、逾時、無法重現或缺乏獨立預期。列出待補證據。 |

測試狀態與根因分開回報。即使根因未知，已觀察到的失敗仍須保留。未啟動、未完成、零案例、跳過案例和不可取得的計數都需要單獨說明。`counts_available=false` 時，正式計數欄的相容值 0 不代表觀察到零失敗。若 Git 狀態驗證失效，標記結果歸屬未確認，避免將其當成指定 Commit 的成功證據。

## 5. 設計人工驗證

每項人工驗證都應寫出目的與對應需求、受測 SHA、環境與測試資料等前置條件、可重現的編號操作、預期觀察、實際結果的回報方式。要求回報實際看到的行為及相關截圖、日誌或重現資訊。未完成時標為「未執行，結果未知」，並說明缺少的條件。

例如某專案要求折疊清單後保留選取。目的為確認操作後仍看得出選取位置。前置條件為指定 SHA 的 UI、載入包含節點 Y 與子項 Z 的標準範例。操作為 (1) 展開 Y，(2) 選取 Z，(3) 折疊 Y，(4) 重新展開 Y。預期為 Z 仍被選取且高亮清楚。請人回報實際選取項、是否看得出高亮、環境與必要截圖。尚未收到觀察時回報「未執行，結果未知」。

## 6. 彙報證據、建立與發布狀態

- 以 scope、分支、完整 SHA 和環境作為報告開頭，逐項列出**已證明**的行為與範圍、**失敗**的檢查及根因依據、**未知**的結論與待補證據、**未執行**的檢查及原因。可以同時有失敗與未知根因，或人工檢查尚未執行而結果未知。禁止把未執行的驗證報為通過。
- 依 [事件契約](specs/03_event_format_contract.md#9-test_result)記錄實際 scope、受測 Commit、已執行命令、自動結果與計數、環境問題、待人工驗證與摘要。`groupctl test` 的 `automated_result` 只涵蓋其 unittest 執行。`semantic_review=not_performed` 和 `human_checks_required=[]` 是工具的初始紀錄，AI 應另外交代已做的審查與仍需的人工驗證。事件內容須檢閱是否有秘密、私人路徑或帳號。禁止為重跑覆寫既有事件，已發布事件的補充應另建事件並關聯原 ID。
- 回報事件 ID、路徑及實際狀態。未寫檔或寫入失敗為「未建立」，CLI 寫檔成功為「本機已建立，未發布」。依 [Repository 契約](specs/02_repository_contract.md#9-event-publication)，事件經 add、commit、push 並確認能在共享遠端查到後才回報「已發布」。禁止將本機或僅已提交的事件宣稱已發布。沒有發布授權時停在真實狀態，交由人檢閱決定，禁止自動 Commit、Push、Merge 或發布。若工作限制禁止建立事件，僅回報證據並標示「未建立」。
