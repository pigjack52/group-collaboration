import { SequenceDiagramData } from '../types/sequenceDiagram';

const STORAGE_KEY_PREFIX = 'sequence-diagram-editor:v1:';

export interface StoredDiagramPayload {
  diagramId: string;
  savedAt: string;
  diagram: SequenceDiagramData;
}

function getStorageKey(diagramId: string): string {
  return `${STORAGE_KEY_PREFIX}${diagramId}`;
}

function isValidDiagramData(value: unknown): value is SequenceDiagramData {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<SequenceDiagramData>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    Array.isArray(candidate.lifelines) &&
    Array.isArray(candidate.messages) &&
    Array.isArray(candidate.fragments)
  );
}

/**
 * 將完整的 SequenceDiagramData（含 Lifeline 順序與 x、Message y、Fragment 幾何與 interactions）
 * 依 diagramId 儲存至 localStorage。
 */
export function saveDiagramToStorage(
  diagram: SequenceDiagramData,
): StoredDiagramPayload {
  const payload: StoredDiagramPayload = {
    diagramId: diagram.id,
    savedAt: new Date().toISOString(),
    diagram,
  };

  window.localStorage.setItem(
    getStorageKey(diagram.id),
    JSON.stringify(payload),
  );

  return payload;
}

/**
 * 依 diagramId 從 localStorage 讀取已儲存的版面與編輯資料。
 * 若無資料或格式無效則回傳 null。
 */
export function loadDiagramFromStorage(
  diagramId: string,
): StoredDiagramPayload | null {
  try {
    const raw = window.localStorage.getItem(getStorageKey(diagramId));
    if (!raw) return null;

    const parsed = JSON.parse(raw) as Partial<StoredDiagramPayload>;
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      parsed.diagramId !== diagramId ||
      typeof parsed.savedAt !== 'string' ||
      !isValidDiagramData(parsed.diagram)
    ) {
      return null;
    }

    return {
      diagramId: parsed.diagramId,
      savedAt: parsed.savedAt,
      diagram: parsed.diagram,
    };
  } catch {
    return null;
  }
}

/**
 * 清除指定 diagramId 在 localStorage 中的儲存資料。
 */
export function clearDiagramFromStorage(diagramId: string): void {
  window.localStorage.removeItem(getStorageKey(diagramId));
}
