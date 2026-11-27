import { useCallback, useState } from 'react';
import { FragmentInteractionsModal } from './components/FragmentInteractionsModal';
import { SequenceDiagramSvg } from './components/SequenceDiagramSvg';
import { mockSequenceDiagram } from './data/mockDiagram';
import {
  autoLayoutLifelines,
  evaluateLayoutCost,
  LifelineLayoutResult,
} from './layout/lifelineLayout';
import {
  clearDiagramFromStorage,
  loadDiagramFromStorage,
  saveDiagramToStorage,
} from './storage/diagramStorage';
import { Fragment, SequenceDiagramData } from './types/sequenceDiagram';
import './App.css';

const MESSAGE_TOP_BOUND = 102;
const MESSAGE_BOTTOM_BOUND = 516;
const MIN_MESSAGE_GAP = 28;

/**
 * 依據既有事件先後順序與所屬 Combined Fragment 邊界限制 Message 的垂直座標 y，
 * 避免拖曳時跨越相鄰訊息順序或脫離所屬 Fragment 範圍。
 */
function clampMessageY(
  diagram: SequenceDiagramData,
  messageId: string,
  rawY: number,
): number {
  const orderedMessages = [...diagram.messages].sort(
    (a, b) => (a.order ?? a.y) - (b.order ?? b.y),
  );
  const index = orderedMessages.findIndex((m) => m.id === messageId);
  if (index === -1) return rawY;

  const current = orderedMessages[index];
  const prev = index > 0 ? orderedMessages[index - 1] : null;
  const next =
    index < orderedMessages.length - 1 ? orderedMessages[index + 1] : null;

  let minY = prev ? prev.y + MIN_MESSAGE_GAP : MESSAGE_TOP_BOUND;
  let maxY = next ? next.y - MIN_MESSAGE_GAP : MESSAGE_BOTTOM_BOUND;

  const parentFragment = diagram.fragments.find(
    (f) =>
      f.id === current.fragmentId || f.interactions?.includes(current.id),
  );
  if (parentFragment) {
    minY = Math.max(minY, parentFragment.y + 34);
    maxY = Math.min(maxY, parentFragment.y + parentFragment.height - 14);
  }

  if (minY > maxY) {
    return current.y;
  }

  return Math.round(Math.max(minY, Math.min(maxY, rawY)));
}

interface InitialAppState {
  layoutResult: LifelineLayoutResult;
  lastSavedAt: string | null;
  hasUnsavedChanges: boolean;
}

/**
 * 載入規則：
 * 若 localStorage 已有該 diagramId 的儲存資料，優先還原，不執行自動排列演算法；
 * 僅在尚無儲存資料（首次開啟）時執行初始自動排列演算法。
 */
function createInitialState(): InitialAppState {
  const stored = loadDiagramFromStorage(mockSequenceDiagram.id);
  if (stored) {
    const order = stored.diagram.lifelines.map((l) => l.id);
    const cost = evaluateLayoutCost(order, stored.diagram);
    return {
      layoutResult: {
        diagram: stored.diagram,
        order,
        cost,
      },
      lastSavedAt: stored.savedAt,
      hasUnsavedChanges: false,
    };
  }

  return {
    layoutResult: autoLayoutLifelines(mockSequenceDiagram),
    lastSavedAt: null,
    hasUnsavedChanges: false,
  };
}

function formatSavedTime(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return isoString;
  return date.toLocaleTimeString('zh-TW', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function App() {
  const [initialState] = useState<InitialAppState>(() => createInitialState());
  const [layoutResult, setLayoutResult] = useState<LifelineLayoutResult>(
    initialState.layoutResult,
  );
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(
    initialState.lastSavedAt,
  );
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState<boolean>(
    initialState.hasUnsavedChanges,
  );
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [editingFragmentId, setEditingFragmentId] = useState<string | null>(
    null,
  );

  const handleAutoLayout = useCallback(() => {
    setLayoutResult((prev) => autoLayoutLifelines(prev.diagram));
    setHasUnsavedChanges(true);
    setSaveNotice(null);
  }, []);

  const handleLifelineMove = useCallback((lifelineId: string, nextX: number) => {
    setLayoutResult((prev) => {
      const updatedLifelines = prev.diagram.lifelines
        .map((lifeline) =>
          lifeline.id === lifelineId ? { ...lifeline, x: nextX } : lifeline,
        )
        .sort((a, b) => a.x - b.x);

      const nextOrder = updatedLifelines.map((l) => l.id);
      const nextDiagram = {
        ...prev.diagram,
        lifelines: updatedLifelines,
      };
      const nextCost = evaluateLayoutCost(nextOrder, nextDiagram);

      return {
        diagram: nextDiagram,
        order: nextOrder,
        cost: nextCost,
      };
    });
    setHasUnsavedChanges(true);
    setSaveNotice(null);
  }, []);

  const handleMessageMove = useCallback((messageId: string, rawY: number) => {
    setLayoutResult((prev) => {
      const clampedY = clampMessageY(prev.diagram, messageId, rawY);
      const updatedMessages = prev.diagram.messages.map((message) =>
        message.id === messageId ? { ...message, y: clampedY } : message,
      );

      return {
        ...prev,
        diagram: {
          ...prev.diagram,
          messages: updatedMessages,
        },
      };
    });
    setHasUnsavedChanges(true);
    setSaveNotice(null);
  }, []);

  const handleFragmentCreate = useCallback((fragment: Fragment) => {
    setLayoutResult((prev) => ({
      ...prev,
      diagram: {
        ...prev.diagram,
        fragments: [...prev.diagram.fragments, fragment],
      },
    }));
    setHasUnsavedChanges(true);
    setSaveNotice(null);
  }, []);

  const handleFragmentUpdate = useCallback(
    (
      fragmentId: string,
      bounds: Pick<Fragment, 'x' | 'y' | 'width' | 'height'>,
    ) => {
      setLayoutResult((prev) => ({
        ...prev,
        diagram: {
          ...prev.diagram,
          fragments: prev.diagram.fragments.map((fragment) =>
            fragment.id === fragmentId ? { ...fragment, ...bounds } : fragment,
          ),
        },
      }));
      setHasUnsavedChanges(true);
      setSaveNotice(null);
    },
    [],
  );

  const handleSaveFragmentInteractions = useCallback(
    (fragmentId: string, interactions: string[]) => {
      setLayoutResult((prev) => ({
        ...prev,
        diagram: {
          ...prev.diagram,
          fragments: prev.diagram.fragments.map((fragment) =>
            fragment.id === fragmentId
              ? { ...fragment, interactions }
              : fragment,
          ),
        },
      }));
      setEditingFragmentId(null);
      setHasUnsavedChanges(true);
      setSaveNotice(null);
    },
    [],
  );

  const handleSaveLayout = useCallback(() => {
    const saved = saveDiagramToStorage(layoutResult.diagram);
    setLastSavedAt(saved.savedAt);
    setHasUnsavedChanges(false);
    setSaveNotice(`已儲存於 ${formatSavedTime(saved.savedAt)}`);
  }, [layoutResult.diagram]);

  const handleResetSaved = useCallback(() => {
    clearDiagramFromStorage(layoutResult.diagram.id);
    setLayoutResult(autoLayoutLifelines(mockSequenceDiagram));
    setLastSavedAt(null);
    setHasUnsavedChanges(false);
    setEditingFragmentId(null);
    setSaveNotice('已清除儲存資料並重設為預設版面');
  }, [layoutResult.diagram.id]);

  const { diagram, cost } = layoutResult;
  const editingFragment = editingFragmentId
    ? diagram.fragments.find((f) => f.id === editingFragmentId) ?? null
    : null;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <span className="phase-badge">Sequence Diagram Layout Editor</span>
          <h1>{diagram.title}</h1>
        </div>
        <div className="header-actions">
          <div className="diagram-stats">
            <span>{diagram.lifelines.length} Lifelines</span>
            <span>{diagram.messages.length} Messages</span>
            <span>{diagram.fragments.length} Fragments</span>
            <span>Cost: {cost.totalCost}</span>
          </div>
          <div className="toolbar-buttons">
            <button
              type="button"
              className="auto-layout-btn"
              onClick={handleAutoLayout}
            >
              重新自動排列（Auto Layout）
            </button>
            <button
              type="button"
              className="save-layout-btn"
              onClick={handleSaveLayout}
            >
              儲存版面（Save）
            </button>
            <button
              type="button"
              className="reset-layout-btn"
              onClick={handleResetSaved}
            >
              清除儲存／重設為預設（Reset / Clear Saved）
            </button>
          </div>
        </div>
      </header>

      <div className="storage-status-bar">
        <span className="storage-status-text">
          {saveNotice
            ? saveNotice
            : lastSavedAt
              ? `已載入儲存版面（最後儲存：${formatSavedTime(lastSavedAt)}）${hasUnsavedChanges ? ' · 有尚未儲存的變更' : ''}`
              : `目前使用預設版面（尚未儲存至瀏覽器）${hasUnsavedChanges ? ' · 有尚未儲存的變更' : ''}`}
        </span>
      </div>

      <main className="canvas-container">
        <SequenceDiagramSvg
          diagram={diagram}
          onLifelineMove={handleLifelineMove}
          onMessageMove={handleMessageMove}
          onFragmentCreate={handleFragmentCreate}
          onFragmentUpdate={handleFragmentUpdate}
          onFragmentEdit={(fragment) => setEditingFragmentId(fragment.id)}
        />
      </main>

      {editingFragment && (
        <FragmentInteractionsModal
          fragment={editingFragment}
          messages={diagram.messages}
          onSave={handleSaveFragmentInteractions}
          onClose={() => setEditingFragmentId(null)}
        />
      )}

      <footer className="layout-summary">
        <span>
          排列順序：{diagram.lifelines.map((l) => l.name).join(' → ')}
        </span>
        <span>
          FragmentWidth: {cost.fragmentWidth} · MessageDistance: {cost.messageDistance} · UnnecessaryCoverage: {cost.unnecessaryLifelineCoverage}
        </span>
      </footer>
    </div>
  );
}

export default App;
